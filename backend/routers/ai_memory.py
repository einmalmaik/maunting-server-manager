"""Einsehbares, editierbares und abschaltbares AI-Memory."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiMemoryEntry, AiMemoryPreference, User
from schemas.ai_memory import (
    AiMemoryClearResponse,
    AiMemoryNoticeAnswer,
    AiMemoryPersonalPage,
    AiMemoryPreferenceResponse,
    AiMemoryPreferenceWrite,
    AiMemoryResponse,
    AiMemoryWrite,
    MemoryScope,
)
from services import ai_memory_service
from services.dis_client import DisSidecarError


router = APIRouter(prefix="/api/ai/memory", tags=["ai-memory"])


def _response(row: AiMemoryEntry, value: str) -> AiMemoryResponse:
    return AiMemoryResponse(
        id=row.id, scope=row.scope, server_id=row.server_id, team_id=row.team_id,
        key=row.key,
        value=value, origin=row.origin, use_count=row.use_count,
        last_used_at=row.last_used_at,
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.get("", response_model=list[AiMemoryResponse])
def list_memory(
    scope: MemoryScope = Query(...),
    server_id: int | None = Query(default=None, ge=1),
    team_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
) -> list[AiMemoryResponse]:
    try:
        return [_response(row, value) for row, value in ai_memory_service.list_entries(
            db, user, scope, server_id, team_id
        )]
    except DisSidecarError as exc:
        raise HTTPException(status_code=503, detail="Memory ist nicht verfuegbar") from exc


@router.get("/personal", response_model=AiMemoryPersonalPage)
def list_personal_memory(
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
) -> AiMemoryPersonalPage:
    """Eine Seite von allem, was diesem Benutzer selbst gehoert.

    `GET /?scope=server` verlangt eine konkrete `server_id`; wer alle seine
    Servernotizen sehen will, muesste die Server erst raten. Genau deshalb waren
    sie ueber die Oberflaeche bisher unerreichbar, obwohl die KI sie schreibt
    und sie in jedem Gespraech mitlaufen.

    ``offset`` ist der einzige Stellhebel von aussen. Die Seitengroesse bleibt
    beim Dienst: sie wird in Sidecar-Roundtrips bezahlt, und ein ``limit`` in
    der Anfrage waere die Einladung, sich 5.000 Entschluesselungen auf einmal zu
    bestellen. Was gerade gilt, steht als ``limit`` in der Antwort — daraus
    rechnet die Oberflaeche Seitenzahl und naechsten Offset.
    """
    try:
        seite = ai_memory_service.personal_entries(db, user, offset=offset)
    except DisSidecarError as exc:
        raise HTTPException(status_code=503, detail="Memory ist nicht verfuegbar") from exc
    return AiMemoryPersonalPage(
        entries=[_response(row, value) for row, value in seite.eintraege],
        total=seite.gesamt,
        clearable=seite.allgemein,
        limit=ai_memory_service.PERSONAL_PAGE_SIZE,
    )


@router.put("", response_model=AiMemoryResponse)
def save_memory(
    payload: AiMemoryWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
    _: None = Depends(verify_csrf),
) -> AiMemoryResponse:
    try:
        row, value = ai_memory_service.upsert_entry(
            db, user=user, scope=payload.scope, server_id=payload.server_id,
            team_id=payload.team_id, key=payload.key, value=payload.value,
        )
        return _response(row, value)
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Memory ist nicht verfuegbar") from exc


@router.delete("", response_model=AiMemoryClearResponse)
def clear_memory(
    scope: MemoryScope = Query(...),
    server_id: int | None = Query(default=None, ge=1),
    team_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
    _: None = Depends(verify_csrf),
) -> AiMemoryClearResponse:
    """Leert einen ganzen Bereich auf einmal.

    Bewusst **vor** der Route mit dem Pfadparameter definiert. FastAPI wertet in
    Definitionsreihenfolge aus, und `/{entry_id}` wuerde einen leeren Pfad zwar
    nicht fangen — aber die Reihenfolge hier ausdruecklich richtig zu halten
    kostet nichts und erspart die Suche, falls jemand den Pfad spaeter aendert.
    """
    return AiMemoryClearResponse(
        removed=ai_memory_service.delete_all_entries(db, user, scope, server_id, team_id)
    )


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    entry_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
    _: None = Depends(verify_csrf),
) -> None:
    ai_memory_service.delete_entry(db, user, entry_id)


def _preference_response(db: Session, user: User) -> AiMemoryPreferenceResponse:
    row = db.get(AiMemoryPreference, user.id)
    return AiMemoryPreferenceResponse(
        enabled=ai_memory_service.preference(db, user.id),
        notice_due=ai_memory_service.notice_due(db, user.id),
        notice_hidden=bool(row.notice_hidden) if row is not None else False,
    )


@router.get("/preference", response_model=AiMemoryPreferenceResponse)
def get_memory_preference(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
) -> AiMemoryPreferenceResponse:
    return _preference_response(db, user)


@router.put("/preference", response_model=AiMemoryPreferenceResponse)
def update_memory_preference(
    payload: AiMemoryPreferenceWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
    _: None = Depends(verify_csrf),
) -> AiMemoryPreferenceResponse:
    ai_memory_service.set_preference(db, user, payload.enabled)
    return _preference_response(db, user)


@router.post("/notice", response_model=AiMemoryPreferenceResponse)
def answer_memory_notice(
    payload: AiMemoryNoticeAnswer,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
    _: None = Depends(verify_csrf),
) -> AiMemoryPreferenceResponse:
    """Nimmt die Antwort auf den Hinweis entgegen, den der Chat vorab zeigt.

    Bewusst ein eigener Endpunkt und nicht `PUT /preference`: ein "Nein" ist
    hier keine Einstellung, sondern eine Terminverschiebung. Es aendert nichts
    am Zustand ausser dem Zeitpunkt, ab dem wieder gefragt werden darf.
    """
    ai_memory_service.record_notice_answer(
        db, user, enable=payload.enable, hide_future=payload.hide_future
    )
    return _preference_response(db, user)
