"""Administrator-controlled Guardian operations."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission, verify_csrf
from models import AuditLog, ChangeEvent, Server, User
from services.guardian_state_service import request_quarantine_clear
from services.server_lifecycle_service import sync_desired_state_to_agent


router = APIRouter(prefix="/api/servers/{server_id}/guardian", tags=["guardian"])


@router.post("/quarantine/clear", status_code=202)
def clear_quarantine(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    require_server_permission(user, server_id, db, "server.restart")
    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    import uuid
    operation_id = str(uuid.uuid4())
    request_quarantine_clear(db, server, operation_id=operation_id)
    db.add(
        AuditLog(
            user_id=user.id,
            action="guardian.quarantine.clear",
            target_type="server",
            target_id=server.id,
            details=f"operation_id={operation_id}",
        )
    )
    db.add(
        ChangeEvent(
            server_id=server.id,
            event_type="guardian_quarantine_clear",
            description="Guardian-Quarantäne wurde zur Freigabe angefordert.",
            details=json.dumps(
                {"operation_id": operation_id},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(server)
    synchronized = sync_desired_state_to_agent(db, server)
    return {
        "ok": True,
        "operation_id": operation_id,
        "generation": server.desired_state_generation,
        "synchronized": synchronized,
    }

