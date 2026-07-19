import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission
from models import ChangeEvent, User

router = APIRouter(prefix="/api/servers/{server_id}/change-timeline", tags=["change-timeline"])


@router.get("")
def list_change_timeline(
    server_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_server_permission(user, server_id, db, "server.view")
    events = (
        db.query(ChangeEvent)
        .filter(ChangeEvent.server_id == server_id)
        .order_by(ChangeEvent.timestamp.desc())
        .all()
    )
    res = []
    for ev in events:
        details_dict = None
        if ev.details:
            try:
                details_dict = json.loads(ev.details)
            except Exception:
                details_dict = None
        res.append({
            "id": ev.id,
            "timestamp": ev.timestamp,
            "event_type": ev.event_type,
            "description": ev.description,
            "details": details_dict,
        })
    return res
