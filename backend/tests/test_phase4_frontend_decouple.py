"""Phase 4: Frontend-Entkopplung — CORS, Cookies cross-site, serve_frontend, CSP."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from config import get_cors_origins
from main import _csp_connect_src, _frontend_einhaengen, app


class TestCorsOrigins:
    def test_panel_url_always_included(self, monkeypatch):
        monkeypatch.setattr(config.settings, "panel_url", "https://panel.example.com", raising=False)
        monkeypatch.setattr(config.settings, "cors_allowed_origins", "", raising=False)
        monkeypatch.setattr(config.settings, "debug", False, raising=False)
        origins = get_cors_origins()
        assert "https://panel.example.com" in origins

    def test_extra_cors_origins_parsed(self, monkeypatch):
        monkeypatch.setattr(config.settings, "panel_url", "https://panel.example.com", raising=False)
        monkeypatch.setattr(
            config.settings,
            "cors_allowed_origins",
            "https://maunting-panel.vercel.app, https://preview.example.com/",
            raising=False,
        )
        monkeypatch.setattr(config.settings, "debug", False, raising=False)
        origins = get_cors_origins()
        assert "https://maunting-panel.vercel.app" in origins
        assert "https://preview.example.com" in origins

    def test_debug_includes_local_dev_ports(self, monkeypatch):
        monkeypatch.setattr(config.settings, "panel_url", "http://localhost:3000", raising=False)
        monkeypatch.setattr(config.settings, "cors_allowed_origins", "", raising=False)
        monkeypatch.setattr(config.settings, "debug", True, raising=False)
        origins = get_cors_origins()
        assert "http://localhost:3000" in origins
        assert "http://localhost:5173" in origins


class TestCspConnectSrc:
    def test_connect_src_includes_self_and_cors(self, monkeypatch):
        monkeypatch.setattr(config.settings, "panel_url", "https://panel.example.com", raising=False)
        monkeypatch.setattr(
            config.settings,
            "cors_allowed_origins",
            "https://maunting-panel.vercel.app",
            raising=False,
        )
        monkeypatch.setattr(config.settings, "debug", False, raising=False)
        # Re-bind main._cors_origins used by CSP builder
        import main as main_mod

        main_mod._cors_origins = get_cors_origins()
        src = _csp_connect_src()
        assert "'self'" in src
        assert "https://panel.example.com" in src
        assert "https://maunting-panel.vercel.app" in src
        assert "wss://panel.example.com" in src


class TestServeFrontendFlag:
    def test_serve_frontend_setting_defaults_true(self):
        # Abwaertskompatibel: Single-Host liefert SPA weiterhin.
        assert hasattr(config.settings, "serve_frontend")

    def test_assets_mount_targets_vite_asset_directory(self, tmp_path):
        # Der /assets-Mount entfernt seinen Prefix vor der Dateisuche. Ohne
        # diesen Ordner würden Vite-Chunks als fehlende Dateien enden und ein
        # vorgeschalteter SPA-Fallback könnte HTML statt JavaScript liefern.
        (tmp_path / "assets").mkdir()
        (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
        (tmp_path / "assets" / "index-3f2a1b.js").write_text("export {}", encoding="utf-8")
        panel = FastAPI()
        _frontend_einhaengen(panel, str(tmp_path))

        with TestClient(panel) as c:
            chunk = c.get("/assets/index-3f2a1b.js")
            fehlt = c.get("/assets/index-alt.js")

        assert chunk.status_code == 200
        assert "javascript" in chunk.headers["content-type"]
        assert fehlt.status_code == 404
        assert not fehlt.headers.get("content-type", "").startswith("text/html")


#: So fragt ein Browser nach einer Seite — Adresszeile, Link aus einer Mail,
#: Neuladen (Chrome).
SEITE = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"}
STARTSEITE = "<!doctype html><title>MSM</title>"


@pytest.fixture
def ausgeliefert(tmp_path):
    """Das Panel, wie es die Oberfläche ohne Caddy davor ausliefert
    (Kubernetes): eine API-Route, dahinter das gebaute Frontend."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(STARTSEITE, encoding="utf-8")
    (tmp_path / "assets" / "index-3f2a1b.js").write_text("export {}", encoding="utf-8")
    panel = FastAPI()

    @panel.get("/api/health")
    def health():
        return {"status": "ok"}

    _frontend_einhaengen(panel, str(tmp_path))
    with TestClient(panel) as c:
        yield c


class TestSpaRueckfall:
    # Die Oberfläche routet im Browser (`BrowserRouter`). Hinter Caddy fängt
    # `try_files {path} /index.html` jede Unterseite ab. Liefert das Panel die
    # Oberfläche selbst aus, endete bis 09/2026 jedes Neuladen einer
    # Unterseite in `{"detail":"Not Found"}` — und jeder Freigabelink aus
    # einer Mail gleich beim ersten Klick.

    @pytest.mark.parametrize("pfad", ["/ai", "/servers/12", "/ai/freigabe/q4Jx-8_Zt3vKd0bW"])
    def test_eine_unterseite_bekommt_die_startseite(self, ausgeliefert, pfad):
        antwort = ausgeliefert.get(pfad, headers=SEITE)

        assert antwort.status_code == 200
        assert antwort.headers["content-type"].startswith("text/html")
        assert antwort.text == STARTSEITE

    def test_auch_ohne_koerper(self, ausgeliefert):
        assert ausgeliefert.head("/ai", headers=SEITE).status_code == 200

    @pytest.mark.parametrize("pfad", ["/assets/index-alt.js", "/assets", "/api/gibt-es-nicht", "/api", "/ws/konsole"])
    def test_nie_unter_assets_api_und_ws(self, ausgeliefert, pfad):
        # Ein fehlender Chunk als HTML stoppte den laufenden Client (siehe
        # `_frontend_einhaengen`), eine unbekannte API-Route als Startseite
        # hielte ein Skript für eine Antwort.
        antwort = ausgeliefert.get(pfad, headers=SEITE)

        assert antwort.status_code == 404
        assert not antwort.headers.get("content-type", "").startswith("text/html")

    @pytest.mark.parametrize("accept", ["image/avif,image/webp,*/*", "*/*", "application/json"])
    def test_nur_fuer_eine_seite(self, ausgeliefert, accept):
        # Ein fehlendes Bild, Skript oder Manifest bleibt 404: HTML an seiner
        # Stelle hielte der Browser für den Inhalt.
        antwort = ausgeliefert.get("/icons/fehlt.png", headers={"Accept": accept})

        assert antwort.status_code == 404

    def test_nur_lesend(self, ausgeliefert):
        assert ausgeliefert.post("/ai", headers=SEITE).status_code == 405

    def test_eine_vorhandene_datei_bleibt_sie_selbst(self, ausgeliefert):
        chunk = ausgeliefert.get("/assets/index-3f2a1b.js", headers=SEITE)
        api = ausgeliefert.get("/api/health", headers=SEITE)

        assert "javascript" in chunk.headers["content-type"]
        assert api.json() == {"status": "ok"}


class TestMeEchoesCsrfHeader:
    def test_me_returns_csrf_header(self, client: TestClient, owner_cookies: dict):
        response = client.get("/api/auth/me", cookies=owner_cookies)
        assert response.status_code == 200
        csrf_cookie = owner_cookies.get("__Secure-csrf_token")
        assert csrf_cookie
        assert response.headers.get("X-CSRF-Token") == csrf_cookie
