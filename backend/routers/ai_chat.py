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
from models import AiMessage, AiProvider, User
from schemas.ai_chat import (
    AiChatRequest,
    AiContextStatus,
    AiConversationDetail,
    AiConversationResponse,
    AiMessageEdit,
    AiMessageEditResponse,
    AiMessageResponse,
    AiQuestionPayload,
    AiRunResponse,
)
from services import (
    ai_chat_service,
    ai_compaction_service,
    ai_context_service,
    ai_context_window,
    ai_reasoning,
    ai_run_broker,
    ai_run_service,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_stream_service import lauf_beginnen, lauf_verfolgen, sse_event


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


def _question(message: AiMessage) -> AiQuestionPayload | None:
    """Die gespeicherte Rueckfrage, falls diese Nachricht eine gestellt hat.

    Eine unlesbare Zeile wird uebergangen statt den ganzen Verlauf mitzureissen —
    im schlimmsten Fall fehlt eine Frage aus der Vergangenheit, der Chat bleibt
    benutzbar.
    """
    if not message.question_json:
        return None
    try:
        return AiQuestionPayload.model_validate_json(message.question_json)
    except ValueError:
        return None


def _message_response(message: AiMessage) -> AiMessageResponse:
    return AiMessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        reasoning=message.reasoning,
        question=_question(message),
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


@router.get("/context", response_model=AiContextStatus)
async def get_context_status(
    request: Request,
    provider_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiContextStatus:
    """Wie voll der Kontext ist, gemessen am Fenster des gewaehlten Modells.

    Der Provider steht im Query und nicht im Lauf, weil die Frage schon **vor**
    der ersten Nachricht beantwortet werden muss: die Oberflaeche zeigt den Ring
    von Anfang an, und beim Umschalten des Modells aendert sich die Antwort
    sofort, ohne dass jemand etwas gesendet haette.

    Die Belegung kommt aus ``geschaetzte_belegung`` und nicht aus einem echten
    Kontextaufbau. Ein Aufbau zoege Redaction, Memory-Auswahl und
    Skill-Verzeichnis ueber den ganzen Verlauf — bei jedem Blick auf den Ring,
    und der wird nach jeder Antwort neu geholt.
    """
    provider = db.get(AiProvider, provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()

    fenster = await ai_context_window.ermitteln(
        request.app.state.ai_http_client, provider
    )
    context_chars = fenster.zeichen if fenster.bekannt else None
    grenzen = ai_context_service.teilbudgets(context_chars)
    belegt = ai_context_service.geschaetzte_belegung(db, conversation, grenzen)
    je_token = ai_context_window.ZEICHEN_JE_TOKEN
    return AiContextStatus(
        known=fenster.bekannt,
        window_tokens=fenster.fenster_tokens if fenster.bekannt else None,
        usable_tokens=fenster.nutzbar_tokens,
        used_tokens=belegt // je_token,
        compaction_at_tokens=ai_compaction_service.faltschwelle(context_chars) // je_token,
        compaction_percent=ai_context_window.schwelle_prozent(),
        summarized=bool(conversation.summary),
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


@router.put("/messages/{message_id}", response_model=AiMessageEditResponse)
def edit_message(
    message_id: str,
    payload: AiMessageEdit,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> AiMessageEditResponse:
    """Nimmt eine eigene Nachricht zurueck, damit sie neu gestellt werden kann.

    Der Endpunkt sendet **nicht**. Er raeumt nur auf: die Nachricht und alles
    Spaetere verschwinden, danach schickt die Oberflaeche den neuen Text ueber
    den gewohnten Streamweg. Zwei Schritte statt einem, weil das Senden eine
    Kontingentreservierung, eine Anbieterwahl und einen Stream braucht — all
    das haette hier nichts verloren.

    Der neue Text wird trotzdem entgegengenommen und geprueft: eine Bearbeitung
    abzulehnen, *nachdem* der halbe Verlauf weg ist, waere die schlechtere
    Reihenfolge.
    """
    safe_content = redact_sensitive_text(payload.content).strip()
    if not safe_content:
        raise HTTPException(status_code=422, detail="Nachricht ist leer")

    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    message = ai_chat_service.owned_message(db, conversation, message_id)
    removed = ai_chat_service.truncate_from(db, conversation, message)
    db.commit()
    return AiMessageEditResponse(removed=removed)


async def _replay_message(message: AiMessage) -> AsyncIterator[str]:
    yield sse_event("message", {"message_id": message.id, "request_id": message.request_id})
    if message.reasoning:
        yield sse_event("reasoning", {"content": message.reasoning})
    if message.content:
        yield sse_event("delta", {"content": message.content})
    # Endete der Zug mit einer Rueckfrage, gehoert sie auch in die Wiedergabe —
    # sonst saehe der Benutzer nach einem erneuten Absenden derselben Anfrage
    # eine Antwort ohne die Frage, auf die er antworten soll.
    frage = _question(message)
    if frage is not None:
        yield sse_event("question", frage.model_dump())
    yield sse_event("done", {"message_id": message.id, "replayed": True})


async def _fehlerstrom(code: str, message_key: str) -> AsyncIterator[str]:
    """Ein Fehler, der vor dem Lauf auftrat — es gibt nichts zu verfolgen."""
    yield sse_event("error", {"code": code, "message_key": message_key})


@router.get("/run")
def get_active_run(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiRunResponse | None:
    """Der Lauf, der gerade noch etwas vorhat — oder nichts.

    Die Oberflaeche fragt das beim Oeffnen: laeuft da noch etwas von vorhin?
    Damit haengt sie sich nach einem Seitenwechsel oder einem Neustart des
    Browsers wieder an, statt eine abgebrochene Antwort zu zeigen.
    """
    run = ai_run_service.aktiver_lauf(db, user_id=user.id)
    if run is None:
        return None
    return AiRunResponse(
        id=run.id,
        status=run.status,
        stop_reason=run.stop_reason,
        message_id=run.message_id,
        live=ai_run_broker.laeuft(run.id),
        created_at=run.created_at,
    )


@router.get("/run/{run_id}/stream")
def attach_run(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> StreamingResponse:
    """Haengt sich an einen laufenden Lauf an — auch Minuten spaeter.

    Kein CSRF-Schutz noetig und keiner moeglich: das ist ein GET, der nichts
    veraendert. Die Zugehoerigkeit wird ueber ``eigener_lauf`` geprueft, ein
    fremder Lauf ist schlicht nicht zu finden.
    """
    run = ai_run_service.eigener_lauf(db, run_id, user)
    if run is None:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")
    return StreamingResponse(
        lauf_verfolgen(run.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/stream")
async def stream_message(
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
    if provider.requires_api_key and not provider.operator_api_key_encrypted:
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
        # Die Denkvorgabe wird **hier** festgelegt und nicht erst beim Senden:
        # `lauf_beginnen` laeuft synchron, der Modellkatalog ist ein
        # HTTP-Abruf. Wichtiger noch — der geklemmte Wert gehoert in den Lauf,
        # damit eine Fortsetzung nach einer Bestaetigung dieselbe Tiefe
        # verwendet wie der erste Zug.
        denken, stufe = await ai_reasoning.vorgabe(
            request.app.state.ai_http_client,
            db,
            user=user,
            provider=provider,
            aktiv=payload.reasoning,
            wunsch=payload.reasoning_effort,
        )
        # Aus demselben Grund hier und nicht in `lauf_beginnen`: das
        # Kontextfenster steht im Modellkatalog, und den zu befragen ist ein
        # HTTP-Abruf. Es gehoert ausserdem in den Lauf — eine Fortsetzung nach
        # einer Bestaetigung muss mit demselben Budget rechnen wie der erste
        # Zug, auch wenn der Betreiber zwischendurch das Modell wechselt.
        fenster = await ai_context_window.ermitteln(
            request.app.state.ai_http_client, provider
        )
        run, fehler = lauf_beginnen(
            db,
            user=user,
            conversation=conversation,
            provider=provider,
            request_id=payload.request_id,
            content=safe_content,
            reasoning=denken,
            reasoning_effort=stufe,
            # Ein unbekanntes Fenster reist als `None` weiter und nicht als
            # Rueckfallzahl: nur so kann die Kompression den Unterschied
            # zwischen "kleines Fenster" und "kein Wissen" noch sehen.
            context_chars=fenster.zeichen if fenster.bekannt else None,
        )
        if run is None:
            code, message_key = fehler or ("AI_PREPARATION_FAILED", "ai.chat.errors.unavailable")
            stream = _fehlerstrom(code, message_key)
        else:
            # Reihenfolge ist hier alles: Kanal auf, **abonnieren**, dann erst
            # die Arbeit starten.
            #
            # Der Rumpf einer StreamingResponse laeuft erst an, wenn Starlette
            # ihn zu lesen beginnt — der Lauf arbeitet da laengst. Wuerde erst
            # dort abonniert, waeren die ersten Zeichen schon durch. Abonniert
            # wird deshalb hier, synchron, vor dem Start.
            ai_run_broker.eroeffnen(run.id)
            abo = ai_run_broker.abonnieren(run.id)
            ai_run_service.lauf_starten(run.id)
            stream = lauf_verfolgen(run.id, abo=abo)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
