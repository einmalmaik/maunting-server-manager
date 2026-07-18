import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient

from models import User
from services.captcha_service import CaptchaService
from services.panel_settings_service import PanelSettingsService


class TestCaptchaSystem:
    def test_get_captcha_config_defaults(self, client: TestClient):
        # Clear settings
        PanelSettingsService.set("captcha_enabled", "false")
        PanelSettingsService.set("captcha_provider", "none")
        PanelSettingsService.set("captcha_site_key", "")
        PanelSettingsService.set("captcha_secret_key", "")
        PanelSettingsService.set("captcha_secret_key_encrypted", "")

        res = client.get("/api/auth/captcha-config")
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is False
        assert body["provider"] == "none"
        assert body["site_key"] == ""

    def test_update_captcha_settings_validation(self, client: TestClient, owner_cookies: dict, csrf_token: str):
        # 1. Invalid provider must fail
        res = client.post(
            "/api/settings",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={
                "captcha_enabled": True,
                "captcha_provider": "invalid-provider",
            }
        )
        assert res.status_code == 400

        # 2. Valid turnstile must succeed
        res = client.post(
            "/api/settings",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={
                "captcha_enabled": True,
                "captcha_provider": "turnstile",
                "captcha_site_key": "my-site-key",
                "captcha_secret_key": "my-super-secret-key",
            }
        )
        assert res.status_code == 200

        # 3. GET settings should return masked secret key
        res = client.get("/api/settings", cookies=owner_cookies)
        assert res.status_code == 200
        body = res.json()
        assert body["captcha_enabled"] is True
        assert body["captcha_provider"] == "turnstile"
        assert body["captcha_site_key"] == "my-site-key"
        assert body["captcha_secret_key"].startswith("*****")
        assert "my-super-secret-key" not in body["captcha_secret_key"]

    def test_captcha_service_verify_token_success(self):
        PanelSettingsService.set("captcha_enabled", "true")
        PanelSettingsService.set("captcha_provider", "turnstile")
        PanelSettingsService.set("captcha_secret_key", "synthetic-configured-key-12345")
        PanelSettingsService.set("captcha_secret_key_encrypted", "")

        mock_resp = AsyncMock()
        mock_resp.json = MagicMock(return_value={"success": True})

        with patch("httpx.AsyncClient.post", return_value=mock_resp) as mock_post:
            asyncio.run(CaptchaService.verify_token("some-valid-token"))
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert args[0] == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
            assert kwargs["data"]["secret"] == "synthetic-configured-key-12345"
            assert kwargs["data"]["response"] == "some-valid-token"

    def test_captcha_service_verify_token_failure(self):
        PanelSettingsService.set("captcha_enabled", "true")
        PanelSettingsService.set("captcha_provider", "hcaptcha")
        PanelSettingsService.set("captcha_secret_key", "synthetic-hcaptcha-key-12345")
        PanelSettingsService.set("captcha_secret_key_encrypted", "")

        mock_resp = AsyncMock()
        mock_resp.json = MagicMock(return_value={"success": False, "error-codes": ["invalid-input-response"]})

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(CaptchaService.verify_token("invalid-token"))
            assert exc.value.status_code == 400
            assert "CAPTCHA-Verifizierung fehlgeschlagen" in exc.value.detail

    @pytest.mark.parametrize(
        ("provider", "secret"),
        [
            ("none", "configured-secret"),
            ("unknown", "configured-secret"),
            ("turnstile", ""),
            ("turnstile", "mock-placeholder"),
            ("turnstile", "test-secret"),
        ],
    )
    def test_captcha_enabled_with_invalid_configuration_fails_closed(
        self, provider: str, secret: str
    ):
        PanelSettingsService.set("captcha_enabled", "true")
        PanelSettingsService.set("captcha_provider", provider)
        PanelSettingsService.set("captcha_secret_key", secret)
        PanelSettingsService.set("captcha_secret_key_encrypted", "")

        with patch("httpx.AsyncClient.post") as post:
            with pytest.raises(HTTPException) as exc:
                asyncio.run(CaptchaService.verify_token("synthetic-captcha-token"))

        assert exc.value.status_code == 503
        post.assert_not_called()

    def test_auth_routes_blocked_when_captcha_enabled(self, client: TestClient):
        PanelSettingsService.set("captcha_enabled", "true")
        PanelSettingsService.set("captcha_provider", "recaptcha")
        PanelSettingsService.set("captcha_secret_key", "synthetic-recaptcha-key-12345")
        PanelSettingsService.set("captcha_secret_key_encrypted", "")

        # 1. Login without token must be blocked
        res = client.post("/api/auth/login", json={"username": "admin", "password": "password123"})
        assert res.status_code == 400
        assert "CAPTCHA-Verifizierung erforderlich" in res.json()["detail"]

        # 2. Register without token must be blocked
        res = client.post("/api/auth/register", json={"username": "newuser", "email": "newuser@example.com", "password": "password123"})
        assert res.status_code == 400
        assert "CAPTCHA-Verifizierung erforderlich" in res.json()["detail"]

        # 3. Forgot password without token must be blocked
        res = client.post("/api/auth/forgot-password", json={"email": "admin@example.com"})
        assert res.status_code == 400
        assert "CAPTCHA-Verifizierung erforderlich" in res.json()["detail"]

        # 4. Reset password without token must be blocked
        res = client.post("/api/auth/reset-password", json={"token": "some-token", "new_password": "newpassword123"})
        assert res.status_code == 400
        assert "CAPTCHA-Verifizierung erforderlich" in res.json()["detail"]

        # Clean up settings
        PanelSettingsService.set("captcha_enabled", "false")
        PanelSettingsService.set("captcha_provider", "none")
