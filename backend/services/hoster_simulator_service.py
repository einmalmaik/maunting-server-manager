"""Simulator-Dienst fuer Hoster- und Shop-Anbindungen (Weg B).

Erlaubt das Ausloesen und Testen von Shop-Ereignissen (Kauf, Sperre, Reaktivierung,
Kuendigung, Webhooks) direkt aus dem Panel, ohne externe Website und ohne Stripe.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import (
    HosterHandoff,
    HosterIdentity,
    HosterIntegration,
    HosterProduct,
    HosterService,
    HosterWebhookDelivery,
    Server,
    User,
)
from schemas.hoster import (
    HosterServiceResponse,
    HosterSimulationRequest,
    HosterSimulationResponse,
)
from services import (
    hoster_handoff_service,
    hoster_integration_service,
    hoster_service_lifecycle,
    hoster_webhook_service,
)


logger = logging.getLogger(__name__)


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


def simulate_event(
    db: Session, *, integration: HosterIntegration, req: HosterSimulationRequest
) -> HosterSimulationResponse:
    """Fuehrt eine simulierte Shop-Aktion fuer eine Sandbox-Integration aus."""
    if not integration.is_sandbox:
        raise HTTPException(
            status_code=400,
            detail="Der Simulator kann nur fuer Sandbox-Integrationen verwendet werden.",
        )

    action = req.action

    if action == "test_webhook":
        if not integration.webhook_url:
            raise HTTPException(
                status_code=400,
                detail="Keine Webhook-URL in dieser Integration hinterlegt.",
            )
        # Erzeuge ein Test-Event
        mock_payload = {
            "event": "service.ready",
            "external_service_id": req.external_service_id or "SIM-TEST-001",
            "desired_state": "active",
            "status": "ready",
            "status_code": None,
            "server_id": 999,
            "correlation_id": str(uuid4()),
            "terminate_after": None,
            "updated_at": hoster_service_lifecycle._now().isoformat(),
        }
        hoster_webhook_service.enqueue_custom_event(
            db,
            integration=integration,
            event_type="service.ready",
            payload=mock_payload,
            correlation_id=mock_payload["correlation_id"],
        )
        return HosterSimulationResponse(
            ok=True,
            action="test_webhook",
            message="Test-Webhook wurde in die Zustellungswarteschlange eingereiht.",
            webhook_status="queued",
        )

    if action == "order":
        product = None
        if req.product_key:
            product = (
                db.query(HosterProduct)
                .filter(
                    HosterProduct.integration_id == integration.id,
                    HosterProduct.external_product_key == req.product_key,
                )
                .first()
            )
        if product is None:
            # Nimm das erste aktivierte Produkt
            product = (
                db.query(HosterProduct)
                .filter(
                    HosterProduct.integration_id == integration.id,
                    HosterProduct.enabled.is_(True),
                )
                .first()
            )
        if product is None:
            raise HTTPException(
                status_code=400,
                detail="Kein Produkt fuer diese Integration gefunden. Bitte lege zuerst ein Produkt an.",
            )

        suffix = uuid4().hex[:6].upper()
        service_id = req.external_service_id or f"SIM-SVC-{suffix}"
        cust_subject = f"SIM-CUST-{suffix}"
        cust_email = req.email or f"sim-{suffix.lower()}@example.com"

        service = hoster_service_lifecycle.apply_desired_state(
            db,
            integration=integration,
            external_service_id=service_id,
            desired_state="active",
            external_subject=cust_subject,
            product_key=product.external_product_key,
            email=cust_email,
        )

        handoff_url = None
        try:
            from config import settings

            _, token = hoster_handoff_service.create_handoff(
                db,
                integration=integration,
                service=service,
                target_path=None,
            )
            base = (settings.panel_url or "").rstrip("/")
            handoff_url = f"{base}/api/hoster/handoff/{token}"
        except Exception as exc:
            logger.warning("Handoff fuer Simulation konnte nicht erstellt werden: %s", exc)

        return HosterSimulationResponse(
            ok=True,
            action="order",
            message=f"Test-Kauf erfolgreich simuliert ({product.external_product_key}).",
            service=_service_response(service),
            handoff_url=handoff_url,
        )

    # Fuer suspend, reactivate, terminate benoetigen wir einen existierenden Service
    service = None
    if req.external_service_id:
        service = hoster_service_lifecycle.get_service(
            db, integration, req.external_service_id
        )
    if service is None:
        # Nimm den zuletzt aktualisierten Service dieser Integration
        service = (
            db.query(HosterService)
            .filter(HosterService.integration_id == integration.id)
            .order_by(HosterService.updated_at.desc())
            .first()
        )
    if service is None:
        raise HTTPException(
            status_code=404,
            detail="Kein aktiver Testvertrag gefunden. Bitte simuliere zuerst einen Kauf.",
        )

    identity = service.identity
    product_key = service.product.external_product_key if service.product else None
    external_subject = (
        identity.external_subject_hint
        if identity and identity.external_subject_hint
        else "SIM-CUST"
    )

    if action == "suspend":
        updated_service = hoster_service_lifecycle.apply_desired_state(
            db,
            integration=integration,
            external_service_id=service.external_service_id,
            desired_state="suspended",
            external_subject=external_subject,
            product_key=product_key,
        )
        return HosterSimulationResponse(
            ok=True,
            action="suspend",
            message="Zahlungssperre erfolgreich simuliert. Server pausiert.",
            service=_service_response(updated_service),
        )

    if action == "reactivate":
        updated_service = hoster_service_lifecycle.apply_desired_state(
            db,
            integration=integration,
            external_service_id=service.external_service_id,
            desired_state="active",
            external_subject=external_subject,
            product_key=product_key,
        )
        return HosterSimulationResponse(
            ok=True,
            action="reactivate",
            message="Reaktivierung erfolgreich simuliert. Server wieder aktiv.",
            service=_service_response(updated_service),
        )

    if action == "terminate":
        updated_service = hoster_service_lifecycle.apply_desired_state(
            db,
            integration=integration,
            external_service_id=service.external_service_id,
            desired_state="terminated",
            external_subject=external_subject,
            product_key=product_key,
        )
        return HosterSimulationResponse(
            ok=True,
            action="terminate",
            message="Kündigung erfolgreich simuliert. Kündigungsfrist läuft.",
            service=_service_response(updated_service),
        )

    raise HTTPException(status_code=400, detail=f"Unbekannte Simulationsaktion: {action}")


def clean_sandbox_data(db: Session, *, integration: HosterIntegration) -> int:
    """Loescht alle Testvertraege, Webhook-Zustellungen und Test-Identitaeten einer Sandbox-Integration."""
    if not integration.is_sandbox:
        raise HTTPException(
            status_code=400,
            detail="Testdaten koennen nur fuer Sandbox-Integrationen zurueckgesetzt werden.",
        )

    services = (
        db.query(HosterService)
        .filter(HosterService.integration_id == integration.id)
        .all()
    )
    count = len(services)

    # Server loeschen
    from services import server_deletion_service

    for srv in services:
        if srv.server_id is not None:
            server = db.query(Server).filter(Server.id == srv.server_id).first()
            if server is not None:
                try:
                    server_deletion_service.delete_server(
                        db,
                        server_id=server.id,
                        principal=None,
                        force=True,
                    )
                except Exception as exc:
                    logger.warning("Sandbox-Server %s konnte nicht geloescht werden: %s", srv.server_id, exc)

    # Handoffs loeschen
    db.query(HosterHandoff).filter(HosterHandoff.integration_id == integration.id).delete(
        synchronize_session=False
    )
    # Deliveries loeschen
    db.query(HosterWebhookDelivery).filter(
        HosterWebhookDelivery.integration_id == integration.id
    ).delete(synchronize_session=False)
    # Services loeschen
    db.query(HosterService).filter(HosterService.integration_id == integration.id).delete(
        synchronize_session=False
    )
    # Identitaeten loeschen (und zugehoerige Sandbox-User)
    identities = (
        db.query(HosterIdentity)
        .filter(HosterIdentity.integration_id == integration.id)
        .all()
    )
    for ident in identities:
        user = db.query(User).filter(User.id == ident.user_id).first()
        db.delete(ident)
        if user is not None and not user.is_owner and (user.email or "").startswith("sim-"):
            try:
                db.delete(user)
            except Exception as exc:
                logger.warning("Sandbox-User %s konnte nicht geloescht werden: %s", user.id, exc)
                user.is_active = False

    db.commit()
    return count
