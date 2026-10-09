"""Die MSM-Suche des Secure Browsers (`routers/browser_suche.py`).

Eigene kleine App mit dem Router: die Anmeldung kommt per Override, SearXNG
ist nachgestellt. So prüft der Test die Route selbst, ohne Datenbank.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from dependencies import get_current_user, verify_csrf
from routers import browser_suche
from services import ai_web_search_service


class _Antwort:
    def __init__(self, daten: dict, status: int = 200) -> None:
        self.status_code = status
        self._daten = daten

    def json(self) -> dict:
        return self._daten


def _app(angemeldet: bool = True, konto: int = 1) -> TestClient:
    app = FastAPI()
    app.include_router(browser_suche.router)

    def nutzer():
        if not angemeldet:
            raise HTTPException(status_code=401)
        return SimpleNamespace(id=konto)

    app.dependency_overrides[get_current_user] = nutzer
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app)


@pytest.fixture
def searxng(monkeypatch: pytest.MonkeyPatch) -> dict:
    gesehen: dict = {"anfragen": [], "antwort": _Antwort({"results": []})}

    class Client:
        def get(self, url, params=None, headers=None):
            gesehen["anfragen"].append({"url": url, "params": dict(params or {})})
            antwort = gesehen["antwort"]
            if isinstance(antwort, Exception):
                raise antwort
            return antwort

    monkeypatch.setattr(ai_web_search_service, "_http_client", lambda: Client())
    monkeypatch.setattr(ai_web_search_service, "searxng_url", lambda: "http://127.0.0.1:8888")
    return gesehen


def test_ohne_anmeldung_gibt_es_keine_suche(searxng: dict) -> None:
    antwort = _app(angemeldet=False).post("/api/browser/suche", json={"q": "wetter"})
    assert antwort.status_code == 401
    assert searxng["anfragen"] == []


def test_sichere_suche_seite_und_sprache_gehen_an_searxng(searxng: dict) -> None:
    antwort = _app(konto=11).post(
        "/api/browser/suche", json={"q": "wetter berlin", "seite": 2, "sicher": True, "sprache": "de"}
    )
    assert antwort.status_code == 200
    params = searxng["anfragen"][0]["params"]
    assert params["q"] == "wetter berlin"
    assert params["pageno"] == 2
    assert params["safesearch"] == 2
    assert params["language"] == "de"
    assert searxng["anfragen"][0]["url"] == "http://127.0.0.1:8888/search"


def test_ohne_sichere_suche_bestimmt_die_instanz(searxng: dict) -> None:
    _app(konto=12).post("/api/browser/suche", json={"q": "wetter"})
    assert "safesearch" not in searxng["anfragen"][0]["params"]


def test_nur_oeffentliche_http_adressen_und_keine_gekuerzten(searxng: dict) -> None:
    lang = "https://example.com/" + "a" * ai_web_search_service.MAX_URL_CHARS
    searxng["antwort"] = _Antwort({"results": [
        {"url": "https://example.com/a", "title": "A", "content": "Text A"},
        {"url": "javascript:alert(1)", "title": "B"},
        {"url": "file:///etc/passwd", "title": "C"},
        {"url": "http://127.0.0.1:8000/api", "title": "D"},
        {"url": "http://192.168.1.1/", "title": "E"},
        {"url": lang, "title": "F"},
        "kein Objekt",
        {"url": "http://example.org/b", "title": "G", "img_src": "https://tracker.example/bild.png"},
    ]})
    antwort = _app(konto=13).post("/api/browser/suche", json={"q": "x"})
    assert antwort.status_code == 200
    assert antwort.json() == {"treffer": [
        {"titel": "A", "url": "https://example.com/a", "inhalt": "Text A"},
        {"titel": "G", "url": "http://example.org/b", "inhalt": ""},
    ]}


def test_eine_seite_hat_hoechstens_zehn_treffer(searxng: dict) -> None:
    searxng["antwort"] = _Antwort({"results": [
        {"url": f"https://example.com/{i}", "title": str(i)} for i in range(25)
    ]})
    antwort = _app(konto=14).post("/api/browser/suche", json={"q": "x"})
    assert len(antwort.json()["treffer"]) == browser_suche.TREFFER_JE_SEITE


@pytest.mark.parametrize(
    "koerper",
    [
        {"q": ""},
        {"q": "   "},
        {"q": "x" * 501},
        {"q": "x", "seite": True},
        {"q": "x", "seite": 0},
        {"q": "x", "seite": "2"},
        {"q": "x", "sicher": "ja"},
        {"q": "x", "sprache": "de-DE"},
        {"q": "x", "ziel": "http://evil.example"},
    ],
)
def test_unpassende_anfragen_erreichen_searxng_nicht(searxng: dict, koerper: dict) -> None:
    antwort = _app(konto=15).post("/api/browser/suche", json=koerper)
    assert antwort.status_code == 422
    assert searxng["anfragen"] == []


def test_ohne_eingerichtete_instanz_503_mit_code(monkeypatch: pytest.MonkeyPatch, searxng: dict) -> None:
    monkeypatch.setattr(ai_web_search_service, "searxng_url", lambda: None)
    antwort = _app(konto=16).post("/api/browser/suche", json={"q": "x"})
    assert antwort.status_code == 503
    assert antwort.json()["detail"]["code"] == "BROWSER_SUCHE_NICHT_EINGERICHTET"


@pytest.mark.parametrize("fehler", [_Antwort({}, status=500), ConnectionError("weg")])
def test_eine_instanz_die_nicht_antwortet_ist_ein_fehler_keine_leere_liste(searxng: dict, fehler) -> None:
    searxng["antwort"] = fehler
    antwort = _app(konto=17).post("/api/browser/suche", json={"q": "x"})
    assert antwort.status_code == 502
    assert antwort.json()["detail"]["code"] == "BROWSER_SUCHE_NICHT_ERREICHBAR"


def test_die_ki_bekommt_bei_fehlern_weiter_eine_leere_liste(searxng: dict) -> None:
    searxng["antwort"] = ConnectionError("weg")
    assert ai_web_search_service._search_searxng("x") == []


def test_die_grenze_gilt_je_konto(searxng: dict) -> None:
    client = _app(konto=18)
    stati = [client.post("/api/browser/suche", json={"q": "x"}).status_code for _ in range(61)]
    assert stati[:60] == [200] * 60
    assert stati[60] == 429
    assert _app(konto=19).post("/api/browser/suche", json={"q": "x"}).status_code == 200
