"""Provider- und BYOK-Routen mit strikt getrennten Berechtigungen."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiProvider, AiUserCredential, User
from schemas.ai_provider import (
    AiProviderAvailableResponse,
    AiProviderCreate,
    AiProviderResponse,
    AiProviderUpdate,
    AiUserCredentialResponse,
    AiUserCredentialUpdate,
)
from services import ai_provider_service, audit_service
from services.dis_client import DisSidecarError


router = APIRouter(prefix="/api/ai", tags=["ai-providers"])


def _admin_response(provider: AiProvider) -> AiProviderResponse:
    return AiProviderResponse(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        default_model=provider.default_model,
        enabled=provider.enabled,
        requires_api_key=provider.requires_api_key,
        allow_private_network=provider.allow_private_network,
        operator_key_configured=bool(provider.operator_api_key_encrypted),
        operator_key_hint=provider.operator_api_key_hint,
        token_price_cents_per_million=provider.token_price_cents_per_million,
        updated_at=provider.updated_at,
    )


@router.get("/settings/providers", response_model=list[AiProviderResponse])
def list_provider_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.settings.read")),
) -> list[AiProviderResponse]:
    providers = db.query(AiProvider).order_by(AiProvider.name.asc()).all()
    return [_admin_response(provider) for provider in providers]


@router.post("/settings/providers", response_model=AiProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: AiProviderCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiProviderResponse:
    try:
        provider = ai_provider_service.create_provider(
            db,
            **payload.model_dump(exclude={"operator_api_key"}),
            operator_api_key=(
                payload.operator_api_key.get_secret_value() if payload.operator_api_key else None
            ),
        )
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="ai.provider.created",
            target_type="ai_provider",
            target_id=provider.id,
            details={
                "name": provider.name,
                "private_network": provider.allow_private_network,
                "operator_key_configured": bool(provider.operator_api_key_encrypted),
            },
        )
        db.commit()
        db.refresh(provider)
        return _admin_response(provider)
    except ai_provider_service.AiProviderConfigurationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider-Name ist bereits vergeben") from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Provider-Key konnte nicht sicher gespeichert werden") from exc


@router.patch("/settings/providers/{provider_id}", response_model=AiProviderResponse)
def update_provider(
    provider_id: int,
    payload: AiProviderUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> AiProviderResponse:
    provider = db.get(AiProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    values = payload.model_dump(
        exclude_unset=True,
        exclude={"operator_api_key", "clear_operator_api_key"},
    )
    try:
        ai_provider_service.update_provider(
            db,
            provider,
            values=values,
            operator_api_key=(
                payload.operator_api_key.get_secret_value() if payload.operator_api_key else None
            ),
            clear_operator_api_key=payload.clear_operator_api_key,
        )
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="ai.provider.updated",
            target_type="ai_provider",
            target_id=provider.id,
            details={
                "changed_fields": sorted(values),
                "key_changed": bool(payload.operator_api_key or payload.clear_operator_api_key),
            },
        )
        db.commit()
        db.refresh(provider)
        return _admin_response(provider)
    except ai_provider_service.AiProviderConfigurationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider-Name ist bereits vergeben") from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Provider-Key konnte nicht sicher gespeichert werden") from exc


@router.delete("/settings/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> Response:
    provider = db.get(AiProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    try:
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="ai.provider.deleted",
            target_type="ai_provider",
            target_id=provider.id,
            details={"name": provider.name},
        )
        db.delete(provider)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider konnte nicht geloescht werden") from exc


@router.get("/providers", response_model=list[AiProviderAvailableResponse])
def list_available_providers(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> list[AiProviderAvailableResponse]:
    providers = db.query(AiProvider).filter(AiProvider.enabled.is_(True)).order_by(AiProvider.name).all()
    credential_provider_ids = {
        row[0]
        for row in db.query(AiUserCredential.provider_id)
        .filter(AiUserCredential.user_id == user.id)
        .all()
    }
    return [
        AiProviderAvailableResponse(
            id=provider.id,
            name=provider.name,
            default_model=provider.default_model,
            requires_api_key=provider.requires_api_key,
            user_key_configured=provider.id in credential_provider_ids,
            operator_key_available=bool(provider.operator_api_key_encrypted),
            available=(
                not provider.requires_api_key
                or provider.id in credential_provider_ids
                or bool(provider.operator_api_key_encrypted)
            ),
        )
        for provider in providers
    ]


@router.get("/providers/{provider_id}/credential", response_model=AiUserCredentialResponse)
def get_credential_status(
    provider_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiUserCredentialResponse:
    if db.get(AiProvider, provider_id) is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    credential = (
        db.query(AiUserCredential)
        .filter(
            AiUserCredential.user_id == user.id,
            AiUserCredential.provider_id == provider_id,
        )
        .first()
    )
    return AiUserCredentialResponse(
        provider_id=provider_id,
        configured=credential is not None,
        key_hint=credential.api_key_hint if credential else None,
    )


@router.put("/providers/{provider_id}/credential", response_model=AiUserCredentialResponse)
def put_credential(
    provider_id: int,
    payload: AiUserCredentialUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> AiUserCredentialResponse:
    provider = db.get(AiProvider, provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    try:
        credential = ai_provider_service.set_user_credential(
            db,
            user_id=user.id,
            provider_id=provider.id,
            api_key=payload.api_key.get_secret_value(),
        )
        db.commit()
        db.refresh(credential)
        return AiUserCredentialResponse(
            provider_id=provider.id,
            configured=True,
            key_hint=credential.api_key_hint,
        )
    except ai_provider_service.AiProviderConfigurationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="API-Key konnte nicht sicher gespeichert werden") from exc


@router.delete("/providers/{provider_id}/credential", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    provider_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> Response:
    credential = (
        db.query(AiUserCredential)
        .filter(
            AiUserCredential.user_id == user.id,
            AiUserCredential.provider_id == provider_id,
        )
        .first()
    )
    if credential is not None:
        db.delete(credential)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
