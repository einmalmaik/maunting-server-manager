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
from models import AiMessage, AiProvider, AiUsageEvent, User
from services.ai_chat_service import get_owned_conversation
from services.ai_action_service import (
    AiActionValidationError,
    READ_TOOLS,
    WRITE_TOOLS,
    create_proposal,
    execute_read_tool,
    provider_tool_definitions,
)
from services.ai_context_service import (
    build_provider_messages,
    estimate_reserved_tokens,
    message_character_count,
    redact_sensitive_text,
)
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
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


logger = logging.getLogger(__name__)
MAX_TOOL_CALLS = 4


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
    *, user_id: int, conversation_id: str, tool_calls
) -> list[dict]:
    if len(tool_calls) > MAX_TOOL_CALLS or any(call.name not in READ_TOOLS for call in tool_calls):
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
                for call in tool_calls
            ],
        }
        results: list[dict] = [assistant_call]
        for call in tool_calls:
            value = execute_read_tool(
                db,
                user=user,
                conversation=conversation,
                tool_name=call.name,
                arguments=call.arguments,
            )
            results.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(value, ensure_ascii=True, separators=(",", ":")),
            })
        return results


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
        return [
            {
                "id": proposal.id,
                "server_id": proposal.server_id,
                "tool_name": proposal.tool_name,
                "preview": json.loads(proposal.preview_json),
                "status": proposal.status,
            }
            for proposal in proposals
        ]


async def stream_conversation_reply(
    *,
    client: httpx.AsyncClient,
    user_id: int,
    conversation_id: str,
    provider_id: int,
    request_id: UUID,
    content: str,
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
    server_id: int | None = None
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
                    server_id = conversation.server_id
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
                        provider_messages = build_provider_messages(db, conversation)
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
                            server_id=conversation.server_id,
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
    usage = StreamUsage()
    # Merkt sich, ob die Abrechnung bereits erfolgt ist. Der Abbruchpfad darf
    # eine schon erfolgreich abgeschlossene Antwort nicht nachtraeglich als
    # fehlgeschlagen ueberschreiben.
    finalized = False
    try:
        tools = provider_tool_definitions() if server_id is not None else None
        async for delta in stream_chat_completion(
            client,
            provider=provider,
            api_key=api_key,
            messages=provider_messages,
            usage=usage,
            tools=tools,
        ):
            chunks.append(delta)
            yield sse_event("delta", {"content": delta})
        if usage.tool_calls:
            kinds = {
                "read" if call.name in READ_TOOLS else "write" if call.name in WRITE_TOOLS else "unknown"
                for call in usage.tool_calls
            }
            if kinds == {"read"}:
                provider_messages.extend(_tool_followup_messages(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tool_calls=usage.tool_calls,
                ))
                followup_usage = StreamUsage()
                async for delta in stream_chat_completion(
                    client,
                    provider=provider,
                    api_key=api_key,
                    messages=provider_messages,
                    usage=followup_usage,
                    tools=tools,
                ):
                    chunks.append(delta)
                    yield sse_event("delta", {"content": delta})
                if followup_usage.tool_calls:
                    proposals = _persist_write_proposals(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        tool_calls=followup_usage.tool_calls,
                        correlation_id=str(request_id),
                    )
                    for proposal in proposals:
                        yield sse_event("proposal", proposal)
                usage.total_tokens = (
                    usage.total_tokens + followup_usage.total_tokens
                    if usage.total_tokens is not None and followup_usage.total_tokens is not None
                    else usage.total_tokens or followup_usage.total_tokens
                )
            elif kinds == {"write"}:
                proposals = _persist_write_proposals(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tool_calls=usage.tool_calls,
                    correlation_id=str(request_id),
                )
                for proposal in proposals:
                    yield sse_event("proposal", proposal)
            else:
                raise AiProviderRequestError("AI_PROVIDER_TOOL_SEQUENCE_INVALID")
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
            had_output=bool(chunks),
            token_price_cents_per_million=token_price_cents_per_million,
        )
        finalized = True
        yield sse_event("done", {"message_id": assistant_id})
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
        )
        yield sse_event("error", {"code": "AI_STREAM_FAILED", "message_key": "ai.errors.unavailable"})
