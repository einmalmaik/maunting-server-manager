"""Die eine persistente AI-Unterhaltung eines Benutzers mit POST-SSE.

Es gibt bewusst keine Routen zum Auflisten, Anlegen oder Loeschen von
Unterhaltungen mehr. Der Assistent hat genau einen Chat; geloescht wird der
*Verlauf*, nicht die Unterhaltung.
"""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiMessage, AiProvider, AiUserCredential, User
from schemas.ai_chat import (
    AiChatRequest,
    AiConversationDetail,
    AiConversationResponse,
    AiMessageResponse,
)
from services import ai_chat_service
from services.ai_redaction import redact_sensitive_text
from services.ai_stream_service import sse_event, stream_conversation_reply


router = APIRouter(prefix="/api/ai/conversation", tags=["ai-chat"])

# Der sichtbare Verlauf. Aeltere Nachrichten bleiben gespeichert und fliessen
# ueber die Zusammenfassung weiter in den Kontext ein.
HISTORY_LIMIT = 200


def _conversation_response(conversation) -> AiConversationResponse:
    return AiConversationResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_response(message: AiMessage) -> AiMessageResponse:
    return AiMessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        reasoning=message.reasoning,
        status=message.status,
        provider_id=message.provider_id,
        model=message.model,
        created_at=message.created_at,
    )


@router.get("", response_model=AiConversationDetail)
def get_conversation(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiConversationDetail:
    """Liefert die Unterhaltung des Benutzers und legt sie beim ersten Aufruf an."""
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()
    db.refresh(conversation)
    messages = (
        db.query(AiMessage)
        .filter(AiMessage.conversation_id == conversation.id)
        .order_by(AiMessage.created_at.desc())
        .limit(HISTORY_LIMIT)
        .all()
    )
    base = _conversation_response(conversation)
    return AiConversationDetail(
        **base.model_dump(),
        messages=[_message_response(message) for message in reversed(messages)],
    )


@router.delete("/messages", status_code=status.HTTP_204_NO_CONTENT)
def clear_conversation_history(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> Response:
    """Leert den Verlauf. Die Unterhaltung und das Audit bleiben bestehen."""
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    ai_chat_service.clear_history(db, conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _replay_message(message: AiMessage) -> AsyncIterator[str]:
    yield sse_event("message", {"message_id": message.id, "request_id": message.request_id})
    if message.reasoning:
        yield sse_event("reasoning", {"content": message.reasoning})
    if message.content:
        yield sse_event("delta", {"content": message.content})
    yield sse_event("done", {"message_id": message.id, "replayed": True})


@router.post("/messages/stream")
def stream_message(
    payload: AiChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> StreamingResponse:
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()
    provider = db.get(AiProvider, payload.provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    has_user_key = (
        db.query(AiUserCredential.id)
        .filter(
            AiUserCredential.user_id == user.id,
            AiUserCredential.provider_id == provider.id,
        )
        .first()
        is not None
    )
    if provider.requires_api_key and not has_user_key and not provider.operator_api_key_encrypted:
        raise HTTPException(status_code=409, detail="Fuer diesen Provider ist kein API-Key konfiguriert")

    existing = db.query(AiMessage).filter(AiMessage.request_id == str(payload.request_id)).first()
    if existing is not None:
        if existing.conversation_id != conversation.id or existing.provider_id != provider.id:
            raise HTTPException(status_code=409, detail="AI-Request-ID wurde widerspruechlich wiederverwendet")
        if existing.status == "complete":
            stream = _replay_message(existing)
        else:
            raise HTTPException(status_code=409, detail="AI-Anfrage ist bereits aktiv oder fehlgeschlagen")
    else:
        safe_content = redact_sensitive_text(payload.content).strip()
        if not safe_content:
            raise HTTPException(status_code=400, detail="Nachricht ist nach Sicherheitsfilter leer")
        stream = stream_conversation_reply(
            client=request.app.state.ai_http_client,
            user_id=user.id,
            conversation_id=conversation.id,
            provider_id=provider.id,
            request_id=payload.request_id,
            content=safe_content,
            reasoning=payload.reasoning,
        )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
