"""Die Schleife eines Laufs: Anbieter fragen, Werkzeuge fahren, Vorschlaege anlegen.

Der Zug der KI hing frueher an einem HTTP-Request — er war ein Generator, den
der Browser am Leben hielt. Hier ist er ein **Segment eines Laufs**
(``segment_ausfuehren``): er holt seinen Zustand aus der Datenbank, arbeitet, und
legt ihn wieder ab. Wer zusieht, geht ihn nichts an; dafuer ist
``ai_run_broker`` da.

Damit die Aufgabenteilung nicht wieder verschwimmt:

* ``ai_run_service``   — wann ein Segment laeuft und was zwischen zweien ueberlebt.
* ``ai_stream_service`` — was **in** einem Segment passiert. Kennt keinen Zeitplan.
* ``ai_run_broker``    — wer zusehen darf. Kennt keines von beiden.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import AsyncIterator
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import (
    AiActionProposal, AiMessage, AiProvider, AiRun, AiToolResult, AiUsageEvent, User,
)
from models.ai_run import BEENDET as AUSGELAUFEN
from services.ai_chat_service import get_owned_conversation
from services import ai_run_broker, ai_run_service
from services.ai_action_errors import (
    AiActionStateError,
    AiActionValidationError,
)
from services.ai_action_service import (
    execute_read_tool,
    provider_tool_definitions,
    question_payload,
)
from services.ai_proposal_service import (
    create_proposal,
    execute_autonomously,
)
from services.ai_tool_registry import (
    ASK_TOOLS,
    READ_TOOLS,
    SKILL_TOOLS,
    WRITE_TOOLS,
)
from services.ai_context_service import (
    build_provider_messages,
    estimate_reserved_tokens,
    message_character_count,
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
from services.ai_limit_service import TOKEN_LIMIT_MAX
from services.dis_client import DisSidecarError
from services.openai_compatible_adapter import (
    MAX_REASONING_CHARS,
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


logger = logging.getLogger(__name__)
# Wieviel Ergebnistext eine Runde hoechstens erzeugen darf.
#
# Die Grenze war frueher eine feste Anzahl Aufrufe. Das war das falsche Mass:
# zwanzig Statusabfragen sind zusammen kleiner als ein einziger Logauszug, und
# `read_server_logs` liefert bis zu 24.000 Zeichen. Eine Zahl behandelt beide
# gleich und wird dadurch entweder zu eng (die KI kann nicht durchfragen) oder
# zu weit (ein halbes Kontextfenster in einer Runde).
#
# Gezaehlt wird deshalb das, was tatsaechlich knapp ist. Billige Aufrufe laufen
# alle; sobald das Budget aufgebraucht ist, werden die restlichen vertagt statt
# abgewiesen. Rund 48.000 Zeichen sind grob 12.000 Tokens — Platz fuer etwa
# dreissig Statusabfragen oder zwei volle Logauszuege.
MAX_TOOL_RESULT_CHARS_PER_ROUND = 48_000
# Absolute Reissleine gegen ein durchgedrehtes Modell. Kein Mensch stellt eine
# Frage, die mehr als das rechtfertigt; wer mehr schickt, antwortet nicht
# gruendlich, sondern fehlerhaft.
MAX_TOOL_CALLS = 32
# Leserunden **je Lauf**, nicht je Nachricht.
#
# Hier standen vier. Das war die Zahl aus der Zeit, in der ein Zug eine Frage
# beantwortete: lesen, lesen, antworten. Fuer einen Auftrag wie "richte den
# Server ein, stell das ein, starte ihn und sag Bescheid" ist sie zu klein —
# die KI kam bis zur Haelfte und musste aufhoeren, obwohl sie wusste, was noch
# fehlte. Genau die Beschwerde: *"die muss das wirklich komplett bis zum Ende
# machen, Aufgaben zu Ende bringen, Ende zu Ende."*
#
# Sechzehn ist keine Beliebigkeit, sondern die Obergrenze der Anbieteraufrufe
# eines Laufs: mehr als sechzehn Leserunden hat noch keine Diagnose gebraucht,
# und die Grenze bricht nicht ab, sondern nimmt die Werkzeuge weg. Das Modell
# antwortet dann aus dem, was es hat.
MAX_TOOL_ROUNDS = 16
# Schreibrunden je Lauf. Zwei reichten fuer "pass die Config an und starte
# danach" — aber nicht fuer eine Einrichtung aus Anlegen, Konfigurieren,
# Starten und Melden. Acht deckt jede Bitte ab, die ein Mensch in einem Absatz
# formuliert, und bleibt weit unter dem, was ein durchgedrehtes Modell braeuchte,
# um Schaden anzurichten — jede einzelne Aktion durchlaeuft weiterhin
# Rechtepruefung und, wo noetig, die Bestaetigung eines Menschen.
MAX_WRITE_ROUNDS = 8
# Wie oft derselbe Lesewerkzeugaufruf mit **denselben** Argumenten laufen darf.
# Ein Modell, das die gleiche Auskunft zum dritten Mal holt, bekommt keine neue
# Antwort — es haengt. Der Aufruf wird dann nicht ausgefuehrt, sondern begruendet
# abgelehnt: eine Grenze, die erklaert, statt einer, die abbricht.
MAX_GLEICHE_AUFRUFE = 3


def sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _finalize_stream(
    *,
    message_id: str,
    usage_event_id: int,
    content: str,
    provider_total_tokens: int | None,
    estimated_actual_tokens: int,
    failed: bool,
    had_output: bool,
    token_price_cents_per_million: int | None = None,
    reasoning: str = "",
    question: dict | None = None,
) -> None:
    with SessionLocal() as db:
        message = db.get(AiMessage, message_id)
        usage_event = db.get(AiUsageEvent, usage_event_id)
        if usage_event is None:
            # Ohne Verbrauchszeile gibt es nichts mehr abzurechnen.
            logger.warning("AI usage event missing at finalization message_id=%s", message_id)
            return
        if message is not None:
            message.content = content
            # Denkschritte werden mitgespeichert, damit der aufklappbare Block
            # nach einem Neuladen der Seite noch da ist. Redigiert wie jeder
            # andere Modelltext auch: ein Modell kann in seinen Ueberlegungen
            # genauso einen Key wiederholen wie in der Antwort.
            message.reasoning = (
                redact_sensitive_text(reasoning)[:MAX_REASONING_CHARS] or None
            )
            # Die Rueckfrage gehoert zur Nachricht. Sie ist bereits durch
            # `question_payload()` geprueft, gekuerzt und redigiert — hier wird
            # nur noch abgelegt.
            if question is not None:
                message.question_json = json.dumps(
                    question, ensure_ascii=True, separators=(",", ":")
                )
            message.status = "failed" if failed else "complete"
        else:
            # Die Nachricht wurde waehrend des Streams entfernt (z. B. Chat
            # geloescht). Die Reservierung muss trotzdem abgeschlossen werden:
            # sonst bliebe sie dauerhaft "reserved" und wuerde Kontingent sowie
            # einen Nebenlaeufigkeitsplatz des Benutzers permanent blockieren.
            logger.warning("AI message missing at finalization message_id=%s", message_id)
        if failed and not had_output:
            fail_ai_usage(db, usage_event)
        else:
            # Nach partieller Ausgabe darf Verbrauch nicht als null verbucht
            # werden. Ohne finale Provider-Usage gilt konservativ die Reserve.
            actual_tokens = provider_total_tokens
            if actual_tokens is None:
                actual_tokens = (
                    usage_event.reserved_tokens if failed else estimated_actual_tokens
                )
            accounted_tokens = min(TOKEN_LIMIT_MAX, max(0, actual_tokens))
            # Kosten folgen den tatsaechlich verbuchten Tokens. Ohne gepflegten
            # Preis bleibt die Reserve (null) stehen; nie weniger als reserviert,
            # damit eine Ueberschreitung nicht nachtraeglich verschwindet.
            actual_cost = usage_event.reserved_cost_microunits
            if token_price_cents_per_million:
                actual_cost = max(
                    actual_cost,
                    (accounted_tokens * int(token_price_cents_per_million)) // 100,
                )
            complete_ai_usage(
                db,
                usage_event,
                actual_tokens=accounted_tokens,
                actual_cost_microunits=actual_cost,
            )
        db.commit()


def _tool_followup_messages(
    *, user_id: int, conversation_id: str, tool_calls, deferred=()
) -> tuple[list[dict], list[dict]]:
    """Fuehrt Lesewerkzeuge aus und baut daraus die Folge-Nachrichten.

    ``deferred`` sind Paare aus Aufruf und Begruendung: Aufrufe, die in dieser
    Runde bewusst **nicht** laufen — ein Schreibwerkzeug, das das Modell mit
    Lesewerkzeugen vermischt hat, oder ein Aufruf ueber der Rundengrenze. Sie
    bekommen trotzdem eine Antwort: das Protokoll verlangt zu jeder
    `tool_call_id` genau ein Ergebnis, und ohne Begruendung wuesste das Modell
    nicht, warum sein Aufruf verschwunden ist.

    Ein **einzelner** fehlgeschlagener Aufruf beendet den Stream nicht. Fragt
    das Modell nebenbei nach einem Server, den der Benutzer nicht sehen darf,
    ist das eine Auskunft an das Modell — kein Grund, dem Benutzer die ganze
    Antwort wegzunehmen. Die Rechtepruefung hat ihre Arbeit getan: ausgefuehrt
    wurde nichts.
    """
    deferred = [(call, reason) for call, reason in deferred]
    if len(tool_calls) + len(deferred) > MAX_TOOL_CALLS:
        raise AiActionValidationError("Ungueltige Read-Tool-Sequenz")
    if any(call.name not in READ_TOOLS for call in tool_calls):
        raise AiActionValidationError("Ungueltige Read-Tool-Sequenz")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise AiActionValidationError("AI-Zugriff wurde entzogen")
        conversation = get_owned_conversation(db, conversation_id, user)
        if conversation is None:
            raise AiActionValidationError("Unterhaltung ist nicht mehr verfuegbar")
        assistant_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=True),
                    },
                }
                for call in [*tool_calls, *(item[0] for item in deferred)]
            ],
        }
        results: list[dict] = [assistant_call]
        # Was der Benutzer im Chat sehen soll: welches Werkzeug lief und womit.
        # Bewusst ohne das Ergebnis — ein Logausschnitt gehoert nicht ungefragt
        # in den sichtbaren Verlauf, und die Antwort fasst ihn ohnehin zusammen.
        display: list[dict] = []
        spent = 0
        for index, call in enumerate(tool_calls):
            # Budget statt Stueckzahl. Wer schon etwas bekommen hat und das
            # Budget ausgeschoepft sieht, hoert auf — der Rest wird vertagt,
            # nicht abgewiesen. Der erste Aufruf laeuft immer: sonst kaeme ein
            # einzelner grosser Logauszug nie durch.
            if index > 0 and spent >= MAX_TOOL_RESULT_CHARS_PER_ROUND:
                deferred.append((call, (
                    "Fuer diese Runde war kein Platz mehr. Der Aufruf lief "
                    "nicht — stelle ihn in der naechsten Runde erneut."
                )))
                continue
            failed_reason: str | None = None
            try:
                value = execute_read_tool(
                    db,
                    user=user,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
            except AiActionValidationError as exc:
                # Fehlendes Recht, fremde Server-ID, ungueltige Argumente. Das
                # Modell soll es erfahren und weitermachen koennen; frueher riss
                # ein solcher Aufruf die gesamte Antwort ab.
                failed_reason = str(exc)
                value = {"error": failed_reason}
            # Persistieren, damit eine Rueckfrage im selben Chat die gerade
            # gelesenen Daten noch sieht. Ohne das musste das Modell sie neu
            # holen — oder antwortete ohne sie, obwohl es sie selbst geholt hatte.
            db.add(AiToolResult(
                id=str(uuid4()),
                conversation_id=conversation.id,
                tool_name=call.name,
                result_json=json.dumps(value, ensure_ascii=True, separators=(",", ":")),
            ))
            # Das Ergebnis wird ausdruecklich als unvertrauenswuerdig gekennzeichnet.
            # Genau hier kommt der Text an, den ein Spieler ueber den Chat eines
            # Gameservers in dessen Log geschrieben hat: read_server_logs liefert
            # bis zu 24.000 Zeichen, die vollstaendig von aussen stammen koennen.
            # Anhaenge tragen dieses Label seit jeher (ai_attachment_service),
            # Tool-Ergebnisse bisher nicht — obwohl sie der offenere Kanal sind.
            serialized = json.dumps(
                {"untrusted": True, "tool": call.name, "data": value},
                ensure_ascii=True,
                separators=(",", ":"),
            )
            spent += len(serialized)
            results.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": serialized,
            })
            entry = {
                "tool_name": call.name,
                "server_id": call.arguments.get("server_id")
                if isinstance(call.arguments.get("server_id"), int)
                else None,
                # Ein gescheiterter Aufruf gehoert sichtbar in den Verlauf.
                # Sonst wirkt eine Antwort vollstaendig, der eine Auskunft fehlt.
                **({"failed": True} if failed_reason else {}),
            }
            # Bei Skills gehoert der Name in den Verlauf, nicht nur "read_skill".
            # Der Betreiber will sehen, *welche* erlernte Vorgehensweise
            # gegriffen hat — sonst wirkt eine Antwort, die aus einem Skill
            # entstanden ist, wie geraten. Der Schluessel kommt aus dem
            # Ergebnis und nicht aus den Argumenten: dort ist er bereits
            # normalisiert und gegen die Sichtbarkeit geprueft.
            if call.name in SKILL_TOOLS and isinstance(value, dict):
                entry["skill_key"] = value.get("skill_key")
                entry["skill_name"] = value.get("name")
                entry["skill_status"] = value.get("status")
                entry["skill_learned"] = bool(value.get("learned"))
            display.append(entry)
        # Erst hier: die Ausfuehrungsschleife oben legt selbst weitere Aufrufe
        # zurueck, sobald das Budget aufgebraucht ist. Wuerden die Absagen
        # vorher erzeugt, blieben genau diese `tool_call_id` ohne Antwort — und
        # manche Anbieter weisen die naechste Anfrage deswegen ab.
        for call, reason in deferred:
            results.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps({
                    "executed": False, "reason": reason,
                }, ensure_ascii=True, separators=(",", ":")),
            })
        db.commit()
        return results, display


def _persist_write_proposals(
    *, user_id: int, conversation_id: str, tool_calls, correlation_id: str, run_id: str | None = None
) -> list[dict]:
    if len(tool_calls) > MAX_TOOL_CALLS or any(call.name not in WRITE_TOOLS for call in tool_calls):
        raise AiActionValidationError("Ungueltige Write-Tool-Sequenz")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise AiActionValidationError("AI-Zugriff wurde entzogen")
        conversation = get_owned_conversation(db, conversation_id, user)
        if conversation is None:
            raise AiActionValidationError("Unterhaltung ist nicht mehr verfuegbar")
        proposals = [
            create_proposal(
                db,
                user=user,
                conversation=conversation,
                tool_name=call.name,
                arguments=call.arguments,
                correlation_id=correlation_id,
            )
            for call in tool_calls
        ]
        # Der Rueckweg: welcher Lauf wartet auf diesen Vorschlag. Ohne ihn
        # wuesste der Bestaetigungsknopf spaeter nicht, wen er aufwecken soll.
        for proposal in proposals:
            proposal.run_id = run_id
        db.commit()
        results: list[dict] = []
        # Feste Kopien: `execute_autonomously` committet und rollt bei einem
        # Fehler zurueck. Ein danach noch gehaltenes ORM-Objekt waere abgelaufen.
        summaries = [
            (proposal.id, proposal.tool_name, proposal.preview_json, proposal.autonomous)
            for proposal in proposals
        ]
        for proposal_id, tool_name, preview_json, autonomous in summaries:
            error_code: str | None = None
            if autonomous:
                # Sofort ausfuehren — aber ueber denselben Pfad wie eine
                # bestaetigte Aktion. Scheitert sie, endet das nicht den Stream:
                # der Benutzer soll die Antwort samt Fehlergrund sehen.
                try:
                    execute_autonomously(db, proposal_id=proposal_id, user=user)
                except AiActionStateError as exc:
                    error_code = exc.code
                except Exception:
                    logger.warning("Autonome AI-Aktion fehlgeschlagen id=%s", proposal_id)
                    error_code = "AI_ACTION_EXECUTION_FAILED"
            current = db.get(AiActionProposal, proposal_id)
            results.append({
                "id": proposal_id,
                "server_id": current.server_id if current is not None else None,
                "tool_name": tool_name,
                "preview": json.loads(preview_json),
                "status": current.status if current is not None else "failed",
                "autonomous": bool(autonomous),
                **({"error_code": error_code} if error_code else {}),
            })
        return results


def _write_followup_messages(
    *, conversation_id: str, tool_calls, proposals: list[dict]
) -> list[dict]:
    """Gibt dem Modell zurueck, was aus seinen Schreib-Aufrufen geworden ist.

    Ohne diesen Rueckfluss endete ein Schreibvorgang stumm: das Modell hatte
    nur einen Werkzeugaufruf abgegeben und nie erfahren, ob er durchging. Die
    Antwortnachricht blieb leer ("Keine Antwort erhalten"), und — schwerer
    wiegend — der naechste Zug sah eine Historie ohne jede Spur der Aktion.
    Ein blosses "danke" wirkte dort wie eine noch offene Bitte, und das Modell
    stoppte denselben Server ein zweites Mal.

    Der Ergebnistext wird zusaetzlich als `AiToolResult` abgelegt. Die
    Abschlussrunde koennte auch ohne Text enden; die Zeile stellt sicher, dass
    die Historie den Vorgang trotzdem kennt.
    """
    outcome_by_tool: dict[str, list[dict]] = {}
    for proposal in proposals:
        outcome_by_tool.setdefault(proposal["tool_name"], []).append({
            "status": proposal.get("status"),
            "autonomous": proposal.get("autonomous"),
            "server_id": proposal.get("server_id"),
            **({"error_code": proposal["error_code"]} if proposal.get("error_code") else {}),
        })

    assistant_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=True),
                },
            }
            for call in tool_calls
        ],
    }
    messages: list[dict] = [assistant_call]
    for call in tool_calls:
        outcomes = outcome_by_tool.get(call.name, [])
        # `succeeded` heisst ausgefuehrt, `proposed` heisst: wartet auf den
        # Menschen. Die Unterscheidung muss beim Modell ankommen, sonst meldet
        # es einen Vorschlag als erledigt.
        payload = {"tool": call.name, "outcomes": outcomes}
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        })

    try:
        with SessionLocal() as db:
            for tool_name, outcomes in outcome_by_tool.items():
                db.add(AiToolResult(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    result_json=json.dumps(
                        {"outcomes": outcomes}, ensure_ascii=True, separators=(",", ":")
                    ),
                ))
            db.commit()
    except Exception:
        # Die Spur in der Historie ist wichtig, aber nicht wichtiger als die
        # Antwort. Scheitert das Schreiben, laeuft die Abschlussrunde trotzdem.
        logger.warning(
            "Ergebnis der Schreibaktion nicht persistiert conversation_id=%s", conversation_id
        )
    return messages


@dataclass(frozen=True)
class _Vorbereitung:
    """Alles, was ein Segment braucht — in einer kurzen Transaktion geholt."""

    run_id: str
    user_id: int
    conversation_id: str
    provider: AiProvider
    api_key: str | None
    message_id: str
    usage_event_id: int
    request_id: str
    reasoning: bool
    token_price_cents_per_million: int | None
    zustand: dict


def _vorschlag_ergebnisse(db, proposal_ids: list[str]) -> list[dict]:
    """Was aus den Vorschlaegen einer geparkten Runde geworden ist.

    Genau die Auskunft, die das Modell beim Aufwecken braucht: hat der Mensch
    zugestimmt, ist die Aktion gelaufen, ist sie gescheitert. Ohne sie wuesste
    es nur, dass es etwas vorgeschlagen hat.
    """
    ergebnisse: list[dict] = []
    for proposal_id in proposal_ids:
        row = db.get(AiActionProposal, proposal_id)
        if row is None:
            ergebnisse.append({
                "tool_name": "unknown",
                "status": "failed",
                "autonomous": False,
                "server_id": None,
                "error_code": "AI_ACTION_NOT_FOUND",
            })
            continue
        ergebnisse.append({
            "tool_name": row.tool_name,
            "status": row.status,
            "autonomous": bool(row.autonomous),
            "server_id": row.server_id,
            **({"error_code": row.error_code} if row.error_code else {}),
        })
    return ergebnisse


def _aktionsmeldung(ergebnisse: list[dict]) -> dict:
    """Die Entscheidung des Menschen, als Meldung des Panels an das Modell.

    Ausdruecklich als Panel-Meldung beschriftet und nicht als Satz des
    Benutzers: das Modell soll nicht glauben, jemand haette ihm das getippt.
    """
    return {
        "role": "user",
        "content": (
            "Meldung des Panels (nicht vom Benutzer geschrieben): Der Benutzer "
            "hat ueber die vorgeschlagenen Aktionen entschieden. Ergebnis:\n"
            + json.dumps(ergebnisse, ensure_ascii=True, separators=(",", ":"))
            + "\n\nArbeite die Aufgabe von hier aus weiter. Ist sie damit "
            "erledigt, sag es kurz; fehlt noch ein Schritt, mach ihn."
        ),
    }


def _segment_beginnen(db, run: AiRun, zustand: dict) -> tuple[str, int, str] | None:
    """Legt Nachricht und Verbrauchszeile fuer ein neues Segment an.

    Jede Fortsetzung nach einer Bestaetigung schreibt eine **eigene** Nachricht.
    Das ist Absicht: "ich stelle um, bitte bestaetigen" und "erledigt, der
    Server laeuft" sind zwei Aussagen. Sie in eine Blase zu zwingen haette
    bedeutet, den Text nachtraeglich zu veraendern — und den Zeitpunkt zu
    verwischen, an dem der Mensch zugestimmt hat.
    """
    provider = db.get(AiProvider, run.provider_id) if run.provider_id else None
    if provider is None:
        return None
    request_id = str(uuid4())
    usage_event = reserve_ai_usage(
        db,
        db.get(User, run.user_id),
        request_id=UUID(request_id),
        estimated_tokens=estimate_reserved_tokens(zustand["provider_messages"]),
        estimated_cost_microunits=estimate_cost_microunits(
            provider, estimate_reserved_tokens(zustand["provider_messages"])
        ),
        server_id=None,
        provider_id=provider.id,
        model=provider.default_model,
    )
    message_id = str(uuid4())
    db.add(AiMessage(
        id=message_id,
        conversation_id=run.conversation_id,
        role="assistant",
        content="",
        status="streaming",
        provider_id=provider.id,
        model=provider.default_model,
        request_id=request_id,
    ))
    db.flush()
    return message_id, usage_event.id, request_id


def _segment_vorbereiten(run_id: str) -> tuple[_Vorbereitung | None, tuple[str, str] | None]:
    """Holt den Lauf aus der Datenbank und macht ihn lauffaehig.

    Die Sitzung ist absichtlich kurz: waehrend der Anbieter streamt, darf keine
    Transaktion offen stehen. Genau dafuer ist der Zustand persistiert.
    """
    with SessionLocal() as db:
        run = db.get(AiRun, run_id)
        if run is None:
            return None, ("AI_RUN_NOT_FOUND", "ai.errors.notFound")
        if run.status not in {"running"}:
            # Zwischen Planung und Ausfuehrung hat sich etwas geaendert — etwa
            # eine neue Nachricht, die den Lauf ueberholt hat.
            return None, None
        user = db.get(User, run.user_id)
        if user is None or not user.is_active:
            return None, ("AI_ACCESS_REVOKED", "ai.errors.access")
        provider = db.get(AiProvider, run.provider_id) if run.provider_id else None
        if provider is None or not provider.enabled:
            return None, ("AI_RESOURCE_NOT_FOUND", "ai.errors.notFound")

        zustand = ai_run_service.zustand_lesen(run)

        # Fortsetzung: der Lauf erfaehrt, wie der Mensch entschieden hat.
        #
        # Bewusst **kein** zweites Werkzeugergebnis: die `tool_call_id` der
        # geparkten Runde hat ihre Antwort schon bekommen ("wartet auf
        # Bestaetigung"), und das Protokoll erlaubt nur eine je Aufruf. Die
        # Entscheidung ist auch kein Werkzeugergebnis, sondern eine Meldung des
        # Panels — und genau als solche gekennzeichnet. Dasselbe Muster nutzt
        # `ai_context_service` bereits fuer frueher gelesene Werkzeugergebnisse.
        pending = zustand.get("pending")
        if pending:
            ergebnisse = _vorschlag_ergebnisse(db, list(pending.get("proposal_ids", [])))
            zustand["provider_messages"].append(_aktionsmeldung(ergebnisse))
            zustand["pending"] = None

        try:
            api_key = resolve_api_key(db, provider, user.id)
        except DisSidecarError:
            return None, ("AI_CREDENTIAL_UNAVAILABLE", "ai.errors.credential")
        if provider.requires_api_key and not api_key:
            return None, ("AI_PROVIDER_KEY_MISSING", "ai.errors.keyMissing")

        if run.message_id:
            nachricht = db.get(AiMessage, run.message_id)
            message_id = run.message_id
            usage_event_id = zustand.get("usage_event_id")
            request_id = zustand.get("request_id") or (
                nachricht.request_id if nachricht is not None else str(uuid4())
            )
            if usage_event_id is None:
                # Der Startpfad legt beides an; fehlt die Verbuchung, ist der
                # Zustand kaputt und ein neues Segment ehrlicher als raten.
                run.message_id = None
        if not run.message_id:
            begonnen = _segment_beginnen(db, run, zustand)
            if begonnen is None:
                return None, ("AI_RESOURCE_NOT_FOUND", "ai.errors.notFound")
            message_id, usage_event_id, request_id = begonnen
            run.message_id = message_id
            zustand["usage_event_id"] = usage_event_id
            zustand["request_id"] = request_id

        ai_run_service.zustand_schreiben(run, zustand)
        db.commit()
        db.refresh(provider)
        db.expunge(provider)
        return _Vorbereitung(
            run_id=run_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            provider=provider,
            api_key=api_key,
            message_id=message_id,
            usage_event_id=int(usage_event_id),
            request_id=str(request_id),
            reasoning=bool(run.reasoning),
            token_price_cents_per_million=provider.token_price_cents_per_million,
            zustand=zustand,
        ), None


def _lauf_abschliessen(
    run_id: str, *, status: str, stop_reason: str, zustand: dict | None = None
) -> None:
    with SessionLocal() as db:
        run = db.get(AiRun, run_id)
        if run is None:
            return
        run.status = status
        run.stop_reason = stop_reason
        if zustand is not None:
            ai_run_service.zustand_schreiben(run, zustand)
        if status != "running":
            # Ein geparkter oder beendeter Lauf hat kein laufendes Segment mehr.
            # Die naechste Fortsetzung legt eine neue Nachricht an.
            run.message_id = None
        run.updated_at = datetime.now(timezone.utc)
        db.commit()
    ai_run_broker.veroeffentlichen(
        run_id, "run", {"run_id": run_id, "status": status, "stop_reason": stop_reason}
    )
    if status in AUSGELAUFEN:
        ai_run_broker.beenden(run_id)


def _werkzeug_signatur(name: str, argumente: dict) -> str:
    return name + "|" + json.dumps(argumente, ensure_ascii=True, sort_keys=True)


async def segment_ausfuehren(run_id: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Fuehrt einen Lauf aus, bis er fertig ist, fragt oder auf einen Menschen wartet.

    Das ist der frueher `stream_conversation_reply` genannte Ablauf — mit dem
    einen Unterschied, der alles aendert: er haengt an keinem Request mehr.
    Ergebnisse gehen an den Vermittler (``ai_run_broker``), nicht an einen
    Generator. Wer zusieht, ist dem Lauf gleichgueltig.
    """
    vorbereitung, fehler = _segment_vorbereiten(run_id)
    if vorbereitung is None:
        if fehler is not None:
            code, message_key = fehler
            ai_run_broker.veroeffentlichen(
                run_id, "error", {"code": code, "message_key": message_key}
            )
            _lauf_abschliessen(run_id, status="failed", stop_reason=code)
        return

    # Der Client kommt normalerweise aus dem Prozess (beim Start gesetzt). Als
    # Parameter ist er ueberreichbar, damit ein Test das Segment ausfuehren kann,
    # ohne eine ganze Anwendung hochzufahren.
    client = client or ai_run_service.http_client()
    if client is None:
        logger.error("Kein AI-HTTP-Client, Lauf kann nicht arbeiten run_id=%s", run_id)
        ai_run_broker.veroeffentlichen(
            run_id, "error", {"code": "AI_RUNTIME_UNAVAILABLE", "message_key": "ai.errors.unavailable"}
        )
        _lauf_abschliessen(run_id, status="failed", stop_reason="AI_RUNTIME_UNAVAILABLE")
        return

    zustand = vorbereitung.zustand
    provider_messages: list[dict] = zustand["provider_messages"]
    conversation_id = vorbereitung.conversation_id
    user_id = vorbereitung.user_id
    message_id = vorbereitung.message_id

    ai_run_broker.neues_segment(run_id)
    ai_run_broker.veroeffentlichen(
        run_id,
        "message",
        {"message_id": message_id, "request_id": vorbereitung.request_id, "run_id": run_id},
    )

    chunks: list[str] = []
    thoughts: list[str] = []
    usage = StreamUsage()
    abgerechnet = False
    gestellte_frage: dict | None = None
    geparkt = False
    try:
        tools = provider_tool_definitions()
        current_usage = usage
        signaturen: dict[str, int] = dict(zustand.get("tool_signatures") or {})
        while True:
            async for chunk in stream_chat_completion(
                client,
                provider=vorbereitung.provider,
                api_key=vorbereitung.api_key,
                messages=provider_messages,
                usage=current_usage,
                tools=tools,
                reasoning=vorbereitung.reasoning,
            ):
                if chunk.kind == "reasoning":
                    thoughts.append(chunk.text)
                    ai_run_broker.veroeffentlichen(run_id, "reasoning", {"content": chunk.text})
                    continue
                chunks.append(chunk.text)
                ai_run_broker.veroeffentlichen(run_id, "delta", {"content": chunk.text})
            if current_usage is not usage:
                usage.total_tokens = (
                    usage.total_tokens + current_usage.total_tokens
                    if usage.total_tokens is not None and current_usage.total_tokens is not None
                    else usage.total_tokens or current_usage.total_tokens
                )
            if not current_usage.tool_calls:
                break
            if tools is None:
                # Diese Runde wurde ohne Werkzeugliste angefragt — sie ist die
                # abschliessende. Meldet der Anbieter trotzdem Werkzeugaufrufe,
                # ist das keine Anfrage, die wir erfuellen: wir haben nichts
                # angeboten. Frueher war `tools = None` nur eine Bitte, und ein
                # Anbieter, der sich nicht daran hielt, hielt den Lauf endlos
                # offen. Hier steht die Grenze auf unserer Seite.
                logger.warning(
                    "Anbieter meldet Werkzeugaufrufe ohne angebotene Werkzeuge, "
                    "werden verworfen run_id=%s anzahl=%d",
                    run_id, len(current_usage.tool_calls),
                )
                break

            # Eine Rueckfrage beendet das Segment: ab hier ist der Mensch dran,
            # und seine Antwort kommt als gewoehnliche Nachricht zurueck.
            frage = next(
                (call for call in current_usage.tool_calls if call.name in ASK_TOOLS),
                None,
            )
            if frage is not None:
                gestellte_frage = question_payload(frage.arguments)
                ai_run_broker.veroeffentlichen(run_id, "question", gestellte_frage)
                break

            kinds = {
                "read" if call.name in READ_TOOLS else "write" if call.name in WRITE_TOOLS else "unknown"
                for call in current_usage.tool_calls
            }
            if kinds == {"write"}:
                proposals = _persist_write_proposals(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tool_calls=current_usage.tool_calls,
                    correlation_id=vorbereitung.request_id,
                    run_id=run_id,
                )
                for proposal in proposals:
                    ai_run_broker.veroeffentlichen(
                        run_id, "action" if proposal.get("autonomous") else "proposal", proposal
                    )
                # Das Ergebnis geht **immer** zurueck ans Modell, auch wenn es
                # nur "wartet auf den Menschen" lautet. Das Protokoll verlangt zu
                # jeder `tool_call_id` genau eine Antwort — und das Modell soll
                # den Vorgang in Worte fassen koennen, statt eine leere Blase
                # ueber der Bestaetigungskarte zu hinterlassen.
                provider_messages.extend(_write_followup_messages(
                    conversation_id=conversation_id,
                    tool_calls=current_usage.tool_calls,
                    proposals=proposals,
                ))
                zustand["write_rounds"] = int(zustand.get("write_rounds", 0)) + 1

                # **Der Punkt, an dem der Lauf frueher endete.** Wartet auch nur
                # ein Vorschlag auf einen Menschen, wird geparkt statt
                # aufgegeben: der Zustand geht in die Datenbank, und die
                # Bestaetigung weckt genau hier wieder auf.
                #
                # Geparkt wird aber erst **nach** einer letzten Runde ohne
                # Werkzeuge. Sonst stuende die Karte im Chat und darueber
                # nichts — der Benutzer soll lesen, was da bestaetigt werden
                # will und warum.
                offen = [
                    proposal["id"] for proposal in proposals
                    if proposal.get("status") in {"proposed", "confirmed"}
                ]
                if offen:
                    zustand["pending"] = {
                        "proposal_ids": [proposal["id"] for proposal in proposals],
                    }
                    geparkt = True
                    tools = None
                    current_usage = StreamUsage()
                    continue
                # Alles ausgefuehrt? Dann darf der Lauf weiterarbeiten. Das ist
                # der Unterschied zwischen "eine Aktion abgeben" und "eine
                # Aufgabe erledigen": erst wenn Schritt eins nachweislich lief,
                # ergibt Schritt zwei ueberhaupt Sinn.
                ausgefuehrt = bool(proposals) and all(
                    proposal.get("status") in {"succeeded", "executing"}
                    and not proposal.get("error_code")
                    for proposal in proposals
                )
                if not (ausgefuehrt and zustand["write_rounds"] < MAX_WRITE_ROUNDS):
                    tools = None
                current_usage = StreamUsage()
                continue
            if "unknown" in kinds:
                raise AiProviderRequestError("AI_PROVIDER_TOOL_SEQUENCE_INVALID")

            deferred_calls: list = []
            if kinds == {"read", "write"}:
                # Gemischte Runde: die Lesewerkzeuge laufen, die Schreibaufrufe
                # bekommen eine Absage mit Begruendung und werden nachgeholt.
                deferred_calls = [
                    (call, (
                        "Schreibaktionen laufen in einer eigenen Runde. Lies "
                        "erst zu Ende und rufe die Aktion danach allein auf."
                    ))
                    for call in current_usage.tool_calls if call.name in WRITE_TOOLS
                ]
                current_usage.tool_calls = [
                    call for call in current_usage.tool_calls if call.name in READ_TOOLS
                ]

            # Schleifenerkennung — **ueber Runden hinweg**, nicht innerhalb einer.
            #
            # Das ist der ganze Unterschied: neun Statusabfragen nebeneinander
            # sind eine gruendliche Bestandsaufnahme ("laufen alle Server?"),
            # dieselbe Abfrage in der vierten Runde hintereinander ist ein
            # haengendes Modell. Gezaehlt wird deshalb je Runde einmal je
            # Signatur, und geprueft wird gegen die Runden davor.
            wiederholt: list = []
            frisch: list = []
            for call in current_usage.tool_calls:
                gezaehlt = signaturen.get(_werkzeug_signatur(call.name, call.arguments), 0)
                if gezaehlt >= MAX_GLEICHE_AUFRUFE:
                    wiederholt.append((call, (
                        "Dieser Aufruf lief mit genau diesen Argumenten bereits in "
                        f"{gezaehlt} Runden und liefert nichts Neues. Arbeite mit dem, "
                        "was du hast, oder frage den Benutzer."
                    )))
                    continue
                frisch.append(call)
            for signatur in {
                _werkzeug_signatur(call.name, call.arguments) for call in frisch
            }:
                signaturen[signatur] = signaturen.get(signatur, 0) + 1
            current_usage.tool_calls = frisch
            deferred_calls.extend(wiederholt)

            zustand["rounds"] = int(zustand.get("rounds", 0)) + 1
            if zustand["rounds"] > MAX_TOOL_ROUNDS:
                # Ein Assistent, der abbricht *weil* er gruendlich war, ist
                # schlechter als einer, der mit dem Vorhandenen antwortet. Ab
                # hier gibt es keine Werkzeuge mehr, aber eine Antwort.
                logger.info(
                    "AI-Werkzeugrunden erschoepft, letzte Antwort ohne Werkzeuge run_id=%s",
                    run_id,
                )
                tools = None
                current_usage = StreamUsage()
                continue
            if not current_usage.tool_calls and not deferred_calls:
                break
            followup, used_tools = _tool_followup_messages(
                user_id=user_id,
                conversation_id=conversation_id,
                tool_calls=current_usage.tool_calls,
                deferred=deferred_calls,
            )
            provider_messages.extend(followup)
            for used in used_tools:
                ai_run_broker.veroeffentlichen(run_id, "tool", used)
            current_usage = StreamUsage()

        zustand["tool_signatures"] = signaturen
        zustand["provider_messages"] = provider_messages
        complete_content = "".join(chunks)
        estimated_actual = max(
            1,
            (message_character_count(provider_messages) + len(complete_content) + 3) // 4,
        )
        _finalize_stream(
            message_id=message_id,
            usage_event_id=vorbereitung.usage_event_id,
            content=complete_content,
            provider_total_tokens=usage.total_tokens,
            estimated_actual_tokens=estimated_actual,
            failed=False,
            # Eine Rueckfrage ist eine vollwertige Antwort, und ein Vorschlag
            # ebenso. Ohne das galten sie als "nichts geliefert" — genau der
            # Fall, in dem der Chat "Keine Antwort erhalten" anzeigte.
            had_output=bool(chunks) or gestellte_frage is not None or geparkt,
            token_price_cents_per_million=vorbereitung.token_price_cents_per_million,
            reasoning="".join(thoughts),
            question=gestellte_frage,
        )
        abgerechnet = True
        ai_run_broker.veroeffentlichen(run_id, "done", {"message_id": message_id})

        if geparkt:
            _lauf_abschliessen(
                run_id,
                status="waiting_confirmation",
                stop_reason="awaiting_confirmation",
                zustand=zustand,
            )
            return
        if gestellte_frage is not None:
            _lauf_abschliessen(
                run_id, status="waiting_user", stop_reason="question", zustand=zustand
            )
            return
        _lauf_abschliessen(run_id, status="completed", stop_reason="done", zustand=zustand)

        # Erst jetzt falten — der Benutzer hat seine Antwort und wartet nicht auf
        # die Zusammenfassung.
        try:
            from services.ai_compaction_service import compact_conversation

            if await compact_conversation(
                client=client,
                user_id=user_id,
                conversation_id=conversation_id,
                provider_id=vorbereitung.provider.id,
            ):
                ai_run_broker.veroeffentlichen(
                    run_id, "compacted", {"conversation_id": conversation_id}
                )
        except Exception as exc:
            logger.info("AI-Kompression uebersprungen error=%s", type(exc).__name__)
    except asyncio.CancelledError:
        # Der Prozess faehrt herunter. Nicht mehr als ehrlich abschliessen.
        if not abgerechnet:
            _finalize_stream(
                message_id=message_id,
                usage_event_id=vorbereitung.usage_event_id,
                content="".join(chunks),
                provider_total_tokens=usage.total_tokens,
                estimated_actual_tokens=0,
                failed=True,
                had_output=bool(chunks),
                token_price_cents_per_million=vorbereitung.token_price_cents_per_million,
                reasoning="".join(thoughts),
            )
            _lauf_abschliessen(run_id, status="cancelled", stop_reason="cancelled")
        raise
    except Exception as exc:
        if isinstance(exc, AiProviderRequestError):
            code, message_key = exc.code, "ai.errors.provider"
        elif isinstance(exc, AiActionValidationError):
            code, message_key = "AI_TOOL_REJECTED", "ai.errors.toolRejected"
        else:
            logger.warning("AI-Lauf fehlgeschlagen error=%s", type(exc).__name__)
            code, message_key = "AI_STREAM_FAILED", "ai.errors.unavailable"
        if not abgerechnet:
            _finalize_stream(
                message_id=message_id,
                usage_event_id=vorbereitung.usage_event_id,
                content="".join(chunks),
                provider_total_tokens=usage.total_tokens,
                estimated_actual_tokens=0,
                failed=True,
                had_output=bool(chunks),
                token_price_cents_per_million=vorbereitung.token_price_cents_per_million,
                reasoning="".join(thoughts),
            )
        ai_run_broker.veroeffentlichen(run_id, "error", {"code": code, "message_key": message_key})
        _lauf_abschliessen(run_id, status="failed", stop_reason=code)


