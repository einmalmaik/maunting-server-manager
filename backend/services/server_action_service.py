"""Autorisierter Einstieg für gemeinsame Server-Lifecycle-Aktionen."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from blueprints.schema import BlueprintSourceType, _is_safe_relative_path
from games import get_plugin
from models import Server, User
from services import audit_service, permission_service
from services.actor_context import ActorContext
from services.operation_task_service import (
    create_or_reuse_task,
    finish_lifecycle_task,
    mark_running,
    set_phase,
)
from services.server_lifecycle_service import (
    LifecycleNotification,
    queue_lifecycle_operation,
)


LIFECYCLE_PERMISSIONS = {
    "start": "server.start",
    "stop": "server.stop",
    "restart": "server.restart",
    "kill": "server.kill",
}


def missing_required_files(install_dir: str, required_files: list[str]) -> list[str]:
    """Prüft Manual-Upload-Dateien ohne Traversal oder Symlink-Akzeptanz."""
    base = Path(install_dir).resolve()
    missing: list[str] = []
    for relative_path in required_files:
        if not _is_safe_relative_path(relative_path):
            missing.append(relative_path)
            continue
        target = base / relative_path
        try:
            target.resolve(strict=False).relative_to(base)
        except (ValueError, RuntimeError):
            missing.append(relative_path)
            continue
        if target.is_symlink() or not target.is_file():
            missing.append(relative_path)
    return missing


def _active_principal(db: Session, actor: ActorContext) -> ActorContext:
    user = (
        db.query(User)
        .filter(User.id == actor.user.id, User.is_active.is_(True))
        .first()
    )
    if user is None:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return ActorContext(
        user=user,
        origin=actor.origin,
        correlation_id=actor.correlation_id,
    )


def _validate_lifecycle_request(
    db: Session,
    actor: ActorContext,
    server_id: int,
    operation: str,
) -> Server:
    permission = LIFECYCLE_PERMISSIONS.get(operation)
    if permission is None:
        raise HTTPException(status_code=400, detail="Unbekannte Lifecycle-Aktion")
    if not permission_service.has_server_permission(db, actor.user, server_id, permission):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    from services.node_service import NODE_OFFLINE_MSG, is_node_offline

    if operation != "kill" and is_node_offline(getattr(server, "node", None)):
        raise HTTPException(status_code=503, detail=NODE_OFFLINE_MSG)
    plugin = get_plugin(server.game_type) if operation != "kill" else None
    if operation != "kill" and plugin is None:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")

    if operation == "start":
        if not server.public_bind_ip:
            from services.network_interfaces_service import default_bind_ip
            assigned = default_bind_ip() or "127.0.0.1"
            server.public_bind_ip = assigned
            db.commit()
        blueprint = plugin.get_blueprint()
        # `missing_required_files` prueft das Dateisystem des Panels. Bei einem
        # Remote-Node liegen die Dateien aber auf dem Agent, und der Panel-Pfad
        # existiert dort gar nicht — die Pruefung wuerde also *immer* fehlende
        # Dateien melden und den Server dauerhaft unstartbar machen. Fuer
        # Remote-Nodes uebernimmt die Runtime-Vorbereitung auf dem Agent diese
        # Validierung, genau wie beim Installationspfad.
        node = getattr(server, "node", None)
        is_remote_node = node is not None and not getattr(node, "is_local", False)
        if (
            blueprint
            and blueprint.source.type == BlueprintSourceType.MANUAL_UPLOAD
            and not is_remote_node
        ):
            manual = blueprint.source.manual
            assert manual is not None
            missing = missing_required_files(server.install_dir, manual.requiredFiles)
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Server kann nicht gestartet werden - folgende Dateien fehlen "
                        f"im Server-Verzeichnis: {', '.join(missing)}. "
                        "Bitte über den File Manager hochladen."
                    ),
                )
    return server


def _request_hash(server_id: int, operation: str) -> str:
    return hashlib.sha256(f"{server_id}:{operation}".encode("ascii")).hexdigest()


def request_lifecycle_operation(
    db: Session,
    *,
    server_id: int,
    operation: str,
    actor: ActorContext,
    notification: LifecycleNotification | None = None,
    idempotency_key: str | None = None,
    retry_of_id: str | None = None,
) -> dict:
    """Prüft RBAC und Pre-Checks vor jeder Worker-/Node-Ausführung erneut."""
    actor = _active_principal(db, actor)
    server = _validate_lifecycle_request(db, actor, server_id, operation)
    task_type = f"server.lifecycle.{operation}"
    task, created = create_or_reuse_task(
        db,
        actor=actor,
        task_type=task_type,
        request_hash=_request_hash(server_id, operation),
        idempotency_key=idempotency_key,
        retry_of_id=retry_of_id,
    )
    if not created:
        if task.status == "failed":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": task.error_code or "task_failed",
                    "message": task.error_message or "errors.task_failed",
                    "task_id": task.id,
                },
            )
        return {
            "message": "Lifecycle-Aktion wurde bereits angenommen",
            "status": task.status,
            "operation": operation,
            "task_id": task.id,
        }

    mark_running(db, task, "queued")
    set_phase(db, task, "queued", server_id=server.id)
    try:
        audit_service.record_privileged_action(
            db,
            user_id=actor.user.id,
            action="server.lifecycle.requested",
            target_type="server",
            target_id=server.id,
            details={"task_id": task.id, "operation": operation},
            origin=actor.origin,
            correlation_id=actor.correlation_id,
        )
        db.commit()
        result = queue_lifecycle_operation(
            db,
            server,
            operation,
            notification,
            task_id=task.id,
        )
        if operation == "kill":
            finish_lifecycle_task(db, task.id, succeeded=True)
    except Exception as exc:
        db.rollback()
        error_code = (
            exc.detail.get("code")
            if isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
            else "lifecycle_request_failed"
        )
        finish_lifecycle_task(
            db,
            task.id,
            succeeded=False,
            error_code=str(error_code),
        )
        raise
    return {**result, "task_id": task.id}
