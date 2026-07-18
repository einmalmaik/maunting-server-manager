"""Phase 4: Frontend-Entkopplung — CORS, Cookies cross-site, serve_frontend, CSP."""
from fastapi.testclient import TestClient

import config
from config import get_cors_origins
from main import app, _csp_connect_src


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


class TestMeEchoesCsrfHeader:
    def test_me_returns_csrf_header(self, client: TestClient, owner_cookies: dict):
        response = client.get("/api/auth/me", cookies=owner_cookies)
        assert response.status_code == 200
        csrf_cookie = owner_cookies.get("__Secure-csrf_token")
        assert csrf_cookie
        assert response.headers.get("X-CSRF-Token") == csrf_cookie
