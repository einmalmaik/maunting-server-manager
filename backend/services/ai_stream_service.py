"""Kurzlebige DB-Transaktionen rund um einen externen AI-Stream."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import AsyncIterator
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import AiActionProposal, AiMessage, AiProvider, AiToolResult, AiUsageEvent, User
from services.ai_chat_service import get_owned_conversation
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
# Bis zu drei aufeinanderfolgende Read-Runden. Vorher war genau eine erlaubt:
# ein Ablauf wie "Kapazitaet lesen → Blueprints lesen → Server vorschlagen" war
# damit unmoeglich, weil die zweite Runde nur noch Write-Tools akzeptierte und
# ein legitimer zweiter Lesezugriff den ganzen Stream abbrach.
# Vier Leserunden. Gemessen an einer echten Netzwerkdiagnose reichen drei
# nicht: das Modell geht list_my_servers → read_server_network →
# read_server_status → check_server_reachability, und erst der letzte Schritt
# ist die eigentliche Messung. Wird die Grenze erreicht, bricht der Stream
# nicht ab — das Modell bekommt einen letzten Durchgang ohne Werkzeuge.
MAX_TOOL_ROUNDS = 4
# Wie viele Schreibrunden eine einzelne Nachricht ausloesen darf. Zwei,
# damit "pass die Config an und starte danach" in zwei aufeinander
# aufbauenden Schritten laufen kann. Mehr braucht keine Bitte, die ein
# Mensch in einem Satz formuliert — und jede weitere waere eine Runde, in
# der niemand mehr mitliest.
MAX_WRITE_ROUNDS = 2


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
    *, user_id: int, conversation_id: str, tool_calls, correlation_id: str
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


async def stream_conversation_reply(
    *,
    client: httpx.AsyncClient,
    user_id: int,
    conversation_id: str,
    provider_id: int,
    request_id: UUID,
    content: str,
    reasoning: bool = False,
) -> AsyncIterator[str]:
    """Persistiert zuerst, streamt ohne offene DB-Session und finalisiert kurz."""
    safe_content = redact_sensitive_text(content).strip()
    if not safe_content:
        yield sse_event("error", {"code": "AI_MESSAGE_EMPTY", "message_key": "ai.errors.empty"})
        return

    assistant_id = str(uuid4())
    usage_event_id: int | None = None
    provider: AiProvider | None = None
    provider_messages: list[dict[str, str]] = []
    api_key: str | None = None
    token_price_cents_per_million: int | None = None
    preparation_error: tuple[str, str] | None = None
    try:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is None or not user.is_active:
                preparation_error = ("AI_ACCESS_REVOKED", "ai.errors.access")
            else:
                conversation = get_owned_conversation(db, conversation_id, user)
                provider = db.get(AiProvider, provider_id)
                if conversation is None or provider is None or not provider.enabled:
                    preparation_error = ("AI_RESOURCE_NOT_FOUND", "ai.errors.notFound")
                else:
                    api_key = resolve_api_key(db, provider, user.id)
                    if provider.requires_api_key and not api_key:
                        preparation_error = ("AI_PROVIDER_KEY_MISSING", "ai.errors.keyMissing")
                    else:
                        user_message = AiMessage(
                            id=str(uuid4()),
                            conversation_id=conversation.id,
                            role="user",
                            content=safe_content,
                            status="complete",
                        )
                        db.add(user_message)
                        db.flush()
                        # Die gerade gestellte Frage steuert mit, welche
                        # Memory-Eintraege bei knappem Platz ueberleben.
                        provider_messages = build_provider_messages(
                            db, conversation, query=safe_content
                        )
                        estimated_tokens = estimate_reserved_tokens(provider_messages)
                        # Kosten werden aus dem vom Betreiber gepflegten
                        # Providerpreis abgeleitet. Ohne Preis bleibt der Wert
                        # null — dann greift auch das Kostenlimit bewusst nicht.
                        usage_event = reserve_ai_usage(
                            db,
                            user,
                            request_id=request_id,
                            estimated_tokens=estimated_tokens,
                            estimated_cost_microunits=estimate_cost_microunits(
                                provider, estimated_tokens
                            ),
                            # Die Unterhaltung hat keinen Serverbezug mehr. Der
                            # Verbrauch je Server entsteht ab jetzt an den
                            # Aktionsvorschlaegen, die ihre `server_id` tragen.
                            server_id=None,
                            provider_id=provider.id,
                            model=provider.default_model,
                        )
                        token_price_cents_per_million = provider.token_price_cents_per_million
                        assistant = AiMessage(
                            id=assistant_id,
                            conversation_id=conversation.id,
                            role="assistant",
                            content="",
                            status="streaming",
                            provider_id=provider.id,
                            model=provider.default_model,
                            request_id=str(request_id),
                        )
                        db.add(assistant)
                        conversation.updated_at = datetime.now(timezone.utc)
                        db.commit()
                        usage_event_id = usage_event.id
                        db.refresh(provider)
                        db.expunge(provider)
    except IntegrityError:
        preparation_error = ("AI_REQUEST_CONFLICT", "ai.errors.requestConflict")
    except AiUsageConflict:
        preparation_error = ("AI_REQUEST_CONFLICT", "ai.errors.requestConflict")
    except AiQuotaExceeded as exc:
        preparation_error = (f"AI_QUOTA_{exc.reason.upper()}", "ai.errors.quota")
    except DisSidecarError:
        preparation_error = ("AI_CREDENTIAL_UNAVAILABLE", "ai.errors.credential")
    except Exception as exc:
        logger.warning("AI stream preparation failed error=%s", type(exc).__name__)
        preparation_error = ("AI_PREPARATION_FAILED", "ai.errors.unavailable")

    if preparation_error is not None:
        code, message_key = preparation_error
        yield sse_event("error", {"code": code, "message_key": message_key})
        return

    if provider is None or usage_event_id is None:
        return
    yield sse_event("message", {"message_id": assistant_id, "request_id": str(request_id)})
    chunks: list[str] = []
    thoughts: list[str] = []
    usage = StreamUsage()
    # Merkt sich, ob die Abrechnung bereits erfolgt ist. Der Abbruchpfad darf
    # eine schon erfolgreich abgeschlossene Antwort nicht nachtraeglich als
    # fehlgeschlagen ueberschreiben.
    finalized = False
    try:
        tools = provider_tool_definitions()
        current_usage = usage
        rounds = 0
        write_rounds = 0
        # Die Rueckfrage dieses Zuges, falls eine gestellt wurde. Sie wird an
        # der Assistenten-Nachricht festgehalten, damit das Modell sie beim
        # naechsten Aufruf in der Historie wiederfindet.
        gestellte_frage: dict | None = None
        while True:
            async for chunk in stream_chat_completion(
                client,
                provider=provider,
                api_key=api_key,
                messages=provider_messages,
                usage=current_usage,
                tools=tools,
                reasoning=reasoning,
            ):
                if chunk.kind == "reasoning":
                    thoughts.append(chunk.text)
                    yield sse_event("reasoning", {"content": chunk.text})
                    continue
                chunks.append(chunk.text)
                yield sse_event("delta", {"content": chunk.text})
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
                # angeboten.
                #
                # Vier Zweige oben setzen `tools = None` und `continue`, um die
                # Schleife zu beenden. Das funktionierte nur, solange sich der
                # Anbieter daran hielt — es war eine Bitte, keine Grenze. Ein
                # Anbieter, der weiter Aufrufe schickt, hielt den Stream endlos
                # offen und verbrannte bei jedem Durchgang Tokens. Genau das
                # passiert reproduzierbar im Test
                # `test_a_tool_call_cannot_reach_a_server_the_user_may_not_see`,
                # der die Suite zum Stillstand brachte.
                #
                # Hier steht die Grenze jetzt auf unserer Seite.
                logger.warning(
                    "Anbieter meldet Werkzeugaufrufe ohne angebotene Werkzeuge, "
                    "werden verworfen conversation_id=%s anzahl=%d",
                    conversation_id, len(current_usage.tool_calls),
                )
                break

            # Eine Rueckfrage beendet den Zug: ab hier ist der Mensch dran,
            # und seine Antwort kommt als gewoehnliche Nachricht zurueck. Alles
            # andere aus derselben Runde waere verfrueht — die Antwort aendert
            # ja gerade die Grundlage.
            frage = next(
                (call for call in current_usage.tool_calls if call.name in ASK_TOOLS),
                None,
            )
            if frage is not None:
                gestellte_frage = question_payload(frage.arguments)
                yield sse_event("question", gestellte_frage)
                # Hier endet der Zug. Frueher folgte noch eine Runde ohne
                # Werkzeuge, damit das Modell den Grund der Frage nennen kann.
                # Gemessen am Betrieb war das ein Fehlgriff: der Prompt sagt dem
                # Modell, die Frage stehe bereits im Chat, also lieferte diese
                # Runde meist gar nichts — ein bezahlter Anbieteraufruf fuer
                # eine leere Blase mit "Keine Antwort erhalten" darunter.
                #
                # Was das Modell erklaeren will, schreibt es ohnehin im selben
                # Durchgang: Anbieter liefern Text und Werkzeugaufrufe zusammen,
                # und dieser Text steht bereits in `chunks`.
                break

            kinds = {
                "read" if call.name in READ_TOOLS else "write" if call.name in WRITE_TOOLS else "unknown"
                for call in current_usage.tool_calls
            }
            if kinds == {"write"}:
                # Ab hier entscheidet der Mensch (oder, bei erteilter Freigabe,
                # die Autonomiegrenze) — es folgt also keine weitere Aktion.
                proposals = _persist_write_proposals(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tool_calls=current_usage.tool_calls,
                    correlation_id=str(request_id),
                )
                for proposal in proposals:
                    yield sse_event(
                        "action" if proposal.get("autonomous") else "proposal", proposal
                    )
                # Frueher endete der Stream hier. Das hatte zwei Folgen, die im
                # Betrieb beide auffielen: die Antwortnachricht blieb leer
                # ("Keine Antwort erhalten"), und die Historie enthielt keine
                # Spur der Aktion — ein blosses "danke" wirkte im naechsten Zug
                # wie eine noch offene Bitte, und derselbe Server wurde ein
                # zweites Mal gestoppt.
                #
                # Stattdessen bekommt das Modell das Ergebnis zurueck und eine
                # letzte Runde **ohne Werkzeuge**, um den Vorgang abzuschliessen.
                # Ohne `tools = None` koennte es in dieser Runde erneut eine
                # Aktion ausloesen — genau die Schleife, die wir schliessen.
                provider_messages.extend(_write_followup_messages(
                    conversation_id=conversation_id,
                    tool_calls=current_usage.tool_calls,
                    proposals=proposals,
                ))
                write_rounds += 1
                # Eine zusammengesetzte Bitte braucht oft zwei Schritte, die
                # aufeinander aufbauen: "pass die Config an und starte ihn
                # danach". Mit nur einer Schreibrunde muesste das Modell beides
                # gleichzeitig abgeben — und koennte den Start nicht davon
                # abhaengig machen, ob die Aenderung durchging.
                #
                # Eine zweite Runde ist nur dann vertretbar, wenn die erste
                # tatsaechlich **ausgefuehrt** wurde. Wartet ein Vorschlag auf
                # den Menschen, ist der Mensch dran und nicht das Modell.
                # `executing` zaehlt mit: ein Lifecycle-Start laeuft als
                # Hintergrundjob und ist damit angestossen, nicht offen.
                executed = bool(proposals) and all(
                    proposal.get("autonomous")
                    and proposal.get("status") in {"succeeded", "executing"}
                    and not proposal.get("error_code")
                    for proposal in proposals
                )
                if not (executed and write_rounds < MAX_WRITE_ROUNDS):
                    # Ohne `tools = None` koennte das Modell endlos weiter
                    # handeln — genau die Schleife, die der Rueckfluss oben
                    # schliessen soll.
                    tools = None
                current_usage = StreamUsage()
                continue
            if "unknown" in kinds:
                # Ein Werkzeug, das weder lesend noch schreibend ist, gibt es
                # nicht — das kann nur eine erfundene Antwort sein.
                raise AiProviderRequestError("AI_PROVIDER_TOOL_SEQUENCE_INVALID")

            deferred_calls: list = []
            if kinds == {"read", "write"}:
                # Gemischte Runde. Frueher riss das den ganzen Stream ab, und
                # der Benutzer bekam statt einer Antwort einen Fehlercode — bei
                # einer zusammengesetzten Bitte ("lies die Config und pass sie
                # an") war das der Normalfall, nicht die Ausnahme.
                #
                # Jetzt laufen die Lesewerkzeuge, und die Schreibaufrufe
                # bekommen eine Absage mit Begruendung zurueck. Das Modell holt
                # sie dann in einer eigenen Runde nach. Die Trennung bleibt
                # damit erhalten — sie wird nur erklaert statt erzwungen.
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

            rounds += 1
            if rounds > MAX_TOOL_ROUNDS:
                # Frueher endete das hier mit einem Fehler. Gemessen an einer
                # echten Netzwerkdiagnose war das falsch: die Kette
                # list_my_servers → read_server_network → read_server_status →
                # check_server_reachability ist voellig legitim und riss dem
                # Benutzer die Antwort weg, obwohl die KI bereits genug wusste.
                # Ein Assistent, der abbricht *weil* er gruendlich war, ist
                # schlechter als einer, der mit dem Vorhandenen antwortet.
                #
                # Die Grenze bleibt: ab hier gibt es keine Werkzeuge mehr. Das
                # Modell bekommt einen letzten Durchgang ohne `tools` und muss
                # aus dem antworten, was es hat.
                # Die Aufrufe dieser Runde werden bewusst **nicht** mehr
                # ausgefuehrt: sonst waere die Grenze um eins verschoben. Sie
                # landen auch nicht in der Historie — ohne zugehoerige
                # Werkzeugantwort wuerden manche Anbieter die naechste Anfrage
                # ablehnen.
                logger.info(
                    "AI-Werkzeugrunden erschoepft, letzte Antwort ohne Werkzeuge "
                    "conversation_id=%s", conversation_id,
                )
                tools = None
                current_usage = StreamUsage()
                continue
            followup, used_tools = _tool_followup_messages(
                user_id=user_id,
                conversation_id=conversation_id,
                tool_calls=current_usage.tool_calls,
                deferred=deferred_calls,
            )
            provider_messages.extend(followup)
            # Sichtbar machen, was die KI gerade getan hat. Ohne das wirkt eine
            # Antwort, die aus Logs und Ports entstanden ist, wie geraten.
            for used in used_tools:
                yield sse_event("tool", used)
            current_usage = StreamUsage()
        complete_content = "".join(chunks)
        estimated_actual = max(
            1,
            (message_character_count(provider_messages) + len(complete_content) + 3)
            // 4,
        )
        _finalize_stream(
            message_id=assistant_id,
            usage_event_id=usage_event_id,
            content=complete_content,
            provider_total_tokens=usage.total_tokens,
            estimated_actual_tokens=estimated_actual,
            failed=False,
            # Eine Rueckfrage ist eine vollwertige Antwort. Ohne dieses Flag
            # gilt ein Zug ohne Fliesstext als "nichts geliefert" — genau der
            # Fall, in dem der Chat "Keine Antwort erhalten" anzeigte.
            had_output=bool(chunks) or gestellte_frage is not None,
            token_price_cents_per_million=token_price_cents_per_million,
            reasoning="".join(thoughts),
            question=gestellte_frage,
        )
        finalized = True
        yield sse_event("done", {"message_id": assistant_id})

        # Erst jetzt falten — der Benutzer hat seine Antwort und wartet nicht
        # auf die Zusammenfassung. Scheitert sie, bleibt der Chat unveraendert
        # und der naechste Durchlauf versucht es erneut.
        try:
            from services.ai_compaction_service import compact_conversation

            if await compact_conversation(
                client=client,
                user_id=user_id,
                conversation_id=conversation_id,
                provider_id=provider.id,
            ):
                yield sse_event("compacted", {"conversation_id": conversation_id})
        except Exception as exc:
            logger.info("AI-Kompression uebersprungen error=%s", type(exc).__name__)
    except (asyncio.CancelledError, GeneratorExit):
        # Bricht der Browser die Verbindung ab, wirft Python beim Aufraeumen des
        # Generators ein GeneratorExit. Das ist kein `Exception` und lief bisher
        # durch alle Handler hindurch: Nachricht blieb "streaming", Reservierung
        # blieb "reserved" und belegte bis zum Prozessneustart Kontingent und
        # einen Nebenlaeufigkeitsplatz.
        if not finalized:
            _finalize_stream(
                message_id=assistant_id,
                usage_event_id=usage_event_id,
                content="".join(chunks),
                provider_total_tokens=usage.total_tokens,
                estimated_actual_tokens=0,
                failed=True,
                had_output=bool(chunks),
                token_price_cents_per_million=token_price_cents_per_million,
                reasoning="".join(thoughts),
            )
        raise
    except AiProviderRequestError as exc:
        _finalize_stream(
            message_id=assistant_id,
            usage_event_id=usage_event_id,
            content="".join(chunks),
            provider_total_tokens=usage.total_tokens,
            estimated_actual_tokens=0,
            failed=True,
            had_output=bool(chunks),
            token_price_cents_per_million=token_price_cents_per_million,
            reasoning="".join(thoughts),
        )
        yield sse_event("error", {"code": exc.code, "message_key": "ai.errors.provider"})
    except AiActionValidationError:
        _finalize_stream(
            message_id=assistant_id,
            usage_event_id=usage_event_id,
            content="".join(chunks),
            provider_total_tokens=usage.total_tokens,
            estimated_actual_tokens=0,
            failed=True,
            had_output=bool(chunks),
            token_price_cents_per_million=token_price_cents_per_million,
            reasoning="".join(thoughts),
        )
        yield sse_event("error", {"code": "AI_TOOL_REJECTED", "message_key": "ai.errors.toolRejected"})
    except Exception as exc:
        logger.warning("AI stream finalization failed error=%s", type(exc).__name__)
        _finalize_stream(
            message_id=assistant_id,
            usage_event_id=usage_event_id,
            content="".join(chunks),
            provider_total_tokens=usage.total_tokens,
            estimated_actual_tokens=0,
            failed=True,
            had_output=bool(chunks),
            token_price_cents_per_million=token_price_cents_per_million,
            reasoning="".join(thoughts),
        )
        yield sse_event("error", {"code": "AI_STREAM_FAILED", "message_key": "ai.errors.unavailable"})
