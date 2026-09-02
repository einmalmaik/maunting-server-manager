"""Autorisierte Statussicht auf persistente Backend-Aufgaben."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models import OperationTask, User
from schemas.operation_task import OperationTaskResponse
from services.operation_task_service import get_visible_task, list_visible_tasks


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=list[OperationTaskResponse])
def list_tasks(
    limit: int = Query(50, ge=1, le=100),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[OperationTask]:
    """Listet eigene Tasks; Audit-Leser dürfen panelweit einsehen."""
    return list_visible_tasks(db, user, limit=limit, status=status)


@router.get("/{task_id}", response_model=OperationTaskResponse)
def get_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OperationTask:
    """Liefert genau einen sichtbaren Task ohne Request-Payload oder Secrets."""
    return get_visible_task(db, user, task_id)
