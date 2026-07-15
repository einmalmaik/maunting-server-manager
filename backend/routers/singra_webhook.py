"""Public inbound webhook for Singra support widget tickets."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from middleware.rate_limit import limiter
from services.singra_webhook_handler import (
    already_processed,
    handle_verified_payload,
    mark_processed,
    parse_json_payload,
    verify_request,
)

router = APIRouter(prefix="/api", tags=["singra-webhook"])


@router.post("/singra-webhook")
@limiter.limit("60/minute")
async def singra_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    raw = await request.body()
    timestamp = request.headers.get("x-singra-timestamp")
    event_id = request.headers.get("x-singra-event-id")
    event_type = request.headers.get("x-singra-event-type")
    signature = request.headers.get("x-singra-signature")

    err = verify_request(raw, timestamp, signature)
    if err == "secret_not_configured":
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    if err:
        raise HTTPException(status_code=401, detail=err)

    if already_processed(db, event_id):
        return {"ok": True, "deduped": True}

    try:
        payload = parse_json_payload(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    await handle_verified_payload(
        db,
        event_type=event_type or payload.get("event"),
        payload=payload,
    )
    mark_processed(db, event_id)
    return {"ok": True}