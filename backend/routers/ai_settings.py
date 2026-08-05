"""Rollenbasierte KI-Limits und effektive Benutzergrenzen.

Diese Routen konfigurieren nur Kontingente. Provider-Schlüssel und Chats
folgen in separaten Schnitten, damit Rollenverwaltung und Secret-Flows nicht
zu einem schwer prüfbaren Monolithen werden.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import Role, User
from schemas.ai_settings import (
    AiRoleLimitsResponse,
    AiRoleLimitsUpdate,
    EffectiveAiLimitsResponse,
)
from services import ai_limit_service, audit_service
from services.role_service import effective_user_role_ids


router = APIRouter(prefix="/api/ai", tags=["ai-settings"])


def _role_response(db: Session, role: Role) -> AiRoleLimitsResponse:
    """Mappt fehlende Konfiguration sicher auf gesperrte Null-Limits."""
    row = ai_limit_service.get_role_limit(db, role.id)
    values = {
        field: getattr(row, field) if row is not None else 0
        for field in ai_limit_service.LIMIT_FIELDS
    }
    return AiRoleLimitsResponse(
        role_id=role.id,
        role_name=role.name,
        configured=row is not None,
        updated_at=row.updated_at if row is not None else None,
        **values,
    )


@router.get("/settings/role-limits", response_model=list[AiRoleLimitsResponse])
def list_role_limits(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.settings.read")),
) -> list[AiRoleLimitsResponse]:
    """Listet jede Rolle; unkonfigurierte Rollen bleiben explizit auf 0."""
    roles = db.query(Role).order_by(Role.is_system.desc(), Role.name.asc()).all()
    return [_role_response(db, role) for role in roles]


@router.put(
    "/settings/role-limits/{role_id}",
    response_model=AiRoleLimitsResponse,
)
def update_role_limits(
    role_id: int,
    req: AiRoleLimitsUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiRoleLimitsResponse:
    """Speichert alle Felder atomar und protokolliert nur nicht-sensible Metadaten."""
    role = db.query(Role).filter(Role.id == role_id).first()
    if role is None:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    values = req.model_dump()
    try:
        ai_limit_service.set_role_limit(db, role.id, values)
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="ai.role_limits.updated",
            target_type="role",
            target_id=role.id,
            details={
                "configured_fields": list(ai_limit_service.LIMIT_FIELDS),
                "unlimited_fields": sorted(
                    field for field, value in values.items() if value is None
                ),
            },
        )
        db.commit()
        db.refresh(role)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="KI-Limits konnten wegen einer gleichzeitigen Änderung nicht gespeichert werden",
        ) from exc
    return _role_response(db, role)


@router.get("/limits/me", response_model=EffectiveAiLimitsResponse)
def get_my_effective_limits(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EffectiveAiLimitsResponse:
    """Zeigt eigene effektive Grenzen; Nutzung bleibt separat permission-gated."""
    limits = ai_limit_service.resolve_effective_limits(db, user)
    return EffectiveAiLimitsResponse(
        role_ids=effective_user_role_ids(db, user),
        **limits.__dict__,
    )
