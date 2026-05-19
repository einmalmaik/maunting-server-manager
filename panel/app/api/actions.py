from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..models import AuditLog, User
from ..permissions import (
    P_BACKUPS_CREATE,
    P_DASHBOARD_VIEW,
    P_SERVER_INSTALL,
    P_SERVER_RESTART,
    P_SERVER_START,
    P_SERVER_STOP,
    P_SERVER_UPDATE,
    P_SERVER_VALIDATE,
    P_SERVER_WIPE,
    P_WORKSHOP_UPDATE,
    has_permission,
    require_perm,
)
from ..shell import (
    PanelCommandError,
    fetch_action_log,
    fetch_action_task,
    invoke_core_action,
    invoke_core_action_async,
)
from .deps import get_current_user, get_db, require_server

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_ACTIONS: dict[str, tuple[str, ...]] = {
    "start": ("start",),
    "stop": ("stop",),
    "restart": ("restart",),
    "install": ("install",),
    "update": ("update",),
    "validate": ("validate",),
    "workshop": ("workshop",),
    "backup": ("backup",),
    "wipe": ("wipe",),
}

_ACTION_PERMISSIONS: dict[str, str] = {
    "start": P_SERVER_START,
    "stop": P_SERVER_STOP,
    "restart": P_SERVER_RESTART,
    "install": P_SERVER_INSTALL,
    "update": P_SERVER_UPDATE,
    "validate": P_SERVER_VALIDATE,
    "workshop": P_WORKSHOP_UPDATE,
    "backup": P_BACKUPS_CREATE,
    "wipe": P_SERVER_WIPE,
}


def _clean_action_detail(detail: str | None, fallback: str) -> str:
    if not detail:
        return fallback
    lines = [line.strip() for line in detail.replace("\r", "").splitlines() if line.strip()]
    if not lines:
        return fallback
    return lines[-1]


def _record_audit(
    db: Session,
    user: User,
    action: str,
    status_value: str,
    detail: str | None,
    target: str | None = None,
) -> None:
    entry = AuditLog(
        user_id=user.id,
        actor_username=user.username,
        action=action,
        target=target,
        status=status_value,
        detail=detail,
    )
    try:
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
        raise


@router.post("/actions/{action_name}")
def invoke_action(
    action_name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    server: str = Depends(require_server),
) -> dict[str, Any]:
    if action_name not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action_name}")

    required_perm = _ACTION_PERMISSIONS.get(action_name)
    if required_perm is None:
        raise HTTPException(status_code=403, detail="Permission denied.")
    if not has_permission(user, required_perm):
        raise HTTPException(status_code=403, detail="Permission denied.")

    if action_name in ("update", "install", "validate", "workshop", "backup", "wipe"):
        try:
            invoke_core_action_async(
                *_ALLOWED_ACTIONS[action_name],
                server_name=server,
                task_channel="workshop" if action_name == "workshop" else "default",
            )
            _record_audit(db, user, action_name, "started", "Async action started", target=server)
            return {"ok": True, "async": True}
        except Exception as exc:
            sanitized_detail = str(exc)[:500]
            logger.exception("Async action %s failed to start.", action_name)
            try:
                _record_audit(db, user, action_name, "failed", sanitized_detail, target=server)
            except Exception:
                logger.exception("Failed to record audit log for async action %s.", action_name)
            lowered_detail = sanitized_detail.lower()
            status_code = 409 if "already running" in lowered_detail or "preparing to start" in lowered_detail else 500
            raise HTTPException(
                status_code=status_code,
                detail=_clean_action_detail(sanitized_detail, "Action failed to start. Check server logs."),
            )

    try:
        result = invoke_core_action(*_ALLOWED_ACTIONS[action_name], server_name=server)
        try:
            success_detail = (result.stdout or "OK")[:500]
            _record_audit(db, user, action_name, "success", success_detail, target=server)
        except Exception:
            logger.exception("Failed to record audit log for successful action %s.", action_name)
        return {"ok": True}
    except PanelCommandError as exc:
        result = getattr(exc, "result", None)
        detail = (
            getattr(result, "stderr", None)
            or getattr(result, "stdout", None)
            or str(exc)
        ) if result else str(exc)
        sanitized_detail = detail[:500]
        try:
            _record_audit(db, user, action_name, "failed", sanitized_detail, target=server)
        except Exception:
            logger.exception("Failed to record audit log for failed action %s.", action_name)
        logger.error("Action %s failed: %s", action_name, detail)
        raise HTTPException(status_code=500, detail=_clean_action_detail(detail, "Action failed. Check server logs."))
    except Exception as exc:
        sanitized_detail = str(exc)[:500]
        try:
            _record_audit(db, user, action_name, "failed", sanitized_detail, target=server)
        except Exception:
            logger.exception("Failed to record audit log for errored action %s.", action_name)
        logger.exception("Action %s encountered unexpected error.", action_name)
        raise HTTPException(status_code=500, detail="An internal server error occurred.")


@router.get("/actions/status")
def get_action_status(
    channel: Literal["default", "workshop"] = Query("default"),
    server: str = Depends(require_server),
    user: User = require_perm(P_DASHBOARD_VIEW),
) -> dict[str, Any]:
    task = fetch_action_task(server_name=server, task_channel=channel)
    log = fetch_action_log(server_name=server, task_channel=channel)
    return {
        "task": task,
        "log": log,
    }
