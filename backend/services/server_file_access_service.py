"""Gemeinsamer, revisionssicherer Textdateizugriff fuer Panel und AI."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Server, User
from services import docker_service, file_edit_service, file_history_service
from services.dis_client import DisSidecarError
from services.file_edit_service import FileRevisionConflict
from services.node_client import NodeClient, NodeClientError
from services.node_service import resolve_server_node


MAX_EDIT_SIZE = 5 * 1024 * 1024


def safe_path(install_dir: str, relative_path: str) -> Path:
    if relative_path.startswith(("/", "\\")) or ".." in Path(relative_path).parts:
        raise HTTPException(status_code=400, detail="Ungueltiger relativer Dateipfad")
    base = Path(install_dir).resolve(strict=False)
    target = (base / relative_path).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Dateipfad liegt ausserhalb des Servers") from exc
    return target


def _server(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


def _agent_key(server: Server) -> str:
    install = (server.install_dir or "").strip()
    base = os.path.basename(os.path.normpath(install)) if install else ""
    if base and base not in {".", ".."} and "/" not in base and "\\" not in base and ".." not in base:
        return base
    return str(server.id)


def _agent(server: Server, db: Session) -> NodeClient | None:
    from services.node_service import NODE_OFFLINE_MSG, is_node_offline

    node = resolve_server_node(server, db)
    if node is None:
        return None
    if is_node_offline(node) and not node.is_local:
        raise HTTPException(status_code=503, detail=NODE_OFFLINE_MSG)
    if node.is_local and server.install_dir and os.path.isdir(server.install_dir):
        return None
    try:
        return NodeClient.from_node(node)
    except NodeClientError as exc:
        if node.is_local:
            return None
        raise HTTPException(status_code=503, detail=exc.message or "Node-Agent nicht erreichbar") from exc


def _agent_error(exc: NodeClientError) -> HTTPException:
    status_code = exc.status_code or 502
    if status_code not in {400, 403, 404, 409, 413}:
        status_code = 502
    return HTTPException(status_code=status_code, detail=exc.message or "Node-Agent Fehler")


def read_server_text(db: Session, *, server_id: int, relative_path: str) -> dict:
    server = _server(db, server_id)
    agent = _agent(server, db)
    if agent is not None:
        try:
            return {"path": relative_path, "name": relative_path.rsplit("/", 1)[-1], **agent.files_read_info(_agent_key(server), relative_path)}
        except NodeClientError as exc:
            raise _agent_error(exc) from exc
    target = safe_path(server.install_dir, relative_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Pfad ist keine Datei")
    if target.stat().st_size > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="Datei ist zu gross")
    try:
        return {"path": relative_path, "name": target.name, **file_edit_service.read_text(target)}
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Datei konnte nicht gelesen werden") from exc


def _apply_permissions(install_dir: str, target: Path) -> None:
    def normalize(path: Path) -> None:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return
        path.chmod(0o750 if path.is_dir() or stat.S_IMODE(info.st_mode) & 0o111 else 0o640)

    try:
        normalize(target)
        base = Path(install_dir).resolve()
        parent = target.parent.resolve()
        while parent != base and parent != parent.parent:
            normalize(parent)
            parent = parent.parent
    except OSError:
        return


def write_server_text(
    db: Session,
    *,
    user: User,
    server_id: int,
    relative_path: str,
    content: str,
    expected_revision: str | None,
    create_only: bool = False,
    repair_permissions: Callable[[str], dict] | None = None,
) -> dict:
    server = _server(db, server_id)
    agent = _agent(server, db)
    if agent is not None:
        try:
            try:
                current = agent.files_read_info(_agent_key(server), relative_path)
            except NodeClientError as exc:
                if exc.status_code != 404:
                    raise
                current = None
            if expected_revision is not None and (
                current is None or current.get("revision") != expected_revision
            ):
                raise HTTPException(status_code=409, detail={"code": "FILE_REVISION_CONFLICT"})
            if create_only and current is not None:
                raise HTTPException(status_code=409, detail="Zieldatei existiert bereits")
            if current is not None:
                file_history_service.snapshot(server_id, relative_path, str(current.get("content", "")), user.id)
            result = agent.files_write(
                _agent_key(server), relative_path, content, expected_revision, create_only
            )
            return {"path": relative_path, **result}
        except NodeClientError as exc:
            raise _agent_error(exc) from exc
        except (DisSidecarError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail="Versionsspeicher ist nicht verfuegbar") from exc

    target = safe_path(server.install_dir, relative_path)
    try:
        current = file_edit_service.read_text(target) if target.is_file() else None
        if expected_revision is not None and (
            current is None or current.get("revision") != expected_revision
        ):
            raise FileRevisionConflict(current.get("revision") if current else None)
        if create_only and current is not None:
            raise FileExistsError
        if current is not None:
            file_history_service.snapshot(server_id, relative_path, str(current["content"]), user.id)
        try:
            result = file_edit_service.write_text(
                target,
                content,
                expected_revision=expected_revision,
                create_only=create_only,
            )
        except PermissionError as first_error:
            repair = (repair_permissions or docker_service.repair_bind_mount_permissions)(
                server.install_dir
            )
            if not repair.get("ok"):
                raise HTTPException(
                    status_code=500, detail="Datei konnte nicht gespeichert werden"
                ) from first_error
            result = file_edit_service.write_text(
                target,
                content,
                expected_revision=expected_revision,
                create_only=create_only,
            )
        _apply_permissions(server.install_dir, target)
        return {"path": relative_path, **result}
    except FileRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "FILE_REVISION_CONFLICT", "current_revision": exc.current_revision},
        ) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Zieldatei existiert bereits") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="Datei konnte nicht gespeichert werden") from exc
    except (DisSidecarError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Versionsspeicher ist nicht verfuegbar") from exc
