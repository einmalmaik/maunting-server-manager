"""Panelseitige Verwaltung der Hoster-Anbindung.

Getrennt vom externen Shop-Router: hier gilt Cookie-Auth plus CSRF plus
`panel.hoster.read/write`, dort ein API-Key. Beide Wege duerfen sich nicht
vermischen.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import (
    HosterIntegration,
    HosterProduct,
    HosterService,
    HosterWebhookDelivery,
    User,
)
from schemas.hoster import (
    HosterDeliveryResponse,
    HosterIntegrationResponse,
    HosterIntegrationUpdate,
    HosterIntegrationWrite,
    HosterProductResponse,
    HosterProductWrite,
    HosterSecretResponse,
    HosterServiceResponse,
)
from services import audit_service, hoster_integration_service
from services.dis_client import DisSidecarError
from services.hoster_integration_service import HosterConfigurationError


router = APIRouter(prefix="/api/hoster", tags=["hoster-admin"])


def _integration_response(row: HosterIntegration) -> HosterIntegrationResponse:
    return HosterIntegrationResponse(
        id=row.id,
        name=row.name,
        slug=row.slug,
        enabled=row.enabled,
        service_user_id=row.service_user_id,
        webhook_url=row.webhook_url,
        terminate_grace_days=row.terminate_grace_days,
        api_key_hint=row.api_key_hint,
        webhook_secret_configured=bool(row.webhook_secret_encrypted),
        webhook_secret_hint=row.webhook_secret_hint,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _product_response(row: HosterProduct) -> HosterProductResponse:
    return HosterProductResponse(
        id=row.id,
        integration_id=row.integration_id,
        external_product_key=row.external_product_key,
        game_type=row.game_type,
        ram_limit_mb=row.ram_limit_mb,
        cpu_limit_percent=row.cpu_limit_percent,
        disk_limit_gb=row.disk_limit_gb,
        node_id=row.node_id,
        backup_interval_hours=row.backup_interval_hours,
        enabled=row.enabled,
    )


def _service_response(row: HosterService) -> HosterServiceResponse:
    return HosterServiceResponse(
        external_service_id=row.external_service_id,
        desired_state=row.desired_state,
        status=row.status,
        status_code=row.status_code,
        server_id=row.server_id,
        task_id=row.task_id,
        correlation_id=row.correlation_id,
        terminate_after=row.terminate_after,
        updated_at=row.updated_at,
    )


def _config_error(exc: HosterConfigurationError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _get_integration(db: Session, integration_id: int) -> HosterIntegration:
    row = db.query(HosterIntegration).filter(HosterIntegration.id == integration_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Integration nicht gefunden")
    return row


# ── Integrationen ──────────────────────────────────────────────────────────


@router.get("/integrations", response_model=list[HosterIntegrationResponse])
def list_integrations(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.hoster.read")),
) -> list[HosterIntegrationResponse]:
    rows = db.query(HosterIntegration).order_by(HosterIntegration.name.asc()).all()
    return [_integration_response(row) for row in rows]


@router.post(
    "/integrations",
    response_model=HosterSecretResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_integration(
    payload: HosterIntegrationWrite,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.hoster.write")),
    _: None = Depends(verify_csrf),
) -> HosterSecretResponse:
    """Legt eine Integration an und gibt den API-Key genau einmal zurueck."""
    try:
        integration, api_key = hoster_integration_service.create_integration(
            db,
            name=payload.name,
            slug=payload.slug,
            enabled=payload.enabled,
            service_user_id=payload.service_user_id,
            webhook_url=payload.webhook_url,
            terminate_grace_days=payload.terminate_grace_days,
        )
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="hoster.integration.created",
            target_type="hoster_integration",
            target_id=integration.id,
            details={"slug": integration.slug, "service_user_id": integration.service_user_id},
        )
        db.commit()
    except HosterConfigurationError as exc:
        db.rollback()
        raise _config_error(exc) from exc
    return HosterSecretResponse(value=api_key, hint=integration.api_key_hint or "****")


@router.patch("/integrations/{integration_id}", response_model=HosterIntegrationResponse)
def update_integration(
    integration_id: int,
    payload: HosterIntegrationUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.hoster.write")),
    _: None = Depends(verify_csrf),
) -> HosterIntegrationResponse:
    integration = _get_integration(db, integration_id)
    values = payload.model_dump(exclude_unset=True)
    try:
        if "service_user_id" in values and values["service_user_id"] is not None:
            hoster_integration_service.require_service_user(db, values["service_user_id"])
            integration.service_user_id = values["service_user_id"]
        if "webhook_url" in values:
            integration.webhook_url = hoster_integration_service.validate_webhook_url(
                values["webhook_url"]
            )
        for field in ("name", "enabled", "terminate_grace_days"):
            if field in values and values[field] is not None:
                setattr(integration, field, values[field])
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="hoster.integration.updated",
            target_type="hoster_integration",
            target_id=integration.id,
            details={"slug": integration.slug, "fields": sorted(values)},
        )
        db.commit()
        db.refresh(integration)
    except HosterConfigurationError as exc:
        db.rollback()
        raise _config_error(exc) from exc
    return _integration_response(integration)


@router.post("/integrations/{integration_id}/api-key", response_model=HosterSecretResponse)
def rotate_api_key(
    integration_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.hoster.write")),
    _: None = Depends(verify_csrf),
) -> HosterSecretResponse:
    """Rotiert den API-Key. Der bisherige Key ist danach sofort ungueltig."""
    integration = _get_integration(db, integration_id)
    api_key = hoster_integration_service.rotate_api_key(db, integration)
    audit_service.record_privileged_action(
        db,
        user_id=actor.id,
        action="hoster.integration.key.rotated",
        target_type="hoster_integration",
        target_id=integration.id,
        details={"slug": integration.slug},
    )
    db.commit()
    return HosterSecretResponse(value=api_key, hint=integration.api_key_hint or "****")


@router.post("/integrations/{integration_id}/webhook-secret", response_model=HosterSecretResponse)
def rotate_webhook_secret(
    integration_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.hoster.write")),
    _: None = Depends(verify_csrf),
) -> HosterSecretResponse:
    """Erzeugt ein neues Signatur-Secret fuer die Webhooks dieses Shops."""
    integration = _get_integration(db, integration_id)
    try:
        secret = hoster_integration_service.set_webhook_secret(db, integration)
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="Verschluesselungsdienst nicht erreichbar"
        ) from exc
    audit_service.record_privileged_action(
        db,
        user_id=actor.id,
        action="hoster.integration.secret.rotated",
        target_type="hoster_integration",
        target_id=integration.id,
        details={"slug": integration.slug},
    )
    db.commit()
    return HosterSecretResponse(value=secret, hint=integration.webhook_secret_hint or "****")


@router.delete("/integrations/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_integration(
    integration_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.hoster.write")),
    _: None = Depends(verify_csrf),
) -> Response:
    """Entfernt eine Integration.

    Solange noch Vertraege existieren, wird abgelehnt: ein Cascade wuerde die
    Zuordnung zwischen Kunden und ihren laufenden Servern stillschweigend
    zerstoeren, waehrend die Server selbst weiterlaufen.
    """
    integration = _get_integration(db, integration_id)
    active = (
        db.query(HosterService)
        .filter(
            HosterService.integration_id == integration.id,
            HosterService.status != "terminated",
        )
        .count()
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail="Integration hat noch aktive Services und kann nicht entfernt werden",
        )
    audit_service.record_privileged_action(
        db,
        user_id=actor.id,
        action="hoster.integration.deleted",
        target_type="hoster_integration",
        target_id=integration.id,
        details={"slug": integration.slug},
    )
    db.delete(integration)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Produkte ───────────────────────────────────────────────────────────────


@router.get("/integrations/{integration_id}/products", response_model=list[HosterProductResponse])
def list_products(
    integration_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.hoster.read")),
) -> list[HosterProductResponse]:
    integration = _get_integration(db, integration_id)
    rows = (
        db.query(HosterProduct)
        .filter(HosterProduct.integration_id == integration.id)
        .order_by(HosterProduct.external_product_key.asc())
        .all()
    )
    return [_product_response(row) for row in rows]


@router.put("/integrations/{integration_id}/products", response_model=HosterProductResponse)
def upsert_product(
    integration_id: int,
    payload: HosterProductWrite,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.hoster.write")),
    _: None = Depends(verify_csrf),
) -> HosterProductResponse:
    integration = _get_integration(db, integration_id)
    try:
        product = hoster_integration_service.upsert_product(
            db, integration=integration, **payload.model_dump()
        )
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="hoster.product.saved",
            target_type="hoster_product",
            target_id=product.id,
            details={
                "slug": integration.slug,
                "product": product.external_product_key,
                "game_type": product.game_type,
            },
        )
        db.commit()
        db.refresh(product)
    except HosterConfigurationError as exc:
        db.rollback()
        raise _config_error(exc) from exc
    return _product_response(product)


@router.delete(
    "/integrations/{integration_id}/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_product(
    integration_id: int,
    product_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.hoster.write")),
    _: None = Depends(verify_csrf),
) -> Response:
    integration = _get_integration(db, integration_id)
    product = (
        db.query(HosterProduct)
        .filter(HosterProduct.id == product_id, HosterProduct.integration_id == integration.id)
        .first()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    audit_service.record_privileged_action(
        db,
        user_id=actor.id,
        action="hoster.product.deleted",
        target_type="hoster_product",
        target_id=product.id,
        details={"slug": integration.slug, "product": product.external_product_key},
    )
    db.delete(product)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Vertraege und Zustellungen ─────────────────────────────────────────────


@router.get("/integrations/{integration_id}/services", response_model=list[HosterServiceResponse])
def list_services(
    integration_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.hoster.read")),
) -> list[HosterServiceResponse]:
    integration = _get_integration(db, integration_id)
    rows = (
        db.query(HosterService)
        .filter(HosterService.integration_id == integration.id)
        .order_by(HosterService.updated_at.desc())
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [_service_response(row) for row in rows]


@router.get(
    "/integrations/{integration_id}/deliveries", response_model=list[HosterDeliveryResponse]
)
def list_deliveries(
    integration_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("panel.hoster.read")),
) -> list[HosterDeliveryResponse]:
    """Zustellstatus der Webhooks. Der Payload selbst wird nicht ausgegeben."""
    integration = _get_integration(db, integration_id)
    rows = (
        db.query(HosterWebhookDelivery)
        .filter(HosterWebhookDelivery.integration_id == integration.id)
        .order_by(HosterWebhookDelivery.id.desc())
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [
        HosterDeliveryResponse(
            id=row.id,
            event_type=row.event_type,
            status=row.status,
            attempt=row.attempt,
            response_code=row.response_code,
            error=row.error,
            correlation_id=row.correlation_id,
            created_at=row.created_at,
            sent_at=row.sent_at,
        )
        for row in rows
    ]


@router.post(
    "/integrations/{integration_id}/deliveries/{delivery_id}/retry",
    status_code=status.HTTP_204_NO_CONTENT,
)
def retry_delivery(
    integration_id: int,
    delivery_id: int,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_global("panel.hoster.write")),
    _: None = Depends(verify_csrf),
) -> Response:
    from services.hoster_webhook_service import retry_delivery as retry

    integration = _get_integration(db, integration_id)
    delivery = (
        db.query(HosterWebhookDelivery)
        .filter(
            HosterWebhookDelivery.id == delivery_id,
            HosterWebhookDelivery.integration_id == integration.id,
        )
        .first()
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Zustellung nicht gefunden")
    retry(db, delivery)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
