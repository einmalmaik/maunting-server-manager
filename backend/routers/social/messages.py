"""KISS-Router für den E2EE-Nachrichtenversand und die FIFO-Zustellung.

Endpunkte:
- POST /api/social/messages/send   -> Nachricht für Zielgeräte einreihen (-> 1 Häkchen ✓)
- GET  /api/social/messages/pending -> Anstehende Chiffrate des eigenen Geräts abholen
- POST /api/social/messages/ack    -> Abgeholte Chiffrate quittieren (-> 2 Häkchen grau ✓✓)
- POST /api/social/messages/read   -> Gelesene Nachrichten melden (-> 2 Häkchen blau ✓✓)
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, verify_csrf
from models import User, UserE2eeDevice
from routers.social.basis import _check_social_enabled
from services.social.queue_service import E2eeQueueService

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(_check_social_enabled)])


class MessageEnvelopeItem(BaseModel):
    target_device_id: int | None = None
    device_id: str | None = None
    encrypted_payload: str


class SendMessageRequest(BaseModel):
    client_uuid: str
    chat_type: str = Field(..., pattern="^(direct|group)$")
    chat_id: str
    envelopes: list[MessageEnvelopeItem]


class AckMessagesRequest(BaseModel):
    queue_ids: list[int]


class ReadReceiptRequest(BaseModel):
    chat_type: str = Field(..., pattern="^(direct|group)$")
    chat_id: str
    client_uuids: list[str]


DeviceIdHeader = Annotated[str | None, Header(alias="X-Device-Id")]


def _resolve_device(db: Session, user: User, device_id: str | None) -> UserE2eeDevice:
    if not device_id:
        raise HTTPException(status_code=400, detail="X-Device-Id Header fehlt.")
    dev = (
        db.query(UserE2eeDevice)
        .filter(
            UserE2eeDevice.user_id == user.id,
            UserE2eeDevice.device_id == device_id,
        )
        .first()
    )
    if not dev:
        raise HTTPException(status_code=404, detail="Gerät nicht registriert.")
    if not dev.is_approved:
        raise HTTPException(status_code=403, detail="Gerät ist nicht freigegeben.")
    return dev


@router.post("/messages/send", dependencies=[Depends(verify_csrf)])
def send_message(
    req: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    device_id: DeviceIdHeader = None,
) -> dict:
    """Reiht eine verschlüsselte Nachricht für alle Zielgeräte ein."""
    sender_device_id: int | None = None
    if device_id:
        dev = (
            db.query(UserE2eeDevice)
            .filter(
                UserE2eeDevice.user_id == current_user.id,
                UserE2eeDevice.device_id == device_id,
            )
            .first()
        )
        if dev:
            sender_device_id = dev.id

    envelopes_data = [
        {
            "target_device_id": e.target_device_id,
            "device_id": e.device_id,
            "encrypted_payload": e.encrypted_payload,
        }
        for e in req.envelopes
    ]

    created_ids = E2eeQueueService.enqueue_message(
        db=db,
        sender=current_user,
        envelopes=envelopes_data,
        client_uuid=req.client_uuid,
        chat_type=req.chat_type,
        chat_id=req.chat_id,
        sender_device_id=sender_device_id,
    )

    return {
        "status": "ok",
        "client_uuid": req.client_uuid,
        "enqueued": len(created_ids),
    }


@router.get("/messages/pending")
def get_pending_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    device_id: DeviceIdHeader = None,
    query_device_id: str | None = Query(None, alias="device_id"),
) -> dict:
    """Liefert alle anstehenden Chiffrate für dieses Gerät."""
    target_dev_id = device_id or query_device_id
    dev = _resolve_device(db, current_user, target_dev_id)
    messages = E2eeQueueService.fetch_pending_messages(db, dev)
    return {"messages": messages}


@router.post("/messages/ack", dependencies=[Depends(verify_csrf)])
def acknowledge_messages(
    req: AckMessagesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    device_id: DeviceIdHeader = None,
    query_device_id: str | None = Query(None, alias="device_id"),
) -> dict:
    """Quittiert verarbeitete Chiffrate und löscht sie aus der Queue."""
    target_dev_id = device_id or query_device_id
    dev = _resolve_device(db, current_user, target_dev_id)
    deleted = E2eeQueueService.acknowledge_messages(db, dev, req.queue_ids)
    return {"status": "ok", "acknowledged": deleted}


@router.post("/messages/read", dependencies=[Depends(verify_csrf)])
def mark_read(
    req: ReadReceiptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Sendet Lesebestätigungen an die Gesprächspartner."""
    E2eeQueueService.mark_chat_read(
        db=db,
        reader=current_user,
        chat_type=req.chat_type,
        chat_id=req.chat_id,
        client_uuids=req.client_uuids,
    )
    return {"status": "ok"}
