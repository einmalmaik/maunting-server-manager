"""Outbound-Webhook-Management (Panel -> Drittsystem).

Eine Subscription pro Server ist moeglich (Discord-Bot, Uptime-Monitor, etc.).
Der eigentliche Versand laeuft im Hintergrund (siehe outbound_webhook_service).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from dependencies import get_current_user, require_server_permission, verify_csrf
from models.server import Server
from models.user import User
from models.webhook_delivery import WebhookDelivery
from models.webhook_subscription import WebhookSubscription
from services import outbound_webhook_service as ow
from services.outbound_webhook_secret_store import delete as secret_store_delete
from services.outbound_webhook_secret_store import put as secret_store_put


router = APIRouter(prefix="/api/servers/{server_id}/webhooks", tags=["webhooks-out"])


# ── Pydantic-Schemas ────────────────────────────────────────────────────────


class SubscriptionCreate(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    target_url: str = Field(min_length=8, max_length=ow.MAX_TARGET_URL_LENGTH)
    event_filter: str | None = Field(default=None, max_length=512)


class SubscriptionUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    target_url: str | None = Field(default=None, max_length=ow.MAX_TARGET_URL_LENGTH)
    event_filter: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None


class SubscriptionItem(BaseModel):
    id: int
    label: str | None
    target_url: str
    secret_hint: str | None
    enabled: bool
    event_filter: str | None
    last_delivery_status: str | None
    last_delivery_at: datetime | None
    last_response_code: int | None


class SubscriptionList(BaseModel):
    items: list[SubscriptionItem]


class SubscriptionWithSecret(BaseModel):
    """Wird NUR bei enable / rotate_secret zurueckgegeben."""
    id: int
    label: str | None
    target_url: str
    secret: str
    event_filter: str | None


class TestRequest(BaseModel):
    event_type: str = Field(default=ow.EVENT_STATUS_CHANGE)


class TestResponse(BaseModel):
    delivery_id: int
    queued: bool


class DeliveryItem(BaseModel):
    id: int
    subscription_id: int
    event_type: str
    payload: dict[str, Any]
    payload_hash: str
    status: str
    response_code: int | None
    error: str | None
    attempt: int
    sent_at: datetime


class DeliveryList(BaseModel):
    items: list[DeliveryItem]


# ── Helper ──────────────────────────────────────────────────────────────────


def _server_or_404(db: Session, server_id: int, user: User) -> Server:
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    require_server_permission(user, server_id, db, "server.view")
    return server


def _require_update(db: Session, server_id: int, user: User) -> None:
    require_server_permission(user, server_id, db, "server.update")


def _subscription_to_item(s: WebhookSubscription) -> SubscriptionItem:
    # Wir geben die volle URL zurueck (User hat sie selbst eingetragen).
    # Sensitive Werte sind hier keine enthalten — secret_hash kommt NICHT in den
    # Response, secret_hint ist reine UX-Erinnerung (letzte 4 Zeichen).
    return SubscriptionItem(
        id=s.id,
        label=s.label,
        target_url=s.target_url,
        secret_hint=s.secret_hint,
        enabled=s.enabled,
        event_filter=s.event_filter,
        last_delivery_status=s.last_delivery_status,
        last_delivery_at=s.last_delivery_at,
        last_response_code=s.last_response_code,
    )


# ── Subscription-CRUD ───────────────────────────────────────────────────────


@router.get("", response_model=SubscriptionList)
def list_subscriptions(
    server_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server = _server_or_404(db, server_id, user)
    rows = (
        db.query(WebhookSubscription)
        .filter(WebhookSubscription.server_id == server.id)
        .order_by(WebhookSubscription.id.asc())
        .all()
    )
    return SubscriptionList(items=[_subscription_to_item(s) for s in rows])


@router.post("", response_model=SubscriptionWithSecret)
def create_subscription(
    server_id: int,
    payload: SubscriptionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    """Legt eine neue Subscription an und gibt das Klartext-Secret einmalig zurueck.

    Das Secret wird im In-Memory-Store abgelegt (siehe outbound_webhook_secret_store).
    """
    _server_or_404(db, server_id, user)
    _require_update(db, server_id, user)

    if not (payload.target_url.startswith("http://") or payload.target_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="target_url muss mit http(s):// beginnen")

    secret = ow.generate_secret()
    sub = WebhookSubscription(
        server_id=server_id,
        label=payload.label,
        target_url=payload.target_url,
        secret_hash=ow.hash_secret(secret),
        secret_hint=ow.secret_hint(secret),
        enabled=True,
        event_filter=payload.event_filter,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    secret_store_put(sub.id, secret)
    return SubscriptionWithSecret(
        id=sub.id,
        label=sub.label,
        target_url=sub.target_url,
        secret=secret,
        event_filter=sub.event_filter,
    )


@router.patch("/{sub_id}", response_model=SubscriptionItem)
def update_subscription(
    server_id: int,
    sub_id: int,
    payload: SubscriptionUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    _server_or_404(db, server_id, user)
    _require_update(db, server_id, user)
    sub = db.get(WebhookSubscription, sub_id)
    if sub is None or sub.server_id != server_id:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if payload.label is not None:
        sub.label = payload.label
    if payload.event_filter is not None:
        sub.event_filter = payload.event_filter
    if payload.target_url is not None:
        if not (payload.target_url.startswith("http://") or payload.target_url.startswith("https://")):
            raise HTTPException(status_code=400, detail="target_url muss mit http(s):// beginnen")
        sub.target_url = payload.target_url
    if payload.enabled is not None:
        sub.enabled = payload.enabled
    db.commit()
    db.refresh(sub)
    return _subscription_to_item(sub)


@router.post("/{sub_id}/rotate", response_model=SubscriptionWithSecret)
def rotate_secret(
    server_id: int,
    sub_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    _server_or_404(db, server_id, user)
    _require_update(db, server_id, user)
    sub = db.get(WebhookSubscription, sub_id)
    if sub is None or sub.server_id != server_id:
        raise HTTPException(status_code=404, detail="Subscription not found")

    secret = ow.generate_secret()
    sub.secret_hash = ow.hash_secret(secret)
    sub.secret_hint = ow.secret_hint(secret)
    db.commit()
    db.refresh(sub)
    secret_store_put(sub.id, secret)
    return SubscriptionWithSecret(
        id=sub.id,
        label=sub.label,
        target_url=sub.target_url,
        secret=secret,
        event_filter=sub.event_filter,
    )


@router.delete("/{sub_id}", response_model=dict)
def delete_subscription(
    server_id: int,
    sub_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    _server_or_404(db, server_id, user)
    _require_update(db, server_id, user)
    sub = db.get(WebhookSubscription, sub_id)
    if sub is None or sub.server_id != server_id:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db.delete(sub)
    db.commit()
    secret_store_delete(sub_id)
    return {"deleted": True}


# ── Test-Send (manueller Trigger ohne Server-Aktion) ─────────────────────────


@router.post("/{sub_id}/test", response_model=TestResponse)
async def test_send(
    server_id: int,
    sub_id: int,
    payload: TestRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    """Sendet ein synthetisches Event an die Subscription.

    Damit kann der User sofort pruefen ob sein Endpoint erreichbar ist,
    ohne erst auf eine echte Status-Aenderung zu warten.
    """
    server = _server_or_404(db, server_id, user)
    _require_update(db, server_id, user)
    sub = db.get(WebhookSubscription, sub_id)
    if sub is None or sub.server_id != server_id:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if payload.event_type not in ow.EVENT_KNOWN_TYPES:
        # Erlaubt auch custom, aber dann dokumentieren wir event_type klar.
        pass

    payload_body = ow.build_status_payload(server)
    delivery_ids = await ow.dispatch_event(
        db, server=server, event_type=payload.event_type, payload=payload_body,
    )
    return TestResponse(delivery_id=delivery_ids[0] if delivery_ids else 0, queued=bool(delivery_ids))


# ── Live-Feed (Audit/Debug) ──────────────────────────────────────────────────


@router.get("/deliveries", response_model=DeliveryList)
def list_deliveries(
    server_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    sub_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server = _server_or_404(db, server_id, user)
    q = db.query(WebhookDelivery).filter(WebhookDelivery.server_id == server.id)
    if sub_id is not None:
        q = q.filter(WebhookDelivery.subscription_id == sub_id)
    rows = q.order_by(WebhookDelivery.sent_at.desc()).limit(limit).all()
    items: list[DeliveryItem] = []
    for r in rows:
        try:
            payload_dict = json.loads(r.payload) if r.payload else {}
        except json.JSONDecodeError:
            payload_dict = {}
        items.append(
            DeliveryItem(
                id=r.id,
                subscription_id=r.subscription_id,
                event_type=r.event_type,
                payload=payload_dict,
                payload_hash=r.payload_hash,
                status=r.status,
                response_code=r.response_code,
                error=r.error,
                attempt=r.attempt,
                sent_at=r.sent_at,
            )
        )
    ow.enforce_retention(db, server.id)
    db.commit()
    return DeliveryList(items=items)
