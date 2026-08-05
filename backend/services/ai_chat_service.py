"""Ownership- und Recovery-Regeln fuer persistente AI-Gespraeche."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, AiUsageEvent, Server, User
from services.ai_usage_service import complete_ai_usage
from services.permission_service import has_server_permission


def canonical_uuid(value: str | UUID) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def get_owned_conversation(
    db: Session,
    conversation_id: str,
    user: User,
) -> AiConversation | None:
    """Fremde oder inzwischen unberechtigte Chats bleiben als 404 verborgen."""
    canonical = canonical_uuid(conversation_id)
    if canonical is None:
        return None
    conversation = (
        db.query(AiConversation)
        .filter(
            AiConversation.id == canonical,
            AiConversation.user_id == user.id,
        )
        .first()
    )
    if conversation is None:
        return None
    if conversation.server_id is not None and not has_server_permission(
        db, user, conversation.server_id, "server.view"
    ):
        return None
    return conversation


def create_conversation(
    db: Session,
    *,
    user: User,
    title: str,
    server_id: int | None,
) -> AiConversation:
    if server_id is not None:
        if db.get(Server, server_id) is None or not has_server_permission(
            db, user, server_id, "server.view"
        ):
            raise LookupError("Server nicht gefunden")
    conversation = AiConversation(
        id=str(uuid4()),
        user_id=user.id,
        server_id=server_id,
        title=title.strip(),
    )
    db.add(conversation)
    db.flush()
    return conversation


def list_conversations(
    db: Session,
    *,
    user: User,
    server_id: int | None,
) -> list[AiConversation]:
    if server_id is not None and (
        db.get(Server, server_id) is None
        or not has_server_permission(db, user, server_id, "server.view")
    ):
        return []
    query = db.query(AiConversation).filter(AiConversation.user_id == user.id)
    if server_id is not None:
        query = query.filter(AiConversation.server_id == server_id)
    else:
        # Globale und serverbezogene Arbeitsraeume bleiben strikt getrennt.
        query = query.filter(AiConversation.server_id.is_(None))
    return query.order_by(AiConversation.updated_at.desc()).limit(100).all()


def reconcile_interrupted_ai_streams(db: Session) -> int:
    """Beendet nach Neustart unbestaetigte Streams ohne Quotenfreigabe.

    Bei einem Prozessabbruch ist unbekannt, wie viele Provider-Tokens bereits
    verbraucht wurden. Daher wird konservativ die Reservierung abgerechnet,
    statt sie als kostenlos fehlgeschlagen zu markieren.
    """
    rows = db.query(AiMessage).filter(AiMessage.status == "streaming").all()
    now = datetime.now(timezone.utc)
    for message in rows:
        message.status = "failed"
        if message.request_id:
            event = (
                db.query(AiUsageEvent)
                .filter(AiUsageEvent.request_id == message.request_id)
                .first()
            )
            if event is not None and event.status == "reserved":
                complete_ai_usage(
                    db,
                    event,
                    actual_tokens=event.reserved_tokens,
                    actual_cost_microunits=event.reserved_cost_microunits,
                    now=now,
                )
    if rows:
        db.commit()
    return len(rows)
