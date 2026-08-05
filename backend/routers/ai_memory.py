"""Einsehbares, editierbares und abschaltbares AI-Memory."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiMemoryEntry, User
from schemas.ai_memory import (
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
        id=row.id, scope=row.scope, server_id=row.server_id, key=row.key,
        value=value, created_at=row.created_at, updated_at=row.updated_at,
    )


@router.get("", response_model=list[AiMemoryResponse])
def list_memory(
    scope: MemoryScope = Query(...),
    server_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
) -> list[AiMemoryResponse]:
    try:
        return [_response(row, value) for row, value in ai_memory_service.list_entries(
            db, user, scope, server_id
        )]
    except DisSidecarError as exc:
        raise HTTPException(status_code=503, detail="Memory ist nicht verfuegbar") from exc


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
            key=payload.key, value=payload.value,
        )
        return _response(row, value)
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Memory ist nicht verfuegbar") from exc


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    entry_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
    _: None = Depends(verify_csrf),
) -> None:
    ai_memory_service.delete_entry(db, user, entry_id)


@router.get("/preference", response_model=AiMemoryPreferenceResponse)
def get_memory_preference(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
) -> AiMemoryPreferenceResponse:
    return AiMemoryPreferenceResponse(enabled=ai_memory_service.preference(db, user.id))


@router.put("/preference", response_model=AiMemoryPreferenceResponse)
def update_memory_preference(
    payload: AiMemoryPreferenceWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.memory.use")),
    _: None = Depends(verify_csrf),
) -> AiMemoryPreferenceResponse:
    return AiMemoryPreferenceResponse(
        enabled=ai_memory_service.set_preference(db, user, payload.enabled)
    )
