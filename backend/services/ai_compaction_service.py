"""Faltet den aelteren Teil eines langen Chats zu einer Zusammenfassung.

Der Assistent hat genau **eine** Unterhaltung, die nie endet. Ohne diesen
Schritt hiess "lang" schlicht "abgeschnitten": `build_provider_messages` nahm
die letzten 20 Nachrichten bzw. 24.000 Zeichen und liess den Rest weg — ohne
Hinweis, ohne Ersatz. Nach ein paar Dutzend Nachrichten wusste die KI nicht
mehr, worum es am Anfang ging, behauptete aber weiterhin, den Verlauf zu kennen.

Drei Entscheidungen, die den Aufbau erklaeren:

**Danach, nicht davor.** Die Kompression laeuft, nachdem eine Antwort fertig
gestreamt wurde. Der Benutzer wartet nie darauf. Der Preis ist, dass die
*aktuelle* Anfrage noch die alte, gekuerzte Historie sah — das ist verkraftbar,
weil die Kuerzung ohnehin erst greift, wenn deutlich mehr Material da ist, als
in eine Anfrage passt.

**Eigene Verbrauchsbuchung.** Die Zusammenfassung ist ein echter Providerruf
und kostet Tokens. Sie bekommt eine eigene Request-ID und laeuft durch dieselbe
Kontingentpruefung wie jede andere Anfrage. Ein unsichtbarer Verbrauch waere
genau das, was Zielpunkt 6 verhindern soll.

**Fehler sind folgenlos.** Scheitert die Kompression, bleibt der Chat wie er
ist: `summarized_until` wird nicht gesetzt, beim naechsten Mal wird es erneut
versucht. Eine misslungene Zusammenfassung darf niemals Nachrichten aus dem
Kontext entfernen.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import UUID, uuid4

import httpx

from database import SessionLocal
from models import AiConversation, AiMessage, AiProvider, User
# `_message_content_for_provider` ist bewusst dieselbe Uebersetzung, die auch
# `build_provider_messages` benutzt. Bei einer Rueckfrage steht der Text nicht
# in `content`, sondern in `question_json`; wer hier `row.content` liest, faltet
# ein leeres "Assistent:" ein, und die Antwort "den zweiten" verliert ihren
# Bezug — die Originalnachrichten liegen danach hinter `summarized_until`.
# Der fuehrende Unterstrich bleibt: die Funktion gehoert dem Kontextaufbau, die
# Verdichtung borgt sie sich nur, statt eine zweite Fassung zu pflegen.
from services.ai_context_service import (
    MAX_SUMMARY_CHARS,
    _message_content_for_provider,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_provider_service import estimate_cost_microunits, resolve_api_key
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    complete_ai_usage,
    fail_ai_usage,
    reserve_ai_usage,
)
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


logger = logging.getLogger(__name__)

# Ab wann ueberhaupt gefaltet wird. Bewusst deutlich ueber dem Kontextbudget
# (24.000 Zeichen): darunter passt alles hinein und es gaebe nichts zu sparen.
COMPACTION_THRESHOLD_CHARS = 40_000
# So viel bleibt nach dem Falten woertlich stehen. Der juengste Teil eines
# Gespraechs traegt den aktuellen Faden; ihn zusammenzufassen waere der
# schaedlichste Teil der Uebung.
KEEP_RECENT_MESSAGES = 12
# Obergrenze fuer das, was in einem Rutsch zusammengefasst wird.
MAX_SOURCE_CHARS = 60_000

SUMMARY_PROMPT = (
    "Fasse den folgenden Gespraechsverlauf zwischen einem Benutzer und dem "
    "MSM-Serverassistenten zusammen. Schreibe hoechstens 15 Saetze in der "
    "Sprache des Gespraechs.\n"
    "Behalte: welche Server und Spiele besprochen wurden, welche Probleme "
    "auftraten, welche Ursachen gefunden wurden, welche Aktionen ausgefuehrt "
    "oder abgelehnt wurden, und offene Punkte.\n"
    "Lass weg: Hoeflichkeitsfloskeln, Wiederholungen, roh zitierte Logzeilen "
    "und Konfigurationsinhalte.\n"
    "Nenne niemals Passwoerter, Schluessel oder Tokens.\n"
    "Gib nur die Zusammenfassung aus, ohne Einleitung."
)


def needs_compaction(db, conversation: AiConversation) -> bool:
    """Prueft ohne Providerruf, ob sich das Falten ueberhaupt lohnt."""
    rows = _pending_messages(db, conversation)
    if len(rows) <= KEEP_RECENT_MESSAGES:
        return False
    foldable = rows[: len(rows) - KEEP_RECENT_MESSAGES]
    # Gemessen wird der Text, den der Anbieter spaeter tatsaechlich saehe. Mit
    # `row.content` zaehlte eine Rueckfrage als 0 Zeichen, obwohl sie mitsamt
    # ihren Vorschlaegen in die Zusammenfassung geht — die Schwelle waere je
    # nach Gespraechsverlauf deutlich zu spaet erreicht. Redigiert wird hier
    # nicht: das kostet Rechenzeit bei jedem Streamende und aendert an der
    # Laenge praktisch nichts.
    return sum(
        len(_message_content_for_provider(row)) for row in foldable
    ) >= COMPACTION_THRESHOLD_CHARS


def _pending_messages(db, conversation: AiConversation) -> list[AiMessage]:
    """Alle noch nicht zusammengefassten, vollstaendigen Nachrichten."""
    query = db.query(AiMessage).filter(
        AiMessage.conversation_id == conversation.id,
        AiMessage.status == "complete",
    )
    if conversation.summarized_until is not None:
        query = query.filter(AiMessage.created_at > conversation.summarized_until)
    return query.order_by(AiMessage.created_at.asc()).all()


def _foldable_window(foldable: list[AiMessage]) -> tuple[list[AiMessage], str]:
    """Waehlt die aeltesten Nachrichten, die zusammen in MAX_SOURCE_CHARS passen.

    Frueher wurde der *gesamte* faltbare Bereich zu einem Text verkettet und
    dann mit `[-MAX_SOURCE_CHARS:]` am Anfang abgeschnitten, waehrend
    `summarized_until` trotzdem bis zur juengsten faltbaren Nachricht wanderte.
    Was der Schnitt weggeworfen hatte, stand danach weder in der Zusammenfassung
    noch in der Historie — es war still verschwunden. Deshalb entscheidet jetzt
    erst das Fenster, was gefaltet wird, und die Grenze folgt dem Fenster statt
    umgekehrt.

    Gefaltet wird von vorne, also chronologisch. Nur so liegt der Ueberhang
    *hinter* der Grenze und bleibt im Kontext; er kommt beim naechsten Durchlauf
    an die Reihe und haengt sich an die dann bestehende Zusammenfassung an.
    Genau das meint der Kommentar bei MAX_SOURCE_CHARS mit "in einem Rutsch".

    Die erste Nachricht kommt immer mit, notfalls gekuerzt. Ohne diese Ausnahme
    wuerde eine einzelne uebergrosse Nachricht das Falten dauerhaft blockieren:
    die Schwelle waere ueberschritten, das Fenster aber leer, und der Chat
    wuechse ohne Gegenwehr weiter.
    """
    fenster: list[AiMessage] = []
    zeilen: list[str] = []
    laenge = 0
    for row in foldable:
        sprecher = "Benutzer" if row.role == "user" else "Assistent"
        zeile = (
            f"{sprecher}: "
            f"{redact_sensitive_text(_message_content_for_provider(row))}"
        )
        if fenster and laenge + len(zeile) + 1 > MAX_SOURCE_CHARS:
            break
        zeile = zeile[:MAX_SOURCE_CHARS]
        fenster.append(row)
        zeilen.append(zeile)
        laenge += len(zeile) + 1
    return fenster, "\n".join(zeilen)


async def compact_conversation(
    *, client: httpx.AsyncClient, user_id: int, conversation_id: str, provider_id: int
) -> bool:
    """Faltet den aelteren Teil zusammen. Liefert True, wenn etwas passiert ist.

    Laeuft ausdruecklich mit eigenen, kurzen DB-Sitzungen: der Aufrufer ist ein
    bereits beendeter Stream, und waehrend des Providerrufs darf keine
    Transaktion offen stehen.
    """
    with SessionLocal() as db:
        conversation = db.get(AiConversation, conversation_id)
        user = db.get(User, user_id)
        provider = db.get(AiProvider, provider_id)
        if conversation is None or user is None or not user.is_active:
            return False
        if provider is None or not provider.enabled:
            return False
        if not needs_compaction(db, conversation):
            return False

        rows = _pending_messages(db, conversation)
        foldable = rows[: len(rows) - KEEP_RECENT_MESSAGES]
        window, transcript = _foldable_window(foldable)
        # Die Grenze steht auf der letzten *tatsaechlich uebertragenen*
        # Nachricht. Stuende sie wie vorher auf `foldable[-1]`, waere alles, was
        # nicht mehr ins Fenster passte, aus dem Kontext gefiltert, ohne je in
        # einer Zusammenfassung gelandet zu sein. `window` ist nie leer:
        # `needs_compaction` hat bereits mehr als KEEP_RECENT_MESSAGES
        # Nachrichten gesehen, und die erste kommt immer mit.
        boundary = window[-1].created_at
        previous = conversation.summary or ""
        api_key = resolve_api_key(db, provider, user.id)
        if provider.requires_api_key and not api_key:
            return False

        messages = [
            {"role": "system", "content": SUMMARY_PROMPT},
            {
                "role": "user",
                "content": (
                    (f"Bisherige Zusammenfassung:\n{previous}\n\n" if previous else "")
                    + f"Neuer Verlauf:\n{transcript}"
                ),
            },
        ]
        estimated = max(1, (len(SUMMARY_PROMPT) + len(transcript) + len(previous)) // 4)
        request_id = uuid4()
        try:
            usage_event = reserve_ai_usage(
                db,
                user,
                request_id=request_id,
                estimated_tokens=estimated,
                estimated_cost_microunits=estimate_cost_microunits(provider, estimated),
                provider_id=provider.id,
                model=provider.default_model,
            )
            db.commit()
            usage_event_id = usage_event.id
        except (AiQuotaExceeded, AiUsageConflict):
            # Kein Kontingent fuer die Zusammenfassung: dann bleibt der Chat
            # eben ungefaltet. Das ist unangenehm, aber ehrlicher als sie am
            # Kontingent vorbei zu erzeugen.
            db.rollback()
            return False
        db.refresh(provider)
        db.expunge(provider)

    summary_parts: list[str] = []
    usage = StreamUsage()
    try:
        async for chunk in stream_chat_completion(
            client, provider=provider, api_key=api_key, messages=messages, usage=usage,
        ):
            if chunk.kind == "content":
                summary_parts.append(chunk.text)
    except (AiProviderRequestError, httpx.HTTPError) as exc:
        logger.info("AI-Kompression fehlgeschlagen error=%s", type(exc).__name__)
        with SessionLocal() as db:
            event = _usage_event(db, request_id)
            if event is not None and event.status == "reserved":
                fail_ai_usage(db, event)
                db.commit()
        return False

    summary = redact_sensitive_text("".join(summary_parts)).strip()[:MAX_SUMMARY_CHARS]
    with SessionLocal() as db:
        event = _usage_event(db, request_id)
        if event is not None and event.status == "reserved":
            actual = usage.total_tokens if usage.total_tokens is not None else estimated
            complete_ai_usage(
                db, event,
                actual_tokens=max(0, actual),
                actual_cost_microunits=event.reserved_cost_microunits,
            )
        if not summary:
            # Leere Antwort: nichts als zusammengefasst markieren, sonst waeren
            # die Nachrichten weg und nichts an ihrer Stelle.
            db.commit()
            return False
        conversation = db.get(AiConversation, conversation_id)
        if conversation is None:
            db.commit()
            return False
        conversation.summary = summary
        conversation.summarized_until = boundary
        conversation.updated_at = datetime.now(timezone.utc)
        db.commit()
    logger.info("AI-Kontext gefaltet conversation_id=%s", conversation_id)
    return True


def _usage_event(db, request_id: UUID):
    from models import AiUsageEvent

    return (
        db.query(AiUsageEvent)
        .filter(AiUsageEvent.request_id == str(request_id))
        .first()
    )
