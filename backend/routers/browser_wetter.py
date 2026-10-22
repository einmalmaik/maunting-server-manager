"""Wetter für den Browser (`/api/browser/wetter`).

Das Gerät sendet nur einen Punkt an MSM; MSM liest die Vorhersage aus dem
eigenen Sidecar. Es gibt keine Anfrage an einen Drittanbieter und keine
Telemetrie. Ohne Sidecar antwortet die Route sauber mit einem Fehlercode; die
Oberfläche zeigt dann „nicht eingerichtet“ statt eines leeren Kastens.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from limits import parse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from dependencies import get_current_user, get_db, verify_csrf
from middleware.rate_limit import limiter
from models.user import User
from services import ai_wetter_service, browser_widget_token_service
from services.ai_wetter_service import WetterUnavailable

router = APIRouter(prefix="/api/browser", tags=["browser"])

_grenze = parse("30/minute")
_token_grenze = parse("10/minute")


class WetterAnfrage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]


def _mit_code(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})


def _widget_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Nur ein gültiges Wetter-Widget-Token; kein Sitzungs-Cookie, kein Konto-Token."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _mit_code(401, "WIDGET_TOKEN_FEHLT")
    klar = authorization.split(None, 1)[1].strip()
    user_id = browser_widget_token_service.pruefen(db, klar)
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        raise _mit_code(401, "WIDGET_TOKEN_UNGUELTIG")
    return user


@router.post("/wetter/widget-token")
def widget_token(
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> dict:
    """Stellt ein eng begrenztes Token für das Android-Homescreen-Widget aus."""
    if not limiter.limiter.hit(_token_grenze, f"browser-wetter-widget-token:{user.id}"):
        raise _mit_code(429, "BROWSER_WETTER_ZU_VIELE")
    klar, ablauf = browser_widget_token_service.ausstellen(db, user.id)
    return {"token": klar, "expires_at": ablauf.isoformat()}


@router.delete("/wetter/widget-token")
def widget_token_widerrufen(
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
    db: Session = Depends(get_db),
) -> dict:
    return {"widerrufen": browser_widget_token_service.widerrufen(db, user.id)}


@router.post("/wetter/widget")
def wetter_widget(
    req: WetterAnfrage,
    user: User = Depends(_widget_user),
) -> dict:
    """Wetter für das Homescreen-Widget; akzeptiert ausschließlich Widget-Tokens."""
    if not limiter.limiter.hit(_grenze, f"browser-wetter-widget:{user.id}"):
        raise _mit_code(429, "BROWSER_WETTER_ZU_VIELE")
    try:
        return ai_wetter_service.wetter_fuer_punkt(req.lat, req.lon)
    except WetterUnavailable as exc:
        if exc.code == "WETTER_SIDECAR_NICHT_ERREICHBAR":
            raise _mit_code(502, "BROWSER_WETTER_NICHT_ERREICHBAR") from exc
        raise _mit_code(503, "BROWSER_WETTER_NICHT_EINGERICHTET") from exc


@router.get("/wetter/konfig")
def wetterkonfig(user: User = Depends(get_current_user)) -> dict:
    return ai_wetter_service.wetter_konfig()


@router.get("/wetter/orte")
def wetterorte(
    q: str = Query(min_length=2, max_length=120),
    user: User = Depends(get_current_user),
) -> dict:
    if not limiter.limiter.hit(_grenze, f"browser-wetter-orte:{user.id}"):
        raise _mit_code(429, "BROWSER_WETTER_ZU_VIELE")
    try:
        return {"orte": ai_wetter_service.wetter_orte(q)}
    except WetterUnavailable as exc:
        if exc.code == "WETTER_SIDECAR_NICHT_ERREICHBAR":
            raise _mit_code(502, "BROWSER_WETTER_NICHT_ERREICHBAR") from exc
        raise _mit_code(503, "BROWSER_WETTER_NICHT_EINGERICHTET") from exc


@router.post("/wetter")
def wetter(
    req: WetterAnfrage,
    user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict:
    if not limiter.limiter.hit(_grenze, f"browser-wetter:{user.id}"):
        raise _mit_code(429, "BROWSER_WETTER_ZU_VIELE")
    try:
        return ai_wetter_service.wetter_fuer_punkt(req.lat, req.lon)
    except WetterUnavailable as exc:
        if exc.code == "WETTER_SIDECAR_NICHT_ERREICHBAR":
            raise _mit_code(502, "BROWSER_WETTER_NICHT_ERREICHBAR") from exc
        raise _mit_code(503, "BROWSER_WETTER_NICHT_EINGERICHTET") from exc
