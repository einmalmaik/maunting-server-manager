"""Wer eine Antwort zwischenspeichern darf.

Bis 09/2026 bekam jede Antwort ohne eigenen `Cache-Control`-Kopf — außer HTML
und `/assets/` — `public, max-age=86400`: auch `/api/auth/me`, auch jede 401.
`public` erlaubt ausgerechnet geteilten Caches (CDN, Firmenproxy), die Antwort
auf eine angemeldete Anfrage zu speichern und anderen auszuliefern (RFC 9111
§3.5).

Die Zusagen jetzt: Was unter `/api/` keinen eigenen Kopf setzt, landet in
keinem Cache, ein Fehler ebenso. Setzt eine Route ihren Kopf selbst (Bild der
Regionsanalyse, Profilbild), gilt ihrer. Die gehashten Assets bleiben ein Jahr,
HTML fragt immer nach, Icons und Schriften dürfen einen Tag bleiben.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main import _frontend_einhaengen, security_headers_middleware

HTML_NIE_AUS_DEM_CACHE = "no-cache, no-store, must-revalidate"


# ── Die API ────────────────────────────────────────────────────────────────


def test_eine_angemeldete_antwort_landet_in_keinem_cache(client: TestClient, owner_cookies: dict) -> None:
    antwort = client.get("/api/auth/me", cookies=owner_cookies)

    assert antwort.status_code == 200
    assert antwort.headers["cache-control"] == "no-store"


def test_eine_abweisung_ebenso_wenig(client: TestClient) -> None:
    antwort = client.get("/api/auth/me")

    assert antwort.status_code == 401
    assert antwort.headers["cache-control"] == "no-store"


def test_ein_unbekannter_api_pfad_ebenso_wenig(client: TestClient) -> None:
    antwort = client.get("/api/gibt-es-nicht")

    assert antwort.status_code == 404
    assert antwort.headers["cache-control"] == "no-store"


# ── Das Frontend, wenn das Panel es selbst ausliefert ──────────────────────
# (MSM_SERVE_FRONTEND ohne Caddy davor, etwa in Kubernetes. Hinter Caddy
# kommt hier nur `/api/*` an.)


@pytest.fixture
def panel(tmp_path: Path):
    """Dieselbe Einhängung und dieselbe Middleware wie `main.app`, aber mit
    eigenem `dist`."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "hilfe").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>MSM</title>", encoding="utf-8")
    (tmp_path / "hilfe" / "index.html").write_text("<!doctype html><title>Hilfe</title>", encoding="utf-8")
    (tmp_path / "assets" / "index-3f2a1b.js").write_text("export {}", encoding="utf-8")
    (tmp_path / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    ausgeliefert = FastAPI()
    ausgeliefert.middleware("http")(security_headers_middleware)
    _frontend_einhaengen(ausgeliefert, str(tmp_path))
    with TestClient(ausgeliefert) as c:
        yield c


def test_ein_gehashtes_asset_bleibt_ein_jahr(panel: TestClient) -> None:
    antwort = panel.get("/assets/index-3f2a1b.js")

    assert antwort.status_code == 200
    assert antwort.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_ein_fehlender_chunk_ist_nicht_unveraenderlich(panel: TestClient) -> None:
    # Ein alter Client fragt nach einem Update nach einem Chunk, den es nicht
    # mehr gibt. Hieße dieses 404 `immutable`, dürfte sein Browser es ein Jahr
    # lang wiederverwenden — auch nach einem Zurückrollen, wenn es den Chunk
    # längst wieder gibt.
    antwort = panel.get("/assets/index-alt.js")

    assert antwort.status_code == 404
    assert antwort.headers["cache-control"] == "no-store"


def test_die_startseite_fragt_immer_nach(panel: TestClient) -> None:
    antwort = panel.get("/")

    assert antwort.status_code == 200
    assert antwort.headers["cache-control"] == HTML_NIE_AUS_DEM_CACHE


def test_html_fragt_auch_unter_einem_anderen_pfad_nach(panel: TestClient) -> None:
    # Erkannt am Inhaltstyp, nicht am Pfad: ein Verzeichnis-Index hier — und
    # genauso ein SPA-Fallback, der die index.html unter `/ai` ausliefert.
    antwort = panel.get("/hilfe/")

    assert antwort.status_code == 200
    assert antwort.headers["content-type"].startswith("text/html")
    assert antwort.headers["cache-control"] == HTML_NIE_AUS_DEM_CACHE


def test_ein_icon_darf_einen_tag_bleiben(panel: TestClient) -> None:
    antwort = panel.get("/favicon.ico")

    assert antwort.status_code == 200
    assert antwort.headers["cache-control"] == "public, max-age=86400"


def test_eine_unterseite_fragt_immer_nach(panel: TestClient) -> None:
    # Wer `/ai` neu lädt, bekommt die index.html — und die darf so wenig im
    # Cache liegen bleiben wie unter `/`, sonst verweist sie nach einem Update
    # auf Chunks, die es nicht mehr gibt.
    antwort = panel.get("/ai", headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    assert antwort.status_code == 200
    assert antwort.headers["content-type"].startswith("text/html")
    assert antwort.headers["cache-control"] == HTML_NIE_AUS_DEM_CACHE


def test_eine_fehlende_datei_wird_nicht_gespeichert(panel: TestClient) -> None:
    # Hieße dieses 404 einen Tag lang `public`, bliebe das Bild auch dann
    # verschwunden, wenn es längst wieder da ist.
    antwort = panel.get("/icons/fehlt.png", headers={"Accept": "image/avif,image/webp,*/*"})

    assert antwort.status_code == 404
    assert antwort.headers["cache-control"] == "no-store"
