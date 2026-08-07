"""Externe Hoster-API: ein Vertrag, ein gewuenschter Zustand.

Der Shop soll nicht mehrere voneinander abhaengige Aufrufe programmieren
muessen, nur um einen Server bereitzustellen. Er meldet den gewuenschten
Zustand; MSM erledigt Validierung, Hostwahl, Portvergabe, Installation,
Rechtevergabe und Statusmeldung.

Authentifizierung erfolgt ausschliesslich ueber den Integrations-API-Key im
Header. Es gibt hier bewusst keinen Cookie-Pfad: eine Browser-Session darf
diese Endpunkte nicht ansprechen koennen.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import HosterIntegration
from schemas.hoster import (
    HosterDesiredStateRequest,
    HosterHandoffRequest,
    HosterHandoffResponse,
    HosterServiceResponse,
)
from services import hoster_handoff_service, hoster_integration_service
from services.hoster_integration_service import (
    API_KEY_HEADER,
    HosterConfigurationError,
)
from services.hoster_service_lifecycle import apply_desired_state, get_service
from services.session_service import issue_session


router = APIRouter(prefix="/api/hoster/v1", tags=["hoster-api"])


def current_integration(
    db: Session = Depends(get_db),
    api_key: str | None = Header(None, alias=API_KEY_HEADER),
) -> HosterIntegration:
    """Loest den API-Key in genau eine aktive Integration auf."""
    return hoster_integration_service.authenticate(db, api_key)


def _service_response(row) -> HosterServiceResponse:
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


@router.put("/services/{external_service_id}", response_model=HosterServiceResponse)
def put_desired_state(
    external_service_id: str,
    payload: HosterDesiredStateRequest,
    db: Session = Depends(get_db),
    integration: HosterIntegration = Depends(current_integration),
) -> HosterServiceResponse:
    """Setzt den gewuenschten Zustand eines Vertrags.

    Der Aufruf ist wiederholbar: derselbe `external_service_id` fuehrt immer
    denselben Vertrag weiter. Ein wegen Netzwerkfehler doppelt gesendeter
    Auftrag erzeugt deshalb keinen zweiten Server.
    """
    try:
        service = apply_desired_state(
            db,
            integration=integration,
            external_service_id=external_service_id,
            desired_state=payload.desired_state,
            external_subject=payload.external_subject,
            product_key=payload.product_key,
            email=payload.email,
        )
    except HosterConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _service_response(service)


@router.get("/services/{external_service_id}", response_model=HosterServiceResponse)
def read_service(
    external_service_id: str,
    db: Session = Depends(get_db),
    integration: HosterIntegration = Depends(current_integration),
) -> HosterServiceResponse:
    """Fragt den tatsaechlichen Zustand eines Vertrags ab."""
    try:
        service = get_service(db, integration, external_service_id)
    except HosterConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if service is None:
        raise HTTPException(status_code=404, detail="Service nicht gefunden")
    return _service_response(service)


@router.post("/handoffs", response_model=HosterHandoffResponse)
def create_handoff(
    payload: HosterHandoffRequest,
    db: Session = Depends(get_db),
    integration: HosterIntegration = Depends(current_integration),
) -> HosterHandoffResponse:
    """Erzeugt einen kurzlebigen Einmal-Link in das Panel des Kunden."""
    try:
        service = get_service(db, integration, payload.external_service_id)
    except HosterConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if service is None:
        raise HTTPException(status_code=404, detail="Service nicht gefunden")
    handoff, token = hoster_handoff_service.create_handoff(
        db, integration=integration, service=service, target_path=payload.target_path
    )
    base = (settings.panel_url or "").rstrip("/")
    return HosterHandoffResponse(
        url=f"{base}/api/hoster/handoff/{token}",
        expires_at=handoff.expires_at,
    )


# ── Einloesung durch den Browser des Kunden ────────────────────────────────
# Bewusst ein eigener Router ohne das /v1-Praefix und ohne API-Key: hier klickt
# ein Mensch. Der Einmal-Token ist gleichzeitig das Authentifizierungsmerkmal
# und der CSRF-Schutz — ein Angreifer kann ihn nicht erraten und er gilt genau
# einmal.

redeem_router = APIRouter(prefix="/api/hoster", tags=["hoster-api"])


@router.get("/health")
def health(integration: HosterIntegration = Depends(current_integration)) -> dict:
    """Erlaubt dem Shop, seinen API-Key zu pruefen, ohne etwas zu veraendern."""
    return {"ok": True, "integration": integration.slug}


@redeem_router.get("/handoff/{token}")
def redeem_handoff(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Meldet den Kunden an und leitet ihn auf sein Panel weiter.

    Bei jedem Fehlerfall (unbekannt, abgelaufen, bereits verwendet) fuehrt der
    Weg einheitlich auf die Loginseite. Ein Angreifer soll aus der Antwort nicht
    ableiten koennen, welcher Fall vorliegt.
    """
    del request
    base = (settings.panel_url or "").rstrip("/")
    try:
        user, target_path = hoster_handoff_service.redeem(db, token)
    except HTTPException:
        response = RedirectResponse(url=f"{base}/login?handoff=invalid", status_code=302)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        return response

    response = RedirectResponse(url=f"{base}{target_path}", status_code=302)
    # Der Link darf niemals in einem Cache oder Verlaufseintrag wiederverwendbar
    # sein. Der Token ist zwar bereits verbraucht, aber ein gecachter Redirect
    # wuerde den Kunden verwirren.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    issue_session(response, db, user)
    return response
