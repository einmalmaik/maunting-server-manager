"""Faltet den aelteren Teil eines langen Chats zu einer Zusammenfassung.

Der Assistent hat genau **eine** Unterhaltung, die nie endet. Ohne diesen
Schritt hiess "lang" schlicht "abgeschnitten": `build_provider_messages` nahm,
was ins Budget passte, und liess den Rest weg — ohne Hinweis, ohne Ersatz. Nach
ein paar Dutzend Nachrichten wusste die KI nicht mehr, worum es am Anfang ging,
behauptete aber weiterhin, den Verlauf zu kennen.

**Wann** gefaltet wird, haengt seit der Fensterberechnung am Modell und an einer
Einstellung des Betreibers, nicht mehr an einer Konstante: der Katalog nennt das
Kontextfenster, `ai_context_window.schwelle_prozent` sagt, wie voll es werden
darf. Ein Modell mit einer Million Token faltet damit erst nach einem sehr
langen Gespraech, eines mit 8.000 nach wenigen Nachrichten — und beides ist
richtig. Nur wenn ueber das Modell nichts bekannt ist, gilt weiter die alte
Konstante.

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
from services import ai_context_window, ai_reasoning
from services.ai_context_service import (
    _message_content_for_provider,
    teilbudgets,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_provider_service import estimate_cost_microunits, resolve_api_key
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    abrechnung,
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

# Ab wann gefaltet wird, **solange ueber das Modell nichts bekannt ist**.
# Bewusst deutlich ueber dem Rueckfallbudget (24.000 Zeichen): darunter passt
# alles hinein und es gaebe nichts zu sparen. Kennt der Katalog das Fenster,
# gilt stattdessen die eingestellte Marke — siehe `faltschwelle`.
COMPACTION_THRESHOLD_CHARS = 40_000
# So viel bleibt nach dem Falten woertlich stehen. Der juengste Teil eines
# Gespraechs traegt den aktuellen Faden; ihn zusammenzufassen waere der
# schaedlichste Teil der Uebung. Haengt bewusst **nicht** am Fenster: das hier
# ist, was nie gefaltet wird, und das ist bei jedem Modell dasselbe.
KEEP_RECENT_MESSAGES = 12
# Obergrenze fuer das, was in einem Rutsch zusammengefasst wird — Rueckfall,
# wenn das Fenster unbekannt ist. Sonst siehe `_quellgrenze`.
MAX_SOURCE_CHARS = 60_000
# Wieviele Zeichen eine zusammengefasste Zeile ungefaehr braucht. Nur zur
# Umrechnung der Laengenvorgabe in eine Satzzahl, die im Prompt stehen kann:
# "hoechstens 34.560 Zeichen" ist keine Anweisung, mit der ein Modell etwas
# anfangen kann, "hoechstens 138 Saetze" schon.
ZEICHEN_JE_SATZ = 250
# Fuenfzehn Saetze galten bisher fuer jedes Gespraech. Bei einem grossen Fenster
# ist das die eigentliche Verlustquelle: 700.000 Zeichen Verlauf auf fuenfzehn
# Saetze einzudampfen wirft mehr weg, als das Falten einspart. Die Obergrenze
# steht trotzdem, denn eine Zusammenfassung, die so lang ist wie das Gespraech,
# waere keine.
MIN_SUMMARY_SAETZE = 15
MAX_SUMMARY_SAETZE = 120


def _summary_prompt(saetze: int) -> str:
    return (
        "Fasse den folgenden Gespraechsverlauf zwischen einem Benutzer und dem "
        f"MSM-Serverassistenten zusammen. Schreibe hoechstens {saetze} Saetze "
        "in der Sprache des Gespraechs.\n"
        "Behalte: welche Server und Spiele besprochen wurden, welche Probleme "
        "auftraten, welche Ursachen gefunden wurden, welche Aktionen ausgefuehrt "
        "oder abgelehnt wurden, und offene Punkte.\n"
        "Lass weg: Hoeflichkeitsfloskeln, Wiederholungen, roh zitierte Logzeilen "
        "und Konfigurationsinhalte.\n"
        "Nenne niemals Passwoerter, Schluessel oder Tokens.\n"
        "Gib nur die Zusammenfassung aus, ohne Einleitung."
    )


#: Der Prompt, wie er ohne Fensterwissen aussieht. Bleibt als Konstante
#: erhalten, weil er die Vorgabe ist, gegen die alles andere gemessen wird.
SUMMARY_PROMPT = _summary_prompt(MIN_SUMMARY_SAETZE)


def _summary_grenzen(context_chars: int | None) -> tuple[str, int]:
    """Prompt und Laengenschnitt fuer eine Zusammenfassung unter diesem Fenster."""
    zeichen = teilbudgets(context_chars).zusammenfassung_zeichen
    saetze = min(
        max(zeichen // ZEICHEN_JE_SATZ, MIN_SUMMARY_SAETZE), MAX_SUMMARY_SAETZE
    )
    return _summary_prompt(saetze), zeichen


def faltschwelle(context_chars: int | None) -> int:
    """Ab wievielen faltbaren Zeichen zusammengefasst wird.

    Ohne bekanntes Fenster die alte Konstante — 40.000 Zeichen, unabhaengig von
    allem. Mit bekanntem Fenster der eingestellte Anteil davon. Die Konstante
    hier auch fuer bekannte Fenster als Untergrenze zu nehmen waere falsch: bei
    einem Modell mit 4.096 Token liegt sie **ueber** dem gesamten Kontext, es
    wuerde also nie gefaltet und stattdessen dauerhaft abgeschnitten.
    """
    if not context_chars:
        return COMPACTION_THRESHOLD_CHARS
    return ai_context_window.faltmarke_zeichen_aus_budget(context_chars)


def _quellgrenze(context_chars: int | None) -> int:
    """Wieviel Verlauf in **einen** Zusammenfassungsruf geht.

    Die Anfrage selbst muss ins Fenster passen — daher die Klemmung nach oben.
    Abgezogen wird der Platz fuer die bisherige Zusammenfassung und die neue:
    beide stehen in derselben Anfrage.

    Bewusst **mehr** als die Faltmarke. Waeren beide gleich, faltete jeder
    Durchlauf genau bis zur Marke und liesse den Ueberhang liegen — der Chat
    haenge dann dauerhaft knapp an der Grenze, statt danach wieder Luft zu
    haben. Ganz aufgeht es trotzdem nicht immer: ein Gespraech kann ueber das
    Fenster hinauswachsen, bevor gefaltet wird, und der Rest kommt dann beim
    naechsten Durchlauf dran.
    """
    if not context_chars:
        return MAX_SOURCE_CHARS
    reserve = 2 * teilbudgets(context_chars).zusammenfassung_zeichen
    return min(context_chars, max(MAX_SOURCE_CHARS, context_chars - reserve))


def needs_compaction(
    db, conversation: AiConversation, context_chars: int | None = None
) -> bool:
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
    ) >= faltschwelle(context_chars)


def _pending_messages(db, conversation: AiConversation) -> list[AiMessage]:
    """Alle noch nicht zusammengefassten, vollstaendigen Nachrichten."""
    query = db.query(AiMessage).filter(
        AiMessage.conversation_id == conversation.id,
        AiMessage.status == "complete",
    )
    if conversation.summarized_until is not None:
        query = query.filter(AiMessage.created_at > conversation.summarized_until)
    return query.order_by(AiMessage.created_at.asc()).all()


def _foldable_window(
    foldable: list[AiMessage], grenze: int = MAX_SOURCE_CHARS
) -> tuple[list[AiMessage], str]:
    """Waehlt die aeltesten Nachrichten, die zusammen in ``grenze`` passen.

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
        if fenster and laenge + len(zeile) + 1 > grenze:
            break
        zeile = zeile[:grenze]
        fenster.append(row)
        zeilen.append(zeile)
        laenge += len(zeile) + 1
    return fenster, "\n".join(zeilen)