def lauf_beginnen(
    db,
    *,
    user: User,
    conversation,
    provider: AiProvider,
    request_id: UUID,
    content: str,
    reasoning: bool,
) -> tuple[AiRun | None, tuple[str, str] | None]:
    """Legt einen Lauf an: Benutzernachricht, Kontingent, Antwortnachricht.

    Bewusst **synchron im Request** und nicht im Hintergrund. Ein
    ueberschrittenes Kontingent, ein fehlender Schluessel oder eine doppelt
    gesendete Anfrage sind Dinge, die der Benutzer sofort erfahren soll — nicht
    Sekunden spaeter aus einem Ereignisstrom. Erst wenn all das durch ist,
    beginnt die eigentliche Arbeit, und ab da haengt sie an nichts mehr.
    """
    safe_content = redact_sensitive_text(content).strip()
    if not safe_content:
        return None, ("AI_MESSAGE_EMPTY", "ai.errors.empty")
    try:
        # Wer eine neue Nachricht schreibt, statt einen Vorschlag zu bestaetigen,
        # hat die Richtung gewechselt. Ein alter, geparkter Lauf darf danach
        # nicht mehr in denselben Chat weiterschreiben.
        ai_run_service.offene_laeufe_abbrechen(db, conversation_id=conversation.id)

        db.add(AiMessage(
            id=str(uuid4()),
            conversation_id=conversation.id,
            role="user",
            content=safe_content,
            status="complete",
        ))
        db.flush()
        provider_messages = build_provider_messages(db, conversation, query=safe_content)
        estimated_tokens = estimate_reserved_tokens(provider_messages)
        usage_event = reserve_ai_usage(
            db,
            user,
            request_id=request_id,
            estimated_tokens=estimated_tokens,
            estimated_cost_microunits=estimate_cost_microunits(provider, estimated_tokens),
            server_id=None,
            provider_id=provider.id,
            model=provider.default_model,
        )
        message_id = str(uuid4())
        db.add(AiMessage(
            id=message_id,
            conversation_id=conversation.id,
            role="assistant",
            content="",
            status="streaming",
            provider_id=provider.id,
            model=provider.default_model,
            request_id=str(request_id),
        ))
        conversation.updated_at = datetime.now(timezone.utc)

        zustand = ai_run_service.leerer_zustand(provider_messages, request_id=str(request_id))
        zustand["usage_event_id"] = usage_event.id
        run = ai_run_service.lauf_anlegen(
            db,
            conversation_id=conversation.id,
            user_id=user.id,
            provider_id=provider.id,
            message_id=message_id,
            reasoning=reasoning,
            zustand=zustand,
        )
        db.commit()
        return run, None
    except IntegrityError:
        db.rollback()
        return None, ("AI_REQUEST_CONFLICT", "ai.errors.requestConflict")
    except AiUsageConflict:
        db.rollback()
        return None, ("AI_REQUEST_CONFLICT", "ai.errors.requestConflict")
    except AiQuotaExceeded as exc:
        db.rollback()
        return None, (f"AI_QUOTA_{exc.reason.upper()}", "ai.errors.quota")
    except DisSidecarError:
        db.rollback()
        return None, ("AI_CREDENTIAL_UNAVAILABLE", "ai.errors.credential")
    except Exception as exc:
        db.rollback()
        logger.warning("AI-Lauf konnte nicht beginnen error=%s", type(exc).__name__)
        return None, ("AI_PREPARATION_FAILED", "ai.errors.unavailable")


