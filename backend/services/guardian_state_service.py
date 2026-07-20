"""Atomic panel-side Guardian intent and generation updates."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import and_, update
from sqlalchemy.orm import Session

from blueprints.schema import Blueprint
from models import Server
from services.guardian_runtime_compiler import guardian_config_hash


DESIRED_POWER_STATES = frozenset({"running", "stopped"})


def set_desired_power_state(
    db: Session,
    server: Server,
    desired_power_state: Literal["running", "stopped"],
) -> bool:
    if desired_power_state not in DESIRED_POWER_STATES:
        raise ValueError("invalid desired power state")
    result = db.execute(
        update(Server)
        .where(
            Server.id == server.id,
            Server.desired_power_state != desired_power_state,
        )
        .values(
            desired_power_state=desired_power_state,
            desired_state_generation=Server.desired_state_generation + 1,
        )
    )
    changed = bool(result.rowcount)
    if changed:
        try:
            db.commit()
            db.refresh(server)
        except Exception:
            db.rollback()
            db.refresh(server)
            raise
    return changed


def ensure_guardian_config_generation(
    db: Session,
    server: Server,
    blueprint: Blueprint,
) -> bool:
    effective_hash = guardian_config_hash(server, blueprint)
    previous = server.guardian_config_hash
    if previous == effective_hash:
        return False
    if previous is None:
        # First compilation establishes the baseline; it is not a user change.
        result = db.execute(
            update(Server)
            .where(Server.id == server.id, Server.guardian_config_hash.is_(None))
            .values(guardian_config_hash=effective_hash)
        )
    else:
        result = db.execute(
            update(Server)
            .where(
                Server.id == server.id,
                Server.guardian_config_hash == previous,
            )
            .values(
                guardian_config_hash=effective_hash,
                desired_state_generation=Server.desired_state_generation + 1,
            )
        )
    changed = bool(result.rowcount)
    if changed:
        try:
            db.commit()
            db.refresh(server)
        except Exception:
            db.rollback()
            db.refresh(server)
            raise
    else:
        db.rollback()
        db.refresh(server)
    return changed and previous is not None


def mark_guardian_configuration_changed(server: Server) -> None:
    """Mark a declarative server edit inside the caller's open transaction."""
    server.guardian_config_hash = None
    server.desired_state_generation = int(server.desired_state_generation or 0) + 1


def set_recovery_suspension(
    db: Session,
    server: Server,
    *,
    operation_id: str,
    reason: str,
    suspend_until: datetime,
) -> None:
    try:
        if str(uuid.UUID(operation_id)) != operation_id.lower():
            raise ValueError("operation_id must be a canonical UUID")
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("operation_id must be a canonical UUID") from exc

    if not reason or len(reason) > 64 or not re.match(r"^[a-z][a-z0-9_-]*$", reason):
        raise ValueError("reason must be a valid string matching ^[a-z][a-z0-9_-]*$ with max length 64")

    if suspend_until.tzinfo is None:
        suspend_until = suspend_until.replace(tzinfo=timezone.utc)
    else:
        suspend_until = suspend_until.astimezone(timezone.utc)

    now = datetime.now(timezone.utc)
    delta = (suspend_until - now).total_seconds()
    if delta > 4 * 60 * 60 + 300:  # Allow 5-minute clock drift margin
        raise ValueError("recovery suspension duration exceeds 4 hours")

    value = {
        "operation_id": operation_id.lower(),
        "reason": reason,
        "suspend_until": suspend_until.isoformat().replace("+00:00", "Z"),
    }
    try:
        server.guardian_recovery_suspension = json.dumps(
            value, sort_keys=True, separators=(",", ":")
        )
        server.desired_state_generation = int(server.desired_state_generation or 0) + 1
        db.commit()
        db.refresh(server)
    except Exception:
        db.rollback()
        db.refresh(server)
        raise


def clear_recovery_suspension(
    db: Session,
    server: Server,
    *,
    operation_id: str,
) -> bool:
    try:
        if str(uuid.UUID(operation_id)) != operation_id.lower():
            raise ValueError("operation_id must be a canonical UUID")
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("operation_id must be a canonical UUID") from exc

    if not server.guardian_recovery_suspension:
        return False
    current = json.loads(server.guardian_recovery_suspension)
    if current.get("operation_id") != operation_id.lower():
        return False

    try:
        server.guardian_recovery_suspension = None
        server.desired_state_generation = int(server.desired_state_generation or 0) + 1
        db.commit()
        db.refresh(server)
        return True
    except Exception:
        db.rollback()
        db.refresh(server)
        raise


def request_quarantine_clear(
    db: Session,
    server: Server,
    *,
    operation_id: str,
) -> None:
    try:
        if str(uuid.UUID(operation_id)) != operation_id.lower():
            raise ValueError("operation_id must be a canonical UUID")
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("operation_id must be a canonical UUID") from exc

    current = None
    if server.guardian_quarantine_control:
        try:
            current = json.loads(server.guardian_quarantine_control)
        except Exception:
            pass

    if current and current.get("clear") is True and current.get("operation_id") == operation_id.lower():
        return

    try:
        server.guardian_quarantine_control = json.dumps(
            {"clear": True, "operation_id": operation_id.lower()},
            sort_keys=True,
            separators=(",", ":"),
        )
        server.desired_state_generation = int(server.desired_state_generation or 0) + 1
        db.commit()
        db.refresh(server)
    except Exception:
        db.rollback()
        db.refresh(server)
        raise
