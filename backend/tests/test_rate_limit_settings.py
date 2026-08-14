"""Tests für konfigurierbare Auth- und Global-Rate-Limits.

Deckt ab:
- Pure resolve/validate (Defaults, Ranges, extreme Eingaben)
- GET/POST /api/settings Persistenz und 4xx bei ungültigen Writes
- Runtime-Durchsetzung: 429 nach konfiguriertem Budget (nicht nur Hardcodes)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.panel_settings_service import PanelSettingsService
from services.rate_limit_settings import (
    DEFAULT_AUTH,
    DEFAULT_GLOBAL,
    KEY_AUTH,
    KEY_GLOBAL,
    resolve_auth_limit,
    resolve_global_limit,
    validate_auth_limit,
    validate_global_limit,
)


class TestResolveAndValidate:
    """Pure Helper — keine DB, deterministisch."""

    def test_resolve_defaults_when_empty_or_missing(self):
        assert resolve_auth_limit(None) == DEFAULT_AUTH
        assert resolve_auth_limit("") == DEFAULT_AUTH
        assert resolve_auth_limit("   ") == DEFAULT_AUTH
        assert resolve_global_limit(None) == DEFAULT_GLOBAL
        assert resolve_global_limit("") == DEFAULT_GLOBAL

    def test_resolve_accepts_in_range(self):
        assert resolve_auth_limit("3") == 3
        assert resolve_auth_limit(50) == 50
        assert resolve_auth_limit(10) == 10
        assert resolve_global_limit("50") == 50
        assert resolve_global_limit(1000) == 1000
        assert resolve_global_limit(100) == 100

    def test_resolve_falls_back_on_out_of_range_and_garbage(self):
        assert resolve_auth_limit("2") == DEFAULT_AUTH
        assert resolve_auth_limit("51") == DEFAULT_AUTH
        assert resolve_auth_limit("abc") == DEFAULT_AUTH
        assert resolve_auth_limit(True) == DEFAULT_AUTH
        assert resolve_global_limit("49") == DEFAULT_GLOBAL
        assert resolve_global_limit("1001") == DEFAULT_GLOBAL
        assert resolve_global_limit("nope") == DEFAULT_GLOBAL

    def test_validate_rejects_out_of_range(self):
        with pytest.raises(ValueError, match="rate_limit_auth"):
            validate_auth_limit(2)
        with pytest.raises(ValueError, match="rate_limit_auth"):
            validate_auth_limit(51)
        with pytest.raises(ValueError, match="rate_limit_global"):
            validate_global_limit(49)
        with pytest.raises(ValueError, match="rate_limit_global"):
            validate_global_limit(1001)

    def test_validate_rejects_non_integer(self):
        with pytest.raises(ValueError, match="ganze Zahl"):
            validate_auth_limit("10.5")
        with pytest.raises(ValueError, match="ganze Zahl"):
            validate_auth_limit("abc")
        with pytest.raises(ValueError, match="ganze Zahl"):
            validate_global_limit(True)
        with pytest.raises(ValueError, match="leer"):
            validate_auth_limit("")
        with pytest.raises(ValueError, match="leer"):
            validate_global_limit(None)

    def test_validate_accepts_boundary(self):
        assert validate_auth_limit(3) == 3
        assert validate_auth_limit(50) == 50
        assert validate_global_limit(50) == 50
        assert validate_global_limit(1000) == 1000


class TestSettingsApiRateLimits:
    """Shipper GET/POST /api/settings für die beiden Keys."""

    def test_get_defaults_when_unset(self, client: TestClient, owner_cookies: dict):
        PanelSettingsService.invalidate_cache()
        res = client.get("/api/settings", cookies=owner_cookies)
        assert res.status_code == 200
        body = res.json()
        assert body["rate_limit_auth"] == DEFAULT_AUTH
        assert body["rate_limit_global"] == DEFAULT_GLOBAL

    def test_post_persists_in_range_values(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        res = client.post(
            "/api/settings",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"rate_limit_auth": 15, "rate_limit_global": 200},
        )
        assert res.status_code == 200

        get_res = client.get("/api/settings", cookies=owner_cookies)
        assert get_res.status_code == 200
        body = get_res.json()
        assert body["rate_limit_auth"] == 15
        assert body["rate_limit_global"] == 200
        # Persistenz über Service (shipped store)
        assert PanelSettingsService.get(KEY_AUTH) == "15"
        assert PanelSettingsService.get(KEY_GLOBAL) == "200"

    def test_post_rejects_auth_out_of_range_without_changing_store(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        client.post(
            "/api/settings",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"rate_limit_auth": 12, "rate_limit_global": 150},
        )
        before_auth = PanelSettingsService.get(KEY_AUTH)
        before_global = PanelSettingsService.get(KEY_GLOBAL)

        for bad in (2, 51, 0, -1, 999):
            res = client.post(
                "/api/settings",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
                json={"rate_limit_auth": bad},
            )
            assert res.status_code == 400, f"expected 400 for auth={bad}, got {res.status_code}"
            assert PanelSettingsService.get(KEY_AUTH) == before_auth
            assert PanelSettingsService.get(KEY_GLOBAL) == before_global

    def test_post_rejects_global_out_of_range_without_changing_store(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        client.post(
            "/api/settings",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"rate_limit_auth": 12, "rate_limit_global": 150},
        )
        before_auth = PanelSettingsService.get(KEY_AUTH)
        before_global = PanelSettingsService.get(KEY_GLOBAL)

        for bad in (49, 1001, 0, -5):
            res = client.post(
                "/api/settings",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
                json={"rate_limit_global": bad},
            )
            assert res.status_code == 400, f"expected 400 for global={bad}, got {res.status_code}"
            assert PanelSettingsService.get(KEY_AUTH) == before_auth
            assert PanelSettingsService.get(KEY_GLOBAL) == before_global

    def test_post_rejects_non_integer(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        # Pydantic 422 oder unsere 400 — beides 4xx, kein silent accept
        res = client.post(
            "/api/settings",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"rate_limit_auth": "abc"},
        )
        assert res.status_code in (400, 422)

        res2 = client.post(
            "/api/settings",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"rate_limit_global": "nope"},
        )
        assert res2.status_code in (400, 422)

    def test_post_requires_write_permission(
        self, client: TestClient, user_cookies: dict, user_csrf_token: str
    ):
        res = client.post(
            "/api/settings",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
            json={"rate_limit_auth": 10},
        )
        assert res.status_code == 403

    def test_get_requires_read_permission(self, client: TestClient, user_cookies: dict):
        # regular_user ohne panel.settings.read
        res = client.get("/api/settings", cookies=user_cookies)
        assert res.status_code == 403


class TestDynamicRateLimitEnforcement:
    """429 nach konfiguriertem Budget über shipped Limiters + Settings-Store."""

    def test_auth_limit_respects_lower_configured_value(self, client: TestClient):
        """Auth-Limit 3 → spätestens der 4. Login-Versuch pro IP liefert 429."""
        PanelSettingsService.set(KEY_AUTH, "3")
        PanelSettingsService.invalidate_cache()
        # Cache neu laden mit dem gesetzten Wert
        assert PanelSettingsService.get(KEY_AUTH) == "3"

        from middleware.rate_limit import limiter

        limiter.reset()

        saw_429 = False
        for _ in range(6):
            response = client.post(
                "/api/auth/login",
                json={
                    "username": "nonexistent",
                    "password": "wrong",
                    "otp_code": None,
                },
            )
            if response.status_code == 429:
                saw_429 = True
                break
        assert saw_429, "Auth rate limit mit rate_limit_auth=3 hat nicht gegriffen"

    def test_auth_default_still_triggers_around_10(self, client: TestClient):
        """Ohne Setting: Default 10 — nach >10 Requests 429 (Regression)."""
        PanelSettingsService.invalidate_cache()
        from middleware.rate_limit import limiter

        limiter.reset()

        saw_429 = False
        for _ in range(12):
            response = client.post(
                "/api/auth/login",
                json={
                    "username": "nonexistent",
                    "password": "wrong",
                    "otp_code": None,
                },
            )
            if response.status_code == 429:
                saw_429 = True
                break
        assert saw_429, "Default auth rate limit (10) hat nicht gegriffen"

    def test_global_limit_respects_lower_configured_value(self, client: TestClient):
        """Global-Limit 50 → nach >50 Health-Requests 429."""
        PanelSettingsService.set(KEY_GLOBAL, "50")
        PanelSettingsService.invalidate_cache()
        assert PanelSettingsService.get(KEY_GLOBAL) == "50"

        from middleware.rate_limit import limiter

        limiter.reset()

        saw_429 = False
        for _ in range(55):
            response = client.get("/api/health")
            if response.status_code == 429:
                saw_429 = True
                break
        assert saw_429, "Global rate limit mit rate_limit_global=50 hat nicht gegriffen"

    def test_global_limit_counts_per_endpoint_not_per_url(self, client: TestClient):
        """Wechselnde Pfad-IDs umgehen das Limit nicht.

        Ohne ``key_style="endpoint"`` bekommt jede Server-ID einen eigenen
        Zähler, und ein einzelner Client darf denselben teuren Endpunkt
        beliebig oft aufrufen, solange er nur die ID variiert.
        """
        PanelSettingsService.set(KEY_GLOBAL, "50")
        PanelSettingsService.invalidate_cache()
        assert PanelSettingsService.get(KEY_GLOBAL) == "50"

        from middleware.rate_limit import limiter

        limiter.reset()

        saw_429 = False
        for server_id in range(1, 56):
            response = client.get(f"/api/servers/{server_id}/status")
            if response.status_code == 429:
                saw_429 = True
                break
        assert saw_429, "Jede Server-ID bekam einen eigenen Zähler — das Limit greift nicht"

    def test_global_default_still_triggers_around_100(self, client: TestClient):
        """Ohne Setting: Default 100 — nach >100 Requests 429 (Regression)."""
        PanelSettingsService.invalidate_cache()
        from middleware.rate_limit import limiter

        limiter.reset()

        saw_429 = False
        for _ in range(105):
            response = client.get("/api/health")
            if response.status_code == 429:
                saw_429 = True
                break
        assert saw_429, "Default global rate limit (100) hat nicht gegriffen"