async def lauf_verfolgen(run_id: str, *, abo=None) -> AsyncIterator[str]:
    """Der Datenstrom zum Browser — ein **Fenster** auf den Lauf, nicht sein Motor.

    Bricht diese Verbindung ab, passiert dem Lauf nichts. Genau das war die
    Beschwerde: *"wenn ich den Browser schliesse, bricht die Anfrage ab."* Sie
    bricht jetzt nur noch die Anzeige ab.

    Wer sich spaeter wieder anhaengt, bekommt zuerst einen ``snapshot`` mit dem
    vollstaendigen bisherigen Stand und danach die Fortsetzung live.
    """
    # ``abo`` kommt vom Endpunkt, wenn dieser bereits **vor** dem Start des Laufs
    # abonniert hat. Das schliesst ein Wettrennen, das sonst unvermeidbar waere:
    # der Lauf arbeitet auf der Ereignisschleife los, waehrend der Rumpf der
    # Antwort erst beim ersten Lesen anlaeuft — die ersten Zeichen waeren durch,
    # bevor jemand zuhoert.
    abo = abo or ai_run_broker.abonnieren(run_id)
    if abo is None:
        # Der Lauf arbeitet in diesem Prozess nicht (mehr). Der Verlauf steht in
        # der Datenbank; die Oberflaeche laedt ihn ohnehin beim Oeffnen.
        yield sse_event("run", {"run_id": run_id, "status": _lauf_status(run_id), "live": False})
        return
    abzug, warteschlange = abo
    yield sse_event("snapshot", abzug.als_ereignis())
    if abzug.status != "running":
        # Der Lauf ruht bereits — er wartet auf einen Menschen oder ist fertig.
        # Die Verbindung offenzuhalten waere ein Warten auf nichts, und im
        # Browser bliebe die Eingabe gesperrt, solange "es laeuft noch" gilt.
        ai_run_broker.abmelden(run_id, warteschlange)
        return
    try:
        while True:
            # Erst leerlaufen lassen, dann aufhoeren. Die Reihenfolge ist der
            # ganze Punkt: der Rumpf einer StreamingResponse laeuft oft erst an,
            # wenn der Lauf schon fertig ist. Ein "laeuft der noch?" vor dem
            # Auslesen wuerde dann eine volle Warteschlange wegwerfen und nur
            # den (leeren) Abzug vom Zeitpunkt des Abonnements zeigen.
            if warteschlange.empty() and not ai_run_broker.laeuft(run_id):
                break
            ereignis, daten = await warteschlange.get()
            if ereignis is None:
                break
            if ereignis == "segment":
                # Ein neues Segment beginnt: der Text davor gehoert zur
                # abgeschlossenen Nachricht und darf nicht weiterwachsen.
                yield sse_event("segment", daten)
                continue
            yield sse_event(ereignis, daten)
            if ereignis == "run" and daten.get("status") != "running":
                # Fertig **oder** geparkt. Beides beendet die Anzeige: ein
                # geparkter Lauf tut von selbst nichts mehr, und eine offene
                # Verbindung wuerde im Browser als "arbeitet noch" gelesen —
                # die Eingabe bliebe gesperrt, obwohl der Mensch dran ist.
                break
    finally:
        ai_run_broker.abmelden(run_id, warteschlange)


def _lauf_status(run_id: str) -> str:
    with SessionLocal() as db:
        run = db.get(AiRun, run_id)
        return run.status if run is not None else "failed"
