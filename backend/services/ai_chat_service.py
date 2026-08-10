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

from fastapi import HTTPException
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


def truncate_from(db: Session, conversation: AiConversation, message: AiMessage) -> int:
    """Schneidet den Verlauf ab dieser Nachricht ab — sie selbst eingeschlossen.

    Das ist die Grundlage des Bearbeitens: wer eine Frage umformuliert, will
    nicht, dass die alte Fassung und die darauf gegebene Antwort weiter im
    Kontext stehen. Beides zusammen waere widerspruechlich, und das Modell
    wuerde die verworfene Fassung weiter beruecksichtigen.

    Abgeschnitten wird **alles Spaetere**, nicht nur die eine Antwort. Bearbeitet
    jemand die dritte von zehn Nachrichten, beruhen die Nachrichten vier bis
    zehn auf einer Praemisse, die es nicht mehr gibt.

    Was mitgeht und warum:

    - **Werkzeugergebnisse** — sie gehoeren zu den geloeschten Zuegen und wuerden
      sonst als "frueher gelesene Daten" in einen Kontext fliessen, in dem
      niemand mehr danach gefragt hat.
    - **Offene Vorschlaege** — eine Rueckfrage zu einer zurueckgenommenen Bitte
      ist gegenstandslos.

    Was **nicht** mitgeht: bereits ausgefuehrte Aktionen. Ein gestoppter Server
    bleibt gestoppt; den Verlauf umzuschreiben aendert daran nichts. Sie stehen
    unveraendert im Audit-Log — das ist der bestaendige Nachweis, der Chat ist
    eine Arbeitsflaeche.

    Wurde der Verlauf davor bereits gefaltet, bleibt die Zusammenfassung
    stehen: sie beschreibt aeltere Zuege, die von der Bearbeitung gar nicht
    betroffen sind.
    """
    from models import AiActionProposal, AiToolResult
    from services import ai_attachment_service

    cutoff = message.created_at
    # Erst die Kennungen holen, dann loeschen: danach ist nicht mehr
    # feststellbar, welche Anhaenge zu den verschwundenen Nachrichten gehoerten.
    # Blieben sie ungebunden liegen, gaelten sie als "noch nicht gesendet" und
    # haengten sich an die **naechste** Frage — eine Datei aus einer
    # zurueckgenommenen Bitte taucht dann in einem Zusammenhang wieder auf, in
    # dem sie niemand angefordert hat.
    betroffene = [
        row[0] for row in db.query(AiMessage.id).filter(
            AiMessage.conversation_id == conversation.id,
            AiMessage.created_at >= cutoff,
        ).all()
    ]
    ai_attachment_service.drop_for_messages(db, betroffene)
    removed = (
        db.query(AiMessage)
        .filter(
            AiMessage.conversation_id == conversation.id,
            AiMessage.created_at >= cutoff,
        )
        .delete(synchronize_session=False)
    )
    db.query(AiToolResult).filter(
        AiToolResult.conversation_id == conversation.id,
        AiToolResult.created_at >= cutoff,
    ).delete(synchronize_session=False)
    # Nur was noch niemand angefasst hat. Ein `executing` oder `succeeded`
    # beschreibt etwas, das in der Welt passiert ist.
    db.query(AiActionProposal).filter(
        AiActionProposal.conversation_id == conversation.id,
        AiActionProposal.created_at >= cutoff,
        AiActionProposal.status == "proposed",
    ).delete(synchronize_session=False)
    conversation.updated_at = datetime.now(timezone.utc)
    db.flush()
    return int(removed)


def owned_message(db: Session, conversation: AiConversation, message_id: str) -> AiMessage:
    """Laedt eine eigene Benutzernachricht dieser Unterhaltung.

    Nur eigene und nur `user`: eine Antwort des Modells umzuschreiben waere
    keine Korrektur, sondern eine Faelschung des Verlaufs — und damit auch des
    Kontexts, aus dem spaetere Antworten entstehen.
    """
    row = db.get(AiMessage, message_id)
    if row is None or row.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="Nachricht nicht gefunden")
    if row.role != "user":
        raise HTTPException(
            status_code=409, detail="Nur eigene Nachrichten lassen sich bearbeiten"
        )
    return row


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
