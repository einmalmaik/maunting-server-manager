"""Failure-safe transfer of one stopped game server to another MSM node.

The source data is never deleted. The database assignment changes only after
the destination file restore and optional PostgreSQL restore succeeded.
"""

from __future__ import annotations

import logging
import os
import tempfile
import ipaddress
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from config import settings
from models import Node, Server
from services.node_client import NodeClient

logger = logging.getLogger(__name__)


class ServerNodeMigrationError(RuntimeError):
    """A safe server-to-node migration could not be completed."""


def _ports(server: Server) -> list[tuple[int, str, str]]:
    return [(port.port, port.protocol, port.role) for port in server.ports]


def _container_is_running(client: NodeClient, container_name: str) -> bool:
    for container in client.list_containers():
        if str(container.get("name") or "") != container_name:
            continue
        state = str(container.get("status") or container.get("state") or "").lower()
        return state in {"running", "restarting", "paused"}
    return False


def _write_source_archive(
    server: Server,
    source_client: NodeClient,
    archive_path: str,
    db: Session,
) -> None:
    from services.backup_paths import create_full_backup_tar
    from services.postgres_service import backup_context, backup_pg_dump_for_archive

    if server.node is not None and not server.node.is_local:
        with open(archive_path, "wb") as archive:
            for chunk in source_client.files_archive(
                server.id,
                postgres=backup_context(db, server.id),
            ):
                archive.write(chunk)
        return

    install_dir = Path(server.install_dir)
    if not install_dir.is_dir():
        raise ServerNodeMigrationError("Quellverzeichnis des Servers fehlt")
    pg_dumps = backup_pg_dump_for_archive(db, server.id)
    create_full_backup_tar(
        archive_path,
        str(install_dir),
        pg_dump_dict=pg_dumps or None,
        server_id=server.id,
    )


def migrate_server_to_node(
    db: Session,
    *,
    server_id: int,
    target_node_id: int,
    target_bind_ip: str | None = None,
    work_dir: str | None = None,
) -> dict[str, Any]:
    """Copy a stopped server to ``target_node_id`` and atomically switch it.

    Source files and databases remain untouched. Destination files use the
    agent's staged restore contract and are rolled back on every pre-cutover
    failure. No plaintext archive path or credential is logged.
    """
    from games.base import container_name_for
    from services.backup_paths import read_pg_dump_from_archive
    from services.postgres_service import restore_pg_dump_from_archive
    from services.server_lifecycle_service import get_server_lifecycle_lock

    server = db.query(Server).filter(Server.id == server_id).first()
    target = db.query(Node).filter(Node.id == target_node_id).first()
    if server is None or server.node is None:
        raise ServerNodeMigrationError("Server oder Quellnode nicht gefunden")
    if target is None:
        raise ServerNodeMigrationError("Zielnode nicht gefunden")
    if server.node_id == target.id:
        raise ServerNodeMigrationError("Server ist bereits dem Zielnode zugeordnet")
    if server.status not in {"stopped", "error"}:
        raise ServerNodeMigrationError("Server muss vor dem Umzug gestoppt sein")
    if server.public_bind_ip and target_bind_ip is None:
        raise ServerNodeMigrationError(
            "Für einen Server mit fester Quell-IP muss die Ziel-Bind-IP ausdrücklich angegeben werden"
        )
    normalized_bind_ip = (target_bind_ip or "").strip()
    if normalized_bind_ip:
        try:
            normalized_bind_ip = ipaddress.ip_address(normalized_bind_ip).compressed
        except ValueError as exc:
            raise ServerNodeMigrationError("Ziel-Bind-IP ist ungültig") from exc

    lock = get_server_lifecycle_lock(server.id)
    if not lock.acquire(blocking=False):
        raise ServerNodeMigrationError("Server wird bereits durch eine andere Operation verändert")

    destination_staged = False
    cutover_committed = False
    migration_root = Path(work_dir or (Path(settings.panel_backup_dir).parent / "migrations"))
    try:
        migration_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(migration_root, 0o700)
        except OSError:
            pass

        source_client = NodeClient.from_node(server.node, timeout=600.0)
        target_client = NodeClient.from_node(target, timeout=600.0)
        source_client.health()
        target_client.health()

        container_name = container_name_for(server.id)
        if _container_is_running(source_client, container_name):
            raise ServerNodeMigrationError("Quellcontainer läuft noch")
        if _container_is_running(target_client, container_name):
            raise ServerNodeMigrationError("Auf dem Zielnode läuft bereits ein Container mit dieser ID")

        ports = _ports(server)
        if ports:
            availability = target_client.ports_available(
                ports,
                normalized_bind_ip or "0.0.0.0",
            )
            if availability.get("available") is not True:
                raise ServerNodeMigrationError("Benötigte Ports sind auf dem Zielnode belegt")

        with tempfile.TemporaryDirectory(prefix=f"server-{server.id}-", dir=migration_root) as tmp:
            try:
                os.chmod(tmp, 0o700)
            except OSError:
                pass
            archive_path = os.path.join(tmp, "server.tar.gz")
            _write_source_archive(server, source_client, archive_path, db)
            if not os.path.isfile(archive_path) or os.path.getsize(archive_path) <= 0:
                raise ServerNodeMigrationError("Serverarchiv ist leer")
            try:
                os.chmod(archive_path, 0o600)
            except OSError:
                pass

            destination_staged = True
            target_client.files_restore_archive(server.id, archive_path)

            pg_dumps = read_pg_dump_from_archive(archive_path)
            if pg_dumps:
                restore_pg_dump_from_archive(
                    db,
                    server.id,
                    pg_dumps,
                    client=target_client,
                )

            source_node_id = server.node_id
            server.node = target
            server.node_id = target.id
            server.public_bind_ip = normalized_bind_ip or None
            server.status = "stopped"
            server.status_message = "Auf Zielnode migriert; Quelldaten wurden beibehalten"
            try:
                db.commit()
            except Exception:
                db.rollback()
                server.node_id = source_node_id
                raise
            cutover_committed = True

            cleanup_pending = False
            try:
                target_client.files_finalize_restore(server.id)
                destination_staged = False
            except Exception:
                # Cutover and target data are already valid. Keep both instead
                # of rolling the target back under the committed DB mapping.
                destination_staged = False
                cleanup_pending = True
                server.status_message = (
                    "Auf Zielnode migriert; temporärer Ziel-Rollbackstand muss bereinigt werden"
                )
                db.commit()
                logger.warning(
                    "Ziel-Finalisierung nach erfolgreichem Cutover ausstehend (server_id=%s)",
                    server.id,
                )

        logger.info(
            "Server-Node-Migration abgeschlossen (server_id=%s, target_node_id=%s)",
            server.id,
            target.id,
        )
        return {
            "ok": True,
            "server_id": server.id,
            "source_node_id": source_node_id,
            "target_node_id": target.id,
            "source_retained": True,
            "cleanup_pending": cleanup_pending,
        }
    except ServerNodeMigrationError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.warning(
            "Server-Node-Migration fehlgeschlagen (server_id=%s, target_node_id=%s, type=%s)",
            server_id,
            target_node_id,
            type(exc).__name__,
        )
        raise ServerNodeMigrationError("Server konnte nicht sicher migriert werden") from exc
    finally:
        if destination_staged and not cutover_committed:
            try:
                target_client.files_rollback_restore(server_id)
            except Exception:
                logger.error(
                    "Ziel-Rollback nach Server-Migration fehlgeschlagen (server_id=%s)",
                    server_id,
                )
        lock.release()
