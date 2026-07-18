"""Failure-safe handoff of an all-in-one installation to its standalone agent.

The game-server files and rootless Docker runtime stay on the same machine.  A
short-lived challenge file proves that the replacement agent sees the exact
same server roots before the database assignment is changed.  No credentials
or challenge values are logged or persisted in the database.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from config import settings
from games.base import container_name_for
from models import Node, Server
from services.node_client import NodeClient, NodeClientError


class LocalNodeHandoffError(RuntimeError):
    """Raised when the local node cannot be handed off without data risk."""


_TRANSIENT_SERVER_STATES = {"installing", "updating", "restoring", "deleting"}


def _server_root(server: Server) -> Path:
    base = Path(settings.servers_dir).resolve()
    candidate = Path(server.install_dir)
    if not candidate.is_absolute():
        candidate = base / candidate
    if candidate.is_symlink():
        raise LocalNodeHandoffError(
            f"Server {server.id}: symbolische Server-Roots werden nicht automatisch uebergeben"
        )
    try:
        root = candidate.resolve(strict=True)
        root.relative_to(base)
    except (FileNotFoundError, NotADirectoryError, ValueError, OSError) as exc:
        raise LocalNodeHandoffError(
            f"Server {server.id}: lokales Datenverzeichnis fehlt oder ist unsicher"
        ) from exc
    if not root.is_dir():
        raise LocalNodeHandoffError(
            f"Server {server.id}: lokaler Server-Root ist kein Verzeichnis"
        )
    return root


def _prepare_agent_server_roots(servers: list[Server]) -> list[tuple[Path, Path]]:
    """Normalize legacy local roots to the agent's numeric root contract.

    Local all-in-one installs historically used ``<game_type>_<id>`` while a
    standalone agent resolves every server as ``<servers_dir>/<id>``. Renames
    stay on the same filesystem and are rolled back if verification or the DB
    cutover fails.
    """

    base = Path(settings.servers_dir).resolve()
    renamed: list[tuple[Path, Path]] = []
    try:
        for server in servers:
            source = _server_root(server)
            destination = base / str(server.id)
            if source == destination:
                continue
            if destination.exists() or destination.is_symlink():
                raise LocalNodeHandoffError(
                    f"Server {server.id}: numerisches Zielverzeichnis ist bereits belegt"
                )
            source.rename(destination)
            renamed.append((source, destination))
            server.install_dir = str(destination)
        return renamed
    except Exception:
        if not _rollback_agent_server_roots(servers, renamed):
            raise LocalNodeHandoffError(
                "Vorbereitung abgebrochen; ein Serververzeichnis konnte nicht "
                "zurückbenannt werden und muss manuell geprüft werden"
            )
        raise


def _rollback_agent_server_roots(
    servers: list[Server],
    renamed: list[tuple[Path, Path]],
) -> bool:
    original_by_destination = {destination: source for source, destination in renamed}
    server_by_destination = {
        Path(server.install_dir): server
        for server in servers
        if Path(server.install_dir) in original_by_destination
    }
    complete = True
    for source, destination in reversed(renamed):
        try:
            if source.exists() and not destination.exists():
                continue
            destination.rename(source)
            server = server_by_destination.get(destination)
            if server is not None:
                server.install_dir = str(source)
        except OSError:
            complete = False
    return complete


def _require_root_rollback(
    servers: list[Server],
    renamed: list[tuple[Path, Path]],
) -> None:
    if not _rollback_agent_server_roots(servers, renamed):
        raise LocalNodeHandoffError(
            "Handoff abgebrochen; mindestens ein Serververzeichnis konnte nicht "
            "zurückbenannt werden und muss vor einem Neustart manuell geprüft werden"
        )


def _write_challenge(root: Path) -> tuple[Path, str]:
    value = secrets.token_urlsafe(48)
    marker = root / f".msm-handoff-{secrets.token_hex(12)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(marker, flags, 0o600)
        try:
            os.write(descriptor, value.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except Exception:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return marker, value


def _verify_shared_server_roots(
    client: NodeClient,
    servers: list[Server],
) -> None:
    markers: list[Path] = []
    try:
        for server in servers:
            root = _server_root(server)
            marker, expected = _write_challenge(root)
            markers.append(marker)
            observed = client.files_read(server.id, marker.name)
            if not secrets.compare_digest(observed, expected):
                raise LocalNodeHandoffError(
                    f"Server {server.id}: der Ersatz-Agent sieht nicht dasselbe Datenverzeichnis"
                )
    except LocalNodeHandoffError:
        raise
    except (NodeClientError, OSError) as exc:
        raise LocalNodeHandoffError(
            "Der Ersatz-Agent konnte die gemeinsamen Server-Daten nicht sicher bestaetigen"
        ) from exc
    finally:
        cleanup_failed = False
        for marker in markers:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            # The marker contains only a random one-time challenge and no
            # operator data. A cleanup failure must still block cutover.
            raise LocalNodeHandoffError(
                "Eine temporaere Handoff-Pruefdatei konnte nicht entfernt werden"
            )


def _verify_runtime(client: NodeClient, servers: list[Server]) -> None:
    try:
        health = client.health()
        if health.get("status") != "ok" or health.get("docker_connected") is not True:
            raise LocalNodeHandoffError("Der Ersatz-Agent hat keinen nutzbaren Docker-Zugriff")
        containers = client.list_containers()
    except LocalNodeHandoffError:
        raise
    except NodeClientError as exc:
        raise LocalNodeHandoffError(
            "Der Ersatz-Agent ist nicht erreichbar oder nicht authentifiziert"
        ) from exc

    visible_names = {
        str(item.get("name") or "")
        for item in containers
        if isinstance(item, dict)
    }
    for server in servers:
        if server.status == "running" or server.container_name:
            expected = container_name_for(server.id)
            if expected not in visible_names:
                raise LocalNodeHandoffError(
                    f"Server {server.id}: erwarteter Container {expected} fehlt beim Ersatz-Agenten"
                )


def handoff_local_node(
    db: Session,
    *,
    replacement_node_id: int,
) -> dict[str, Any]:
    """Replace the local-node record after proving shared storage/runtime.

    Legacy local directory names are normalized atomically to the standalone
    agent contract. Containers and file contents remain untouched. The database
    reassignment is committed only after storage and runtime verification.
    """

    local_nodes = db.query(Node).filter(Node.is_local.is_(True)).all()
    if len(local_nodes) != 1:
        raise LocalNodeHandoffError(
            "Es muss genau ein lokaler Node registriert sein"
        )
    local_node = local_nodes[0]
    replacement = db.query(Node).filter(Node.id == replacement_node_id).first()
    if replacement is None:
        raise LocalNodeHandoffError("Der Ersatz-Node wurde nicht gefunden")
    if replacement.id == local_node.id or replacement.is_local:
        raise LocalNodeHandoffError("Der Ersatz-Node muss ein eigenstaendiger Remote-Node sein")

    servers = (
        db.query(Server)
        .filter(Server.node_id == local_node.id)
        .order_by(Server.id.asc())
        .all()
    )
    transient = [server.id for server in servers if server.status in _TRANSIENT_SERVER_STATES]
    if transient:
        joined = ", ".join(str(server_id) for server_id in transient)
        raise LocalNodeHandoffError(
            f"Laufende Installations-/Updatevorgaenge zuerst abschliessen: {joined}"
        )

    renamed_roots: list[tuple[Path, Path]] = []
    locks = []
    try:
        from services.server_lifecycle_service import get_server_lifecycle_lock

        for server in servers:
            lock = get_server_lifecycle_lock(server.id)
            if not lock.acquire(blocking=False):
                raise LocalNodeHandoffError(
                    f"Server {server.id}: eine andere Server-Operation ist noch aktiv"
                )
            locks.append(lock)

        renamed_roots = _prepare_agent_server_roots(servers)
        client = NodeClient.from_node(replacement)
        _verify_runtime(client, servers)
        _verify_shared_server_roots(client, servers)
    except LocalNodeHandoffError:
        db.rollback()
        _require_root_rollback(servers, renamed_roots)
        for lock in reversed(locks):
            lock.release()
        raise
    except Exception as exc:
        db.rollback()
        _require_root_rollback(servers, renamed_roots)
        for lock in reversed(locks):
            lock.release()
        raise LocalNodeHandoffError(
            "Der Ersatz-Node konnte nicht sicher verifiziert werden"
        ) from exc

    try:
        for server in servers:
            server.node = replacement
        db.flush()
        db.delete(local_node)
        db.commit()
    except Exception as exc:
        db.rollback()
        _require_root_rollback(servers, renamed_roots)
        raise LocalNodeHandoffError(
            "Die Node-Zuordnung konnte nicht atomar umgestellt werden"
        ) from exc
    finally:
        for lock in reversed(locks):
            lock.release()

    return {
        "ok": True,
        "replacement_node_id": replacement.id,
        "server_ids": [server.id for server in servers],
        "data_moved": bool(renamed_roots),
        "source_data_retained": True,
    }
