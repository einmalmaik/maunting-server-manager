"""Ownership- und Recovery-Regeln fuer die persistenten AI-Unterhaltungen.

Fuer die `EINZELFENSTER`-Arten hat jeder Benutzer genau einen Chat je Anlass:
der Mensch tippt (`primary`), oder eine Guardian-Stoerung weckt die KI
(`guardian`). Es gibt weiterhin keinen Weg, sich einen weiteren solchen Chat
anzulegen — der Assistent soll wie ein Gespraechspartner funktionieren, nicht
wie eine Ablage, in der man erst den richtigen Ordner suchen muss.

`worker`-Fenster sind die Ausnahme mit eigener Fabrik
(`worker_unterhaltung_anlegen`): ein Fenster je deklariertem Auftrag, mehrere
gleichzeitig (docs/agentic-framework.md, v3).

Warum die Trennung ueberhaupt sein muss, steht am Modell: solange beides in
derselben Zeile stand, schlossen sich Reparatur und Gespraech gegenseitig aus.

Der Serverbezug haengt am einzelnen Werkzeugaufruf
(`ai_action_service._resolve_server`) und nicht an der Unterhaltung — auch nicht
an der Guardian-Unterhaltung.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, AiUsageEvent, User
from models.ai_conversation import ARTEN, EINZELFENSTER
from services.ai_usage_service import complete_ai_usage


DEFAULT_TITLE = "KI-Assistent"

#: Der Titel je Art. Die Spalte gab es laengst und niemand las sie; jetzt traegt
#: sie das, was die Oberflaeche ueber ein Fenster schreibt.
TITEL = {
    "primary": DEFAULT_TITLE,
    "guardian": "Guardian-Reparaturen",
}


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


def get_or_create_conversation(
    db: Session, user: User, kind: str = "primary"
) -> AiConversation:
    """Liefert die Unterhaltung dieser Art und legt sie beim ersten Mal an.

    Der eindeutige Index auf ``(user_id, kind)`` ist die eigentliche
    Zusicherung. Zwei gleichzeitige erste Aufrufe (zwei Browsertabs) rennen
    sonst in dieselbe Luecke zwischen Pruefung und Insert; der Verlierer liest
    die Zeile des Gewinners.

    **Der Wiedereinstieg nach der `IntegrityError` filtert auf `kind` mit.** Ohne
    das waere er falsch, seit es mehr als eine Zeile je Benutzer gibt: legt ein
    Browsertab den Dauerchat an, waehrend der Takt die Guardian-Zeile anlegt,
    schlaegt eine der beiden Einfuegungen fehl — und ein `.one()` ohne `kind`
    faende danach zwei Zeilen und riefe `MultipleResultsFound`, oder schlimmer,
    ein `.first()` lieferte die falsche. Ein Reparaturlauf, der in den Dauerchat
    schreibt, ist genau der Zustand, den diese Aenderung beseitigt.

    ``kind`` wird gegen `EINZELFENSTER` geprueft, obwohl die Datenbank einen
    CHECK traegt: ein Tippfehler soll hier auffallen und nicht als
    Integritaetsverletzung mitten in einer fremden Transaktion. ``worker`` ist
    hier bewusst ebenfalls ein Fehler: get-or-create setzt Eindeutigkeit
    voraus — ``.first()`` gaebe irgendeinen Worker zurueck und der
    Wiedereinstieg wuerfe ``MultipleResultsFound``. Wer ein Worker-Fenster
    braucht, ruft `worker_unterhaltung_anlegen`.
    """
    if kind not in EINZELFENSTER:
        if kind in ARTEN:
            raise ValueError(
                f"Unterhaltungsart {kind!r} ist kein Einzelfenster — "
                "worker_unterhaltung_anlegen benutzen"
            )
        raise ValueError(f"Unbekannte Unterhaltungsart: {kind!r}")

    existing = (
        db.query(AiConversation)
        .filter(AiConversation.user_id == user.id, AiConversation.kind == kind)
        .first()
    )
    if existing is not None:
        return existing

    conversation = AiConversation(
        id=str(uuid4()),
        user_id=user.id,
        server_id=None,
        kind=kind,
        title=TITEL.get(kind, DEFAULT_TITLE),
    )
    try:
        with db.begin_nested():
            db.add(conversation)
            db.flush()
    except IntegrityError:
        conversation = (
            db.query(AiConversation)
            .filter(AiConversation.user_id == user.id, AiConversation.kind == kind)
            .one()
        )
    return conversation


def worker_unterhaltung_anlegen(db: Session, user: User, titel: str) -> AiConversation:
    """Legt das Fenster eines neuen Worker-Auftrags an — immer eine neue Zeile.

    Bewusst kein get-or-create: mehrere gleichzeitige Auftraege sind der Zweck
    der Art ``worker``, der eindeutige Index nimmt sie aus. Der Titel kommt aus
    ``worker_start(titel)`` und macht das Fenster in der Liste erkennbar; die
    Kappe (wie viele Auftraege gleichzeitig) liegt beim Aufrufer und beim
    Betreiber-Deckel, nicht hier.
    """
    # Der Titel kommt aus einer Modellausgabe und geht später ungefiltert an
    # die Oberfläche (Worker-Leiste, Fensterkopf) — geschwärzt wird deshalb
    # hier beim Anlegen, nicht bei jedem Leser einzeln.
    from services.ai_redaction import redact_sensitive_text

    fenster = AiConversation(
        id=str(uuid4()),
        user_id=user.id,
        server_id=None,
        kind="worker",
        title=redact_sensitive_text(titel or "Auftrag").strip()[:160] or "Auftrag",
    )
    db.add(fenster)
    db.flush()
    return fenster


def get_or_create_primary_conversation(db: Session, user: User) -> AiConversation:
    """Der Dauerchat. Duenner Wrapper, damit die vorhandenen Aufrufer bleiben.

    Ein Dutzend Stellen fragt nach "der" Unterhaltung und meint dabei immer den
    Dauerchat. Sie alle in einem Zug umzuschreiben hiesse, an jeder einzelnen zu
    entscheiden, ob sie es wirklich meint — und eine davon falsch zu
    beantworten. Wer eine andere Art braucht, ruft `get_or_create_conversation`
    und sagt welche.
    """
    return get_or_create_conversation(db, user, "primary")


def clear_history(db: Session, conversation: AiConversation) -> int:
    """Loescht den Verlauf, behaelt aber die Unterhaltung selbst.

    Die Unterhaltung bleibt, weil sie die Identitaet des Chats ist.
    Geloescht wird, was der Benutzer sieht: Nachrichten, Werkzeugergebnisse,
    Aktionsvorschlaege, Anhaenge und die Zusammenfassung. Bereits ausgefuehrte
    Aktionen bleiben im Audit (audit_logs); die fliegenden Vorschlagskarten
    des Chats werden mit dem Verlauf abgeraeumt, damit kein verwaister
    Zustand im leeren Chat stehenbleibt. Auch Worker-Unterhaltungen und alle
    zugehoerigen Vorschlaege des Benutzers werden restlos bereinigt.
    """
    from models import (
        AiActionProposal,
        AiAttachment,
        AiConversation,
        AiMeldung,
        AiToolResult,
    )
    from services import ai_run_service

    worker_conv_ids = [
        row[0]
        for row in db.query(AiConversation.id)
        .filter(
            AiConversation.user_id == conversation.user_id,
            AiConversation.kind == "worker",
        )
        .all()
    ]
    alle_conv_ids = [conversation.id, *worker_conv_ids]

    for cid in alle_conv_ids:
        ai_run_service.vorgaenger_abloesen(db, conversation_id=cid)

    db.query(AiMeldung).filter(AiMeldung.user_id == conversation.user_id).delete(
        synchronize_session=False
    )

    removed = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id.in_(alle_conv_ids))
        .delete(synchronize_session=False)
    )
    db.query(AiToolResult).filter(
        AiToolResult.conversation_id.in_(alle_conv_ids)
    ).delete(synchronize_session=False)
    db.query(AiAttachment).filter(
        AiAttachment.conversation_id.in_(alle_conv_ids)
    ).delete(synchronize_session=False)
    db.query(AiActionProposal).filter(
        (AiActionProposal.conversation_id.in_(alle_conv_ids))
        | (AiActionProposal.user_id == conversation.user_id)
    ).delete(synchronize_session=False)

    if worker_conv_ids:
        db.query(AiConversation).filter(
            AiConversation.id.in_(worker_conv_ids)
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
