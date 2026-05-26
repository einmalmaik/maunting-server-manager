"""Tests fuer den Panel-Settings Router (Steam-Account)."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User
from services.steam_account_service import SteamAccountService


class TestSteamAccountEndpoints:
    def test_post_steam_account_requires_permission(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str
    ):
        res = client.post(
            "/api/settings/steam-account",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
            json={"username": "u", "password": "p"},
        )
        assert res.status_code == 403

    def test_post_steam_account_requires_csrf(
        self, client: TestClient, owner_cookies: dict
    ):
        res = client.post(
            "/api/settings/steam-account",
            cookies=owner_cookies,
            json={"username": "u", "password": "p"},
        )
        assert res.status_code == 403

    def test_post_steam_account_ok(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        res = client.post(
            "/api/settings/steam-account",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"username": "steam_user", "password": "steam_pass"},
        )
        assert res.status_code == 200
        assert SteamAccountService.get_username() == "steam_user"
        assert SteamAccountService.get_decrypted_password() == "steam_pass"
        SteamAccountService.clear()

    def test_delete_steam_account_requires_permission(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str
    ):
        res = client.delete(
            "/api/settings/steam-account",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert res.status_code == 403

    def test_delete_steam_account_requires_csrf(
        self, client: TestClient, owner_cookies: dict
    ):
        res = client.delete(
            "/api/settings/steam-account",
            cookies=owner_cookies,
        )
        assert res.status_code == 403

    def test_delete_steam_account_ok(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        SteamAccountService.set("u", "p")
        res = client.delete(
            "/api/settings/steam-account",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        assert SteamAccountService.is_configured() is False

    def test_get_settings_never_returns_password(
        self, client: TestClient, owner_cookies: dict
    ):
        SteamAccountService.set("u", "secret123")
        res = client.get("/api/settings", cookies=owner_cookies)
        assert res.status_code == 200
        body = res.json()
        assert body.get("steam_account_username") == "u"
        assert body.get("steam_account_configured") is True
        assert "secret123" not in str(body)
        assert "password" not in str(body).lower() or "steam_account_password" not in body
        SteamAccountService.clear()

    def test_post_steam_account_rejects_empty(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        res = client.post(
            "/api/settings/steam-account",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"username": "", "password": "p"},
        )
        assert res.status_code == 400
