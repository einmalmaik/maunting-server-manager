from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends

from ..models import AuditLog, User
from ..permissions import P_DASHBOARD_VIEW, require_perm
from ..shell import (
    PanelCommandError,
    fetch_action_task,
    fetch_autorestart_status,
    fetch_backup_runs,
    fetch_core_status,
    fetch_panel_status,
    fetch_workshop_status,
)
from .deps import get_current_server, get_db

router = APIRouter()
logger = logging.getLogger(__name__)


def _extract_bridge_error(exc: PanelCommandError) -> str:
    if exc.result is not None:
        return exc.result.stderr or exc.result.stdout or "Bridge command failed."
    return str(exc) or "Bridge command failed."


def _serialize_audit(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "actor_username": entry.actor_username,
        "action": entry.action,
        "target": entry.target,
        "status": entry.status,
        "detail": entry.detail,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


@router.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    _: User = require_perm(P_DASHBOARD_VIEW),
    server: str | None = Depends(get_current_server),
):
    errors: list[str] = []
    core_status = None
    panel_status = None
    autorestart = None
    workshop = None
    backup_runs: list = []

    # Fetch panel_status and audit_entries regardless of server selection
    # since they don't depend on a specific server
    try:
        panel_status = fetch_panel_status()
    except PanelCommandError as exc:
        errors.append(_extract_bridge_error(exc))

    audit_entries: list = []
    try:
        audit_entries = db.scalars(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
        ).all()
    except SQLAlchemyError:
        logger.exception("Failed to fetch audit log entries.")
        errors.append("Failed to fetch audit log.")

    # If no server is selected, return early with minimal data
    if server is None:
        return {
            "core_status": None,
            "panel_status": panel_status,
            "autorestart": None,
            "workshop": None,
            "backup_runs": [],
            "audit_entries": [_serialize_audit(e) for e in audit_entries],
            "bridge_error": "No server selected. Please create or select a server first.",
            "task": None,
        }

    # Fetch each data source independently so a single failure does not
    # prevent the rest of the dashboard from loading.
    try:
        core_status = fetch_core_status(server_name=server)
    except PanelCommandError as exc:
        errors.append(_extract_bridge_error(exc))

    try:
        autorestart = fetch_autorestart_status(server_name=server)
    except PanelCommandError as exc:
        errors.append(_extract_bridge_error(exc))

    try:
        workshop = fetch_workshop_status(server_name=server)
    except PanelCommandError as exc:
        errors.append(_extract_bridge_error(exc))

    try:
        result = fetch_backup_runs(server_name=server)
        backup_runs = ((result or {}).get("runs") or [])[:5]
    except PanelCommandError as exc:
        errors.append(_extract_bridge_error(exc))

    task = None
    try:
        task = fetch_action_task(server_name=server, task_channel="default")
    except PanelCommandError as exc:
        errors.append(_extract_bridge_error(exc))

    bridge_error = "; ".join(e[:200] for e in errors) if errors else None

    return {
        "core_status": core_status,
        "panel_status": panel_status,
        "autorestart": autorestart,
        "workshop": workshop,
        "backup_runs": backup_runs,
        "audit_entries": [_serialize_audit(e) for e in audit_entries],
        "bridge_error": bridge_error,
        "task": task,
    }