async def compact_conversation(
    *,
    client: httpx.AsyncClient,
    user_id: int,
    conversation_id: str,
    provider_id: int,
    context_chars: int | None = None,
) -> bool:
    """Faltet den aelteren Teil zusammen. Liefert True, wenn etwas passiert ist.

    Laeuft ausdruecklich mit eigenen, kurzen DB-Sitzungen: der Aufrufer ist ein
    bereits beendeter Stream, und waehrend des Providerrufs darf keine
    Transaktion offen stehen.

    ``context_chars`` kommt vom Lauf, der gerade zu Ende ging, und wird hier
    **nicht** neu ermittelt. Der Grund ist nicht die Ersparnis eines
    Katalogabrufs, sondern die Uebereinstimmung: gefaltet werden soll nach der
    Marke des Modells, das gerade geantwortet hat. Waehlte der Betreiber
    zwischendurch ein anderes Modell, faltete das Streamende sonst nach einem
    Fenster, das fuer dieses Gespraech nie galt.
    """
    with SessionLocal() as db:
        conversation = db.get(AiConversation, conversation_id)
        user = db.get(User, user_id)
        provider = db.get(AiProvider, provider_id)
        if conversation is None or user is None or not user.is_active:
            return False
        if provider is None or not provider.enabled:
            return False
        if not needs_compaction(db, conversation, context_chars):
            return False

        rows = _pending_messages(db, conversation)
        foldable = rows[: len(rows) - KEEP_RECENT_MESSAGES]
        window, transcript = _foldable_window(foldable, _quellgrenze(context_chars))
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

        prompt, summary_grenze = _summary_grenzen(context_chars)
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    (f"Bisherige Zusammenfassung:\n{previous}\n\n" if previous else "")
                    + f"Neuer Verlauf:\n{transcript}"
                ),
            },
        ]
        estimated = max(1, (len(prompt) + len(transcript) + len(previous)) // 4)
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
    # Eine Zusammenfassung ist eine Fleissaufgabe, keine Ueberlegung. Das war
    # immer schon gemeint, stand aber nur darin, dass hier nichts gesetzt war —
    # und „nichts gesetzt" heisst bei einem Anbieter ohne Schalter „nimm deine
    # Vorgabe". Bei OpenAI wurde jede Faltung mit Denkschritten bezahlt.
    denken, denkstufe = await ai_reasoning.aus_fuer(
        client, provider, api_key=api_key
    )
    try:
        async for chunk in stream_chat_completion(
            client, provider=provider, api_key=api_key, messages=messages, usage=usage,
            reasoning=denken, reasoning_effort=denkstufe,
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

    summary = redact_sensitive_text("".join(summary_parts)).strip()[:summary_grenze]
    with SessionLocal() as db:
        event = _usage_event(db, request_id)
        if event is not None and event.status == "reserved":
            # Dieselbe Abrechnung wie im Chat. Hier stand fest
            # `actual_cost_microunits=event.reserved_cost_microunits` — die
            # Verdichtung buchte damit **nie** echte Kosten, sondern immer die
            # Schaetzung von vor dem Aufruf, selbst wenn der Anbieter den
            # tatsaechlichen Betrag daneben gemeldet hatte.
            tokens, kosten, herkunft = abrechnung(
                usage,
                reserved_tokens=event.reserved_tokens,
                estimated_actual_tokens=estimated,
                token_price_micro_usd_per_million=(
                    provider.token_price_micro_usd_per_million
                ),
            )
            complete_ai_usage(
                db, event,
                actual_tokens=tokens,
                actual_cost_microunits=kosten,
                aufschluesselung=usage,
                cost_source=herkunft,
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
