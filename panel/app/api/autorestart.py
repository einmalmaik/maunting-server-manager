from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..models import AuditLog, User
from ..permissions import P_AUTORESTART_MANAGE, P_AUTORESTART_VIEW, require_perm
from ..shell import PanelCommandError, fetch_autorestart_status, invoke_core_action
from .deps import get_db, require_server

router = APIRouter()
logger = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
_VALID_INTERVAL_HOURS = {1, 2, 3, 4, 6, 8, 12, 24}


def _record_audit(
    db: Session,
    user: User,
    action: str,
    target: str | None,
    status_value: str,
    detail: str | None,
) -> None:
    entry = AuditLog(
        user_id=user.id,
        actor_username=user.username,
        action=action,
        target=target,
        status=status_value,
        detail=detail,
    )
    db.add(entry)
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to record audit log action=%s user=%s", action, user.username)
        db.rollback()


@router.get("/autorestart")
def get_autorestart(
    user: User = require_perm(P_AUTORESTART_VIEW),
    server: str = Depends(require_server),
):
    try:
        return fetch_autorestart_status(server_name=server)
    except PanelCommandError as exc:
        detail = exc.result.stderr or str(exc)
        logger.error("autorestart fetch failed: %s", detail)
        raise HTTPException(status_code=500, detail=detail or "Failed to fetch autorestart status.")


class AutorestartUpdate(BaseModel):
    mode: Literal["off", "times", "interval"]
    times: str = ""
    interval_hours: str = ""


@router.post("/autorestart")
def update_autorestart(
    body: AutorestartUpdate,
    db: Session = Depends(get_db),
    user: User = require_perm(P_AUTORESTART_MANAGE),
    server: str = Depends(require_server),
):
    try:
        if body.mode == "off":
            invoke_core_action("autorestart", "clear", server_name=server)
        elif body.mode == "times":
            time_tokens = [t.strip() for t in body.times.split() if t.strip()]
            if not time_tokens:
                raise HTTPException(status_code=422, detail="At least one time required.")
            invalid = [t for t in time_tokens if not _TIME_RE.match(t)]
            if invalid:
                raise HTTPException(status_code=422, detail=f"Invalid time format(s): {', '.join(invalid)}. Expected HH:MM.")
            invoke_core_action("autorestart", "set", "times", *time_tokens, server_name=server)
        else:  # interval
            raw_interval = body.interval_hours.strip()
            if not raw_interval:
                raise HTTPException(status_code=422, detail="interval_hours required.")
            try:
                interval_val = int(raw_interval)
            except ValueError:
                raise HTTPException(status_code=422, detail="interval_hours must be a whole number.") from None
            if interval_val not in _VALID_INTERVAL_HOURS:
                raise HTTPException(
                    status_code=422,
                    detail=f"interval_hours must be one of: {', '.join(str(value) for value in sorted(_VALID_INTERVAL_HOURS))}.",
                )
            invoke_core_action("autorestart", "set", "interval", str(interval_val), server_name=server)

        try:
            updated = fetch_autorestart_status(server_name=server)
        except PanelCommandError as exc:
            detail = exc.result.stderr or exc.result.stdout or str(exc)
            _record_audit(db, user, "autorestart.update", body.mode, "success", "Applied, but status refresh failed.")
            logger.error("autorestart.update status refresh failed after successful update: %s", detail)
            raise HTTPException(status_code=500, detail=detail or "Autorestart update applied, but status refresh failed.")

        _record_audit(db, user, "autorestart.update", body.mode, "success", updated.get("summary") or "OK")
        return updated
    except PanelCommandError as exc:
        detail = exc.result.stderr or exc.result.stdout or str(exc)
        _record_audit(db, user, "autorestart.update", body.mode, "failed", detail)
        logger.error("autorestart.update failed: %s", detail)
        raise HTTPException(status_code=500, detail=detail or "Autorestart update failed.")
