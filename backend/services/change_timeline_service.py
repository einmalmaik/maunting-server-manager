import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models.change_event import ChangeEvent


def log_change_event(
    db: Session,
    server_id: int,
    event_type: str,
    description: str,
    details: dict | None = None,
) -> ChangeEvent:
    """Idempotently logs a correlated action/config change event to the server timeline."""
    event = ChangeEvent(
        server_id=server_id,
        timestamp=datetime.now(timezone.utc),
        event_type=event_type,
        description=description,
        details=json.dumps(details) if details else None,
    )
    db.add(event)
    try:
        db.commit()
        db.refresh(event)
    except Exception:
        db.rollback()
        raise
    return event
