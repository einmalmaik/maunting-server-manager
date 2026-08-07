"""Kleine Persistenzschicht für idempotente, nachvollziehbare Aufgaben."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import OperationTask, User
from services import audit_service, permission_service
from services.actor_context import ActorContext


TASK_SERVER_PROVISION = "server.provision"
TERMINAL_STATUSES = frozenset({"succeeded", "failed"})
_MAX_IDEMPOTENCY_KEY_LENGTH = 128
_TASK_TYPE_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_ORIGINS = frozenset({"direct", "ai", "external", "system"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > _MAX_IDEMPOTENCY_KEY_LENGTH
        or not _IDEMPOTENCY_KEY_RE.fullmatch(normalized)
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_idempotency_key",
                "message": "errors.invalid_idempotency_key",
            },
        )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_task_id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden") from exc


def create_or_reuse_task(
    db: Session,
    *,
    actor: ActorContext,
    task_type: str,
    request_hash: str,
    idempotency_key: str | None = None,
    retry_of_id: str | None = None,
) -> tuple[OperationTask, bool]:
    """Legt eine Task vor Seiteneffekten an oder liefert dieselbe Anfrage zurück."""
    task_type = (task_type or "").strip()
    if len(task_type) > 64 or not _TASK_TYPE_RE.fullmatch(task_type):
        raise ValueError("Ungültiger Task-Typ")
    if not _SHA256_RE.fullmatch(request_hash or ""):
        raise ValueError("Ungültiger Request-Fingerprint")
    if actor.origin not in _ORIGINS:
        raise ValueError("Ungültige Task-Herkunft")
    try:
        correlation_id = str(UUID(str(actor.correlation_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Ungültige Task-Korrelations-ID") from exc
    key_hash = _hash_idempotency_key(idempotency_key)
    if retry_of_id and key_hash is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "retry_requires_idempotency_key", "message": "errors.retry_requires_idempotency_key"},
        )

    if key_hash is not None:
        existing = (
            db.query(OperationTask)
            .filter(
                OperationTask.actor_user_id == actor.user.id,
                OperationTask.task_type == task_type,
                OperationTask.idempotency_key_hash == key_hash,
            )
            .first()
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "idempotency_key_conflict", "message": "errors.idempotency_key_conflict"},
                )
            return existing, False

    retry_of = None
    attempt = 1
    if retry_of_id:
        retry_of = (
            db.query(OperationTask)
            .filter(
                OperationTask.id == _canonical_task_id(retry_of_id),
                OperationTask.actor_user_id == actor.user.id,
                OperationTask.task_type == task_type,
            )
            .first()
        )
        if retry_of is None:
            raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
        if retry_of.status != "failed" or retry_of.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail={"code": "task_not_retryable", "message": "errors.task_not_retryable"},
            )
        attempt = retry_of.attempt + 1

    task = OperationTask(
        id=str(uuid4()),
        task_type=task_type,
        actor_user_id=actor.user.id,
        origin=actor.origin,
        correlation_id=correlation_id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        retry_of_id=retry_of.id if retry_of else None,
        attempt=attempt,
        status="queued",
        phase="accepted",
    )
    db.add(task)
    try:
        db.commit()
        db.refresh(task)
    except IntegrityError:
        db.rollback()
        # Konkurrenz auf derselben Idempotency-ID: Gewinner sicher laden.
        if key_hash is None:
            raise
        existing = (
            db.query(OperationTask)
            .filter(
                OperationTask.actor_user_id == actor.user.id,
                OperationTask.task_type == task_type,
                OperationTask.idempotency_key_hash == key_hash,
            )
            .first()
        )
        if existing is None or existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail={"code": "idempotency_key_conflict", "message": "errors.idempotency_key_conflict"},
            )
        return existing, False
    return task, True


def mark_running(db: Session, task: OperationTask, phase: str) -> None:
    if task.status in TERMINAL_STATUSES:
        return
    now = _utcnow()
    task.status = "running"
    task.phase = phase
    task.started_at = task.started_at or now
    task.updated_at = now
    db.commit()


def set_phase(
    db: Session,
    task: OperationTask,
    phase: str,
    *,
    server_id: int | None = None,
) -> None:
    if task.status in TERMINAL_STATUSES:
        return
    task.phase = phase
    if server_id is not None:
        task.server_id = server_id
    task.updated_at = _utcnow()
    db.commit()


def mark_succeeded(db: Session, task: OperationTask, *, phase: str = "ready") -> None:
    if task.status in TERMINAL_STATUSES:
        return
    now = _utcnow()
    task.status = "succeeded"
    task.phase = phase
    task.error_code = None
    task.error_message = None
    task.completed_at = now
    task.updated_at = now
    db.commit()


def mark_failed(
    db: Session,
    task: OperationTask,
    *,
    error_code: str,
    phase: str = "rolled_back",
) -> None:
    if task.status == "succeeded":
        return
    now = _utcnow()
    task.status = "failed"
    task.phase = phase
    task.error_code = error_code[:64]
    task.error_message = f"errors.{error_code}"[:128]
    task.completed_at = now
    task.updated_at = now
    db.commit()


def finish_server_provisioning(
    db: Session,
    server_id: int,
    *,
    succeeded: bool,
    success_phase: str = "ready",
) -> None:
    """Schließt die jüngste laufende Provisionierung aus dem Install-Callback."""
    task = (
        db.query(OperationTask)
        .filter(
            OperationTask.task_type == TASK_SERVER_PROVISION,
            OperationTask.server_id == server_id,
            OperationTask.status == "running",
        )
        .order_by(OperationTask.created_at.desc())
        .first()
    )
    if task is None or task.status in TERMINAL_STATUSES:
        return
    if succeeded:
        mark_succeeded(db, task, phase=success_phase)
        error_code = None
    else:
        error_code = "server_installation_failed"
        mark_failed(db, task, error_code=error_code, phase="failed")
    audit_service.record_privileged_action(
        db,
        user_id=task.actor_user_id,
        action="server.provision.completed" if succeeded else "server.provision.failed",
        target_type="server",
        target_id=server_id,
        details={"task_id": task.id, "error_code": error_code},
        origin=task.origin,
        correlation_id=task.correlation_id,
    )
    db.commit()


def finish_lifecycle_task(
    db: Session,
    task_id: str | None,
    *,
    succeeded: bool,
    error_code: str = "server_lifecycle_failed",
) -> None:
    """Schließt einen explizit an den Worker übergebenen Lifecycle-Task ab."""
    if task_id is None:
        return
    task = db.query(OperationTask).filter(OperationTask.id == task_id).first()
    if (
        task is None
        or task.status in TERMINAL_STATUSES
        or not task.task_type.startswith("server.lifecycle.")
    ):
        return
    if succeeded:
        mark_succeeded(db, task)
    else:
        mark_failed(db, task, error_code=error_code, phase="failed")
    # Ein KI-Vorschlag, der diese Aktion ausgeloest hat, wartet auf genau diesen
    # Abschluss. Ohne das bliebe er dauerhaft auf "executing" stehen, obwohl der
    # Vorgang laengst entschieden ist.
    from models import AiActionProposal

    proposal = (
        db.query(AiActionProposal)
        .filter(
            AiActionProposal.task_id == task.id,
            AiActionProposal.status == "executing",
        )
        .first()
    )
    if proposal is not None:
        proposal.status = "succeeded" if succeeded else "failed"
        proposal.executed_at = _utcnow()
        if not succeeded:
            proposal.error_code = error_code[:64]

    operation = task.task_type.rsplit(".", 1)[-1]
    audit_service.record_privileged_action(
        db,
        user_id=task.actor_user_id,
        action="server.lifecycle.completed" if succeeded else "server.lifecycle.failed",
        target_type="server",
        target_id=task.server_id,
        details={"task_id": task.id, "operation": operation, "error_code": None if succeeded else error_code},
        origin=task.origin,
        correlation_id=task.correlation_id,
    )
    db.commit()


def recover_interrupted_tasks(db: Session) -> int:
    """Markiert nach Prozessneustart nicht fortsetzbare Tasks eindeutig fehlgeschlagen."""
    tasks = (
        db.query(OperationTask)
        .filter(OperationTask.status.in_(("queued", "running")))
        .all()
    )
    recovered = 0
    for task in tasks:
        if task.task_type == TASK_SERVER_PROVISION and task.server_id is not None:
            from models import Server
            from services.server_provisioning_service import PENDING_INSTALL_DIR

            server = db.query(Server).filter(Server.id == task.server_id).first()
            if server is not None and server.install_dir == PENDING_INSTALL_DIR:
                # Der Prozess endete zwischen dem Commit der Serverzeile und dem
                # Anlegen des Installationsverzeichnisses. Die Zeile ist nicht
                # benutzbar, belegt aber ihren Portblock. Ohne diesen Zweig
                # wurde sie wegen status="stopped" als erfolgreiche
                # Provisionierung gemeldet.
                db.delete(server)
                db.commit()
                mark_failed(
                    db,
                    task,
                    error_code="server_provisioning_interrupted",
                    phase="failed",
                )
                recovered += 1
                continue
            if server is not None and server.status in {"stopped", "running", "awaiting_files"}:
                mark_succeeded(db, task)
                recovered += 1
                continue
            if server is not None and server.status == "installing":
                # Der Install-Thread lebt nur im beendeten Webprozess. Ohne
                # bestätigten Agent-Job wäre ein stilles Weiterlaufen eine
                # falsche Erfolgsaussage; ein Retry bleibt bewusst manuell.
                server.status = "error"
                server.status_message = "Provisionierung durch Panel-Neustart unterbrochen"
                db.commit()
            if server is not None and server.status == "error":
                mark_failed(
                    db,
                    task,
                    error_code="server_installation_interrupted",
                    phase="failed",
                )
                recovered += 1
                continue
        mark_failed(db, task, error_code="task_interrupted", phase="failed")
        recovered += 1
    return recovered


def get_visible_task(db: Session, user: User, task_id: str) -> OperationTask:
    task = db.query(OperationTask).filter(OperationTask.id == _canonical_task_id(task_id)).first()
    can_read_all = permission_service.has_global_permission(db, user, "system.audit.read")
    if task is None or (task.actor_user_id != user.id and not can_read_all):
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    return task


def list_visible_tasks(
    db: Session,
    user: User,
    *,
    limit: int,
    status: str | None,
) -> list[OperationTask]:
    query = db.query(OperationTask)
    if not permission_service.has_global_permission(db, user, "system.audit.read"):
        query = query.filter(OperationTask.actor_user_id == user.id)
    if status is not None:
        if status not in {"queued", "running", "succeeded", "failed"}:
            raise HTTPException(status_code=422, detail="Ungültiger Task-Status")
        query = query.filter(OperationTask.status == status)
    return query.order_by(OperationTask.created_at.desc()).limit(min(max(limit, 1), 100)).all()
