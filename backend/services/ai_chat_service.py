"""Ownership- und Recovery-Regeln fuer die eine persistente AI-Unterhaltung.

Jeder Benutzer hat genau einen Chat. Es gibt keinen Weg, einen zweiten
anzulegen: der Assistent soll wie ein Gespraechspartner funktionieren, nicht wie
eine Ablage, in der man erst den richtigen Ordner suchen muss. Der Serverbezug
haengt am einzelnen Werkzeugaufruf (`ai_action_service._resolve_server`) und
nicht mehr an der Unterhaltung.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, AiUsageEvent, User
from services.ai_usage_service import complete_ai_usage


DEFAULT_TITLE = "KI-Assistent"


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
    """Fremde Chats bleiben als 404 verborgen."""
    canonical = canonical_uuid(conversation_id)
    if canonical is None:
        return None
    return (
        db.query(AiConversation)
        .filter(
            AiConversation.id == canonical,
            AiConversation.user_id == user.id,
        )
        .first()
    )


def get_or_create_primary_conversation(db: Session, user: User) -> AiConversation:
    """Liefert die eine Unterhaltung des Benutzers und legt sie beim ersten Mal an.

    Der eindeutige Index auf ``user_id`` ist die eigentliche Zusicherung. Zwei
    gleichzeitige erste Aufrufe (zwei Browsertabs) rennen sonst in dieselbe
    Luecke zwischen Pruefung und Insert; der Verlierer liest die Zeile des
    Gewinners.
    """
    existing = (
        db.query(AiConversation).filter(AiConversation.user_id == user.id).first()
    )
    if existing is not None:
        return existing

    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title=DEFAULT_TITLE
    )
    try:
        with db.begin_nested():
            db.add(conversation)
            db.flush()
    except IntegrityError:
        conversation = (
            db.query(AiConversation).filter(AiConversation.user_id == user.id).one()
        )
    return conversation


def clear_history(db: Session, conversation: AiConversation) -> int:
    """Loescht den Verlauf, behaelt aber die Unterhaltung selbst.

    Die Unterhaltung bleibt, weil sie die Identitaet des Chats ist — an ihr
    haengen laufende Vorschlaege und die Idempotenz der Anfragen. Geloescht wird,
    was der Benutzer sieht: Nachrichten, Werkzeugergebnisse und die
    Zusammenfassung. Bereits ausgefuehrte Aktionen bleiben im Audit; ein
    Chatverlauf ist kein Loeschknopf fuer die Nachvollziehbarkeit.
    """
    from models import AiToolResult

    removed = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == conversation.id)
        .delete(synchronize_session=False)
    )
    db.query(AiToolResult).filter(
        AiToolResult.conversation_id == conversation.id
    ).delete(synchronize_session=False)
    conversation.summary = None
    conversation.summarized_until = None
    conversation.updated_at = datetime.now(timezone.utc)
    db.flush()
    return int(removed)


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
