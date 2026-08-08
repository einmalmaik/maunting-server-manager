"""Freigaben fuer den autonomen KI-Modus (Zielpunkt 3.7).

Die Berechtigung `ai.autonomous.use` sagt, dass ein Benutzer den Modus
verwenden *darf*. Diese Endpunkte legen fest, *wo* und *wieviel* — und zwar
ausschliesslich fuer den aufrufenden Benutzer selbst. Eine Freigabe fuer einen
fremden Benutzer gibt es hier bewusst nicht: sie waere eine Rechteerweiterung
durch die Hintertuer.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiAutonomyGrant, User
from schemas.ai_autonomy import AiAutonomyGrantResponse, AiAutonomyGrantWrite
from services import ai_autonomy_service, permission_service


router = APIRouter(prefix="/api/ai/autonomy", tags=["ai-autonomy"])


def _response(db: Session, row: AiAutonomyGrant) -> AiAutonomyGrantResponse:
    return AiAutonomyGrantResponse(
        id=row.id,
        server_id=row.server_id,
        enabled=row.enabled,
        max_actions_per_hour=row.max_actions_per_hour,
        used_last_hour=ai_autonomy_service.hourly_usage(db, user_id=row.user_id),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _require_server_access(db: Session, user: User, server_id: int | None) -> None:
    """Eine Freigabe fuer einen Server, den man nicht sehen darf, waere sinnlos.

    Sie wuerde ohnehin nichts bewirken — jede Aktion prueft ihr eigenes Recht —
    aber sie wuerde dem Benutzer die Existenz eines fremden Servers verraten.
    """
    if server_id is None:
        return
    if not permission_service.has_server_permission(db, user, server_id, "server.view"):
        raise HTTPException(status_code=404, detail="Server nicht gefunden")


@router.get("", response_model=list[AiAutonomyGrantResponse])
def list_grants(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.autonomous.use")),
) -> list[AiAutonomyGrantResponse]:
    rows = (
        db.query(AiAutonomyGrant)
        .filter(AiAutonomyGrant.user_id == user.id)
        .order_by(AiAutonomyGrant.server_id.is_(None).desc(), AiAutonomyGrant.server_id)
        .all()
    )
    return [_response(db, row) for row in rows]


@router.put("", response_model=AiAutonomyGrantResponse)
def upsert_grant(
    payload: AiAutonomyGrantWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.autonomous.use")),
    _: None = Depends(verify_csrf),
) -> AiAutonomyGrantResponse:
    _require_server_access(db, user, payload.server_id)
    try:
        row = ai_autonomy_service.set_grant(
            db,
            user=user,
            server_id=payload.server_id,
            enabled=payload.enabled,
            max_actions_per_hour=payload.max_actions_per_hour,
            granted_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(row)
    return _response(db, row)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_grant(
    server_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.autonomous.use")),
    _: None = Depends(verify_csrf),
) -> None:
    _require_server_access(db, user, server_id)
    if ai_autonomy_service.clear_grant(db, user_id=user.id, server_id=server_id):
        db.commit()
