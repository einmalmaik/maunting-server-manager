"""Rollenbasierte KI-Limits und effektive Benutzergrenzen.

Diese Routen konfigurieren nur Kontingente. Provider-Schlüssel und Chats
folgen in separaten Schnitten, damit Rollenverwaltung und Secret-Flows nicht
zu einem schwer prüfbaren Monolithen werden.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import Role, User
from schemas.ai_settings import (
    AiRoleLimitsResponse,
    AiRoleLimitsUpdate,
    AiWebSearchKeyUpdate,
    AiWebSearchStatus,
    EffectiveAiLimitsResponse,
)
from services import ai_limit_service, audit_service
from services.dis_client import DisSidecarError
from services.role_service import effective_user_role_ids


router = APIRouter(prefix="/api/ai", tags=["ai-settings"])


def _role_response(db: Session, role: Role) -> AiRoleLimitsResponse:
    """Zeigt eine unkonfigurierte Rolle als „unbegrenzt“ statt als Null-Limit.

    Frueher stand hier 0. Das war doppelt irrefuehrend: es beschrieb weder den
    gespeicherten Zustand (es ist gar nichts gespeichert) noch das tatsaechliche
    Verhalten (ohne jede Rollenkonfiguration gilt unbegrenzt, siehe
    ``ai_limit_service``) — und ein unbeabsichtigtes Speichern haette die Rolle
    hart gesperrt. ``configured`` bleibt der ehrliche Unterschied zwischen
    „nichts hinterlegt“ und „ausdruecklich unbegrenzt gesetzt“.
    """
    row = ai_limit_service.get_role_limit(db, role.id)
    values = {
        field: getattr(row, field) if row is not None else None
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


@router.get("/settings/web-search", response_model=AiWebSearchStatus)
def get_web_search_status(
    _: User = Depends(require_global("panel.settings.read")),
) -> AiWebSearchStatus:
    """Nur ob ein Schluessel hinterlegt ist — nie der Schluessel selbst."""
    from services import ai_web_search_service

    return AiWebSearchStatus(configured=ai_web_search_service.is_configured())


@router.put("/settings/web-search", response_model=AiWebSearchStatus)
def set_web_search_key(
    payload: AiWebSearchKeyUpdate,
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiWebSearchStatus:
    """Hinterlegt oder entfernt den Suchschluessel.

    Ein leerer Wert entfernt ihn — dann verschwindet auch das Werkzeug aus dem
    Katalog, statt bei jedem Versuch zu scheitern.
    """
    from services import ai_web_search_service

    secret = payload.api_key.get_secret_value() if payload.api_key else ""
    try:
        ai_web_search_service.store_api_key(secret)
    except DisSidecarError as exc:
        raise HTTPException(
            status_code=503, detail="Suchschluessel konnte nicht sicher gespeichert werden"
        ) from exc

    configured = ai_web_search_service.is_configured()
    with SessionLocal() as audit_db:
        audit_service.record_privileged_action(
            audit_db,
            user_id=actor.id,
            action="ai.web_search.key.updated",
            target_type="panel_setting",
            target_id=None,
            # Bewusst nur der Zustand, nie ein Teil des Schluessels.
            details={"configured": configured},
        )
        audit_db.commit()
    return AiWebSearchStatus(configured=configured)


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
