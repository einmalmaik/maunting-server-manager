"""Wetter-Routen des Browsers (`routers/browser_wetter.py`).

Eigene kleine App: Anmeldung und CSRF kommen per Override, der Sidecar ist
nachgestellt. So wird die Route selbst geprüft, ohne Datenbank und ohne Netz.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from dependencies import get_current_user, get_db, verify_csrf
from routers import browser_wetter
from services import ai_wetter_service
from services.ai_wetter_service import WetterUnavailable

STAND = {
    "jetzt": {"temp": 18.0, "lage": "klar", "wind": 3.2, "feuchte": 55, "regen": 0.0, "wolken": 10, "zeit": "2026-10-22T12:00:00+00:00"},
    "stunden": [],
    "tage": [],
    "quelle": "NOAA GFS 0.50°",
    "stand": "2026-10-22T06:00:00+00:00",
}


def _app(angemeldet: bool = True, konto: int = 1, widget: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(browser_wetter.router)

    def nutzer():
        if not angemeldet:
            raise HTTPException(status_code=401)
        return SimpleNamespace(id=konto)

    app.dependency_overrides[get_current_user] = nutzer
    app.dependency_overrides[verify_csrf] = lambda: None
    app.dependency_overrides[get_db] = lambda: None
    if widget:
        app.dependency_overrides[browser_wetter._widget_user] = lambda: SimpleNamespace(id=7)
    return TestClient(app)


@pytest.fixture
def dienst(monkeypatch: pytest.MonkeyPatch) -> dict:
    zustand: dict = {"fehler": None}
    monkeypatch.setattr(ai_wetter_service, "is_sidecar_running", lambda: True)

    def wetter(lat: float, lon: float) -> dict:
        if zustand["fehler"]:
            raise WetterUnavailable(zustand["fehler"])
        assert -90 <= lat <= 90 and -180 <= lon <= 180
        return STAND

    monkeypatch.setattr(ai_wetter_service, "wetter_fuer_punkt", wetter)
    monkeypatch.setattr(ai_wetter_service, "wetter_orte", lambda q: [{"name": q.title(), "land": "DE", "lat": 52.5, "lon": 13.4}])
    return zustand


def test_ohne_anmeldung_kein_wetter(dienst: dict) -> None:
    assert _app(angemeldet=False).post("/api/browser/wetter", json={"lat": 52.5, "lon": 13.4}).status_code == 401


def test_wetter_liefert_die_sidecar_antwort(dienst: dict) -> None:
    antwort = _app().post("/api/browser/wetter", json={"lat": 52.5, "lon": 13.4})
    assert antwort.status_code == 200
    assert antwort.json()["jetzt"]["temp"] == 18.0
    assert antwort.json()["quelle"].startswith("NOAA")


def test_wetter_prueft_koordinaten(dienst: dict) -> None:
    assert _app().post("/api/browser/wetter", json={"lat": 91, "lon": 0}).status_code == 422
    assert _app().post("/api/browser/wetter", json={"lat": 0, "lon": 181}).status_code == 422


def test_sidecar_aus_liefert_502(dienst: dict) -> None:
    dienst["fehler"] = "WETTER_SIDECAR_NICHT_ERREICHBAR"
    antwort = _app().post("/api/browser/wetter", json={"lat": 52.5, "lon": 13.4})
    assert antwort.status_code == 502
    assert antwort.json()["detail"]["code"] == "BROWSER_WETTER_NICHT_ERREICHBAR"


def test_ortssuche_liefert_treffer(dienst: dict) -> None:
    antwort = _app().get("/api/browser/wetter/orte", params={"q": "berlin"})
    assert antwort.status_code == 200
    assert antwort.json()["orte"][0]["name"] == "Berlin"


def test_widget_route_braucht_ein_widget_token(dienst: dict) -> None:
    antwort = _app().post("/api/browser/wetter/widget", json={"lat": 52.5, "lon": 13.4})
    assert antwort.status_code == 401


def test_widget_route_mit_gueltigem_token(dienst: dict) -> None:
    antwort = _app(widget=True).post("/api/browser/wetter/widget", json={"lat": 52.5, "lon": 13.4})
    assert antwort.status_code == 200
    assert antwort.json()["jetzt"]["temp"] == 18.0


def test_wetter_verbietet_unbekannte_felder(dienst: dict) -> None:
    antwort = _app().post("/api/browser/wetter", json={"lat": 52.5, "lon": 13.4, "adresse": "Angreifer"})

    assert antwort.status_code == 422


def test_wetter_braucht_beide_koordinaten(dienst: dict) -> None:
    assert _app().post("/api/browser/wetter", json={"lat": 52.5}).status_code == 422
    assert _app().post("/api/browser/wetter", json={}).status_code == 422
    assert _app().post("/api/browser/wetter", json={"lat": None, "lon": 13.4}).status_code == 422


def test_wetter_lehnt_nan_und_unendlich_ab(dienst: dict) -> None:
    for roh in ('{"lat": NaN, "lon": 13.4}', '{"lat": 52.5, "lon": Infinity}', '{"lat": -Infinity, "lon": 13.4}'):
        antwort = _app().post(
            "/api/browser/wetter",
            content=roh,
            headers={"content-type": "application/json"},
        )
        assert antwort.status_code == 422, roh


def test_ortssuche_braucht_einen_brauchbaren_begriff(dienst: dict) -> None:
    assert _app().get("/api/browser/wetter/orte").status_code == 422
    assert _app().get("/api/browser/wetter/orte", params={"q": "a"}).status_code == 422
    assert _app().get("/api/browser/wetter/orte", params={"q": "x" * 121}).status_code == 422


def test_ortssuche_ohne_sidecar_liefert_502(dienst: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def fehlt(_: str) -> list:
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR")

    monkeypatch.setattr(ai_wetter_service, "wetter_orte", fehlt)

    antwort = _app().get("/api/browser/wetter/orte", params={"q": "berlin"})

    assert antwort.status_code == 502
    assert antwort.json()["detail"]["code"] == "BROWSER_WETTER_NICHT_ERREICHBAR"


def test_nicht_eingerichteter_sidecar_liefert_503(dienst: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    dienst["fehler"] = "WETTER_NICHT_EINGERICHTET"
    antwort = _app().post("/api/browser/wetter", json={"lat": 52.5, "lon": 13.4})
    assert antwort.status_code == 503
    assert antwort.json()["detail"]["code"] == "BROWSER_WETTER_NICHT_EINGERICHTET"

    def fehlt(_: str) -> list:
        raise WetterUnavailable("WETTER_NICHT_EINGERICHTET")

    monkeypatch.setattr(ai_wetter_service, "wetter_orte", fehlt)
    assert _app().get("/api/browser/wetter/orte", params={"q": "berlin"}).status_code == 503


def test_widget_route_lehnt_fehlendes_und_fremdes_bearer_ab(dienst: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from services import browser_widget_token_service

    assert _app().post("/api/browser/wetter/widget", json={"lat": 52.5, "lon": 13.4}).status_code == 401
    assert _app().post(
        "/api/browser/wetter/widget",
        json={"lat": 52.5, "lon": 13.4},
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    ).status_code == 401
    assert _app().post(
        "/api/browser/wetter/widget",
        json={"lat": 52.5, "lon": 13.4},
        headers={"Authorization": "Bearer kein-widget-token"},
    ).status_code == 401

    monkeypatch.setattr(browser_widget_token_service, "pruefen", lambda _db, _token: None)
    antwort = _app().post(
        "/api/browser/wetter/widget",
        json={"lat": 52.5, "lon": 13.4},
        headers={"Authorization": "Bearer pw_unbekannt"},
    )
    assert antwort.status_code == 401
    assert antwort.json()["detail"]["code"] == "WIDGET_TOKEN_UNGUELTIG"


def test_widget_route_akzeptiert_kein_sitzungscookie(dienst: dict) -> None:
    client = _app()
    client.cookies.set("__Secure-access_token", "sitzung")

    antwort = client.post("/api/browser/wetter/widget", json={"lat": 52.5, "lon": 13.4})

    assert antwort.status_code == 401


def test_wetter_routen_halten_das_limit_ein(dienst: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_wetter.limiter.limiter, "hit", lambda *_, **__: False)

    assert _app().post("/api/browser/wetter", json={"lat": 52.5, "lon": 13.4}).status_code == 429
    assert _app().get("/api/browser/wetter/orte", params={"q": "berlin"}).status_code == 429
    assert _app(widget=True).post("/api/browser/wetter/widget", json={"lat": 52.5, "lon": 13.4}).status_code == 429
    assert _app().post("/api/browser/wetter/widget-token").status_code == 429
