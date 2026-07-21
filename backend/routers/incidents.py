import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission
from models import Incident, User
from services.change_timeline_service import log_change_event

router = APIRouter(prefix="/api/servers/{server_id}/incidents", tags=["incidents"])


@router.get("")
def list_incidents(
    server_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_server_permission(user, server_id, db, "server.view")
    incidents = (
        db.query(Incident)
        .filter(Incident.server_id == server_id)
        .order_by(Incident.created_at.desc())
        .all()
    )
    res = []
    for inc in incidents:
        attempts_list = []
        if inc.attempts:
            try:
                attempts_list = json.loads(inc.attempts)
            except Exception:
                attempts_list = []
        res.append({
            "id": inc.id,
            "title": inc.title,
            "description": inc.description,
            "type": inc.type,
            "status": inc.status,
            "fingerprint": inc.fingerprint,
            "created_at": inc.created_at,
            "resolved_at": inc.resolved_at,
            "attempts": attempts_list,
        })
    return res


@router.post("/{inc_id}/resolve")
def resolve_incident(
    server_id: int,
    inc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_server_permission(user, server_id, db, "server.start")
    incident = (
        db.query(Incident)
        .filter(Incident.id == inc_id, Incident.server_id == server_id)
        .first()
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident nicht gefunden")

    import uuid
    from models import Server
    from services.guardian_state_service import request_quarantine_clear

    incident.status = "resolved"
    incident.resolved_at = datetime.now(timezone.utc)

    server = db.query(Server).filter(Server.id == server_id).first()
    if server:
        if server.guardian_observed_state == "quarantined" or incident.status == "quarantined":
            try:
                request_quarantine_clear(db, server, operation_id=str(uuid.uuid4()))
            except Exception:
                pass
            server.guardian_observed_state = "healthy"
        server.guardian_sync_error_statistics = None

    log_change_event(
        db,
        server_id,
        "recovery",
        f"Incident '{incident.title}' manuell als gelöst markiert.",
    )

    db.commit()
    return {"ok": True}
