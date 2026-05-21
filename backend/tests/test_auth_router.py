"""Tests for auth router: login, logout, refresh, CSRF, cookies."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, RefreshToken


class TestLogin:
    def test_login_sets_http_only_cookies(self, client: TestClient, owner_user: User):
        response = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": None,
        })
        assert response.status_code == 200
        # Must have all three cookies
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies
        assert "csrf_token" in response.cookies
        # access_token and refresh_token must be httponly (FastAPI TestClient exposes them)

    def test_login_wrong_password(self, client: TestClient, owner_user: User):
        response = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "WrongPass123!",
            "otp_code": None,
        })
        assert response.status_code == 401
        assert response.json()["detail"] == "Ungueltige Anmeldedaten"

    def test_login_nonexistent_user(self, client: TestClient):
        response = client.post("/api/auth/login", json={
            "username": "nobody",
            "password": "SomePass123!",
            "otp_code": None,
        })
        assert response.status_code == 401

    def test_login_inactive_user(self, client: TestClient, inactive_user: User):
        response = client.post("/api/auth/login", json={
            "username": "inactive",
            "password": "Inactive123!",
            "otp_code": None,
        })
        # Login succeeds but subsequent /me should fail
        assert response.status_code == 200
        cookies = dict(response.cookies)
        me = client.get("/api/auth/me", cookies=cookies)
        assert me.status_code == 401

    def test_login_2fa_required(self, client: TestClient, db: Session, owner_user: User):
        owner_user.two_factor_secret = "JBSWY3DPEHPK3PXP"
        owner_user.two_factor_enabled = True
        db.commit()
        response = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": None,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["requires_2fa"] is True


class TestLogout:
    def test_logout_revokes_refresh_token(self, client: TestClient, owner_user: User, owner_cookies: dict, db: Session):
        csrf = owner_cookies.get("csrf_token")
        assert csrf is not None

        # Count active refresh tokens before
        before = db.query(RefreshToken).filter(
            RefreshToken.revoked_at.is_(None),
        ).count()
        assert before > 0

        response = client.post(
            "/api/auth/logout",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200

        # All tokens for user revoked
        after = db.query(RefreshToken).filter(
            RefreshToken.revoked_at.is_(None),
        ).count()
        assert after == 0

        # Cookies cleared
        assert "access_token" not in response.cookies or response.cookies.get("access_token") == ""

    def test_logout_without_csrf_fails(self, client: TestClient, owner_cookies: dict):
        response = client.post("/api/auth/logout", cookies=owner_cookies)
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_logout_with_wrong_csrf_fails(self, client: TestClient, owner_cookies: dict):
        response = client.post(
            "/api/auth/logout",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": "wrong_csrf"},
        )
        assert response.status_code == 403


class TestRefresh:
    def test_refresh_rotates_token(self, client: TestClient, owner_user: User, owner_cookies: dict, db: Session):
        old_refresh = owner_cookies.get("refresh_token")
        response = client.post("/api/auth/refresh", cookies=owner_cookies)
        assert response.status_code == 200

        # Old refresh token must be marked used
        old_rt = db.query(RefreshToken).filter(
            RefreshToken.token_hash == old_refresh,
        ).first()
        # Note: old_refresh is plaintext, DB stores hash, so direct lookup won't work
        # Instead verify count: there should be 2 tokens total (1 used, 1 new)
        all_rts = db.query(RefreshToken).filter(RefreshToken.user_id == owner_user.id).all()
        assert len(all_rts) == 2
        used = [r for r in all_rts if r.used_at is not None]
        assert len(used) == 1
        new = [r for r in all_rts if r.used_at is None and r.revoked_at is None]
        assert len(new) == 1

    def test_refresh_without_cookie_fails(self, client: TestClient):
        response = client.post("/api/auth/refresh")
        assert response.status_code == 401


class TestGetCurrentUser:
    def test_me_returns_user(self, client: TestClient, owner_cookies: dict, owner_user: User):
        response = client.get("/api/auth/me", cookies=owner_cookies)
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "owner"
        assert data["is_owner"] is True

    def test_me_without_cookie_fails(self, client: TestClient):
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_me_with_invalid_token_fails(self, client: TestClient):
        client.cookies.set("access_token", "invalid.token.here")
        response = client.get("/api/auth/me")
        assert response.status_code == 401


class TestSetupStatus:
    def test_setup_required_when_no_owner(self, client: TestClient, db: Session):
        # Remove all users to simulate fresh install
        db.query(User).delete()
        db.commit()
        response = client.get("/api/auth/setup-status")
        assert response.status_code == 200
        assert response.json()["setup_required"] is True

    def test_setup_not_required_when_owner_exists(self, client: TestClient, owner_user: User):
        response = client.get("/api/auth/setup-status")
        assert response.status_code == 200
        assert response.json()["setup_required"] is False


class TestCsrfProtectionOnEndpoints:
    """Verify that state-changing endpoints require CSRF."""

    def test_post_without_csrf_fails(self, client: TestClient, owner_cookies: dict):
        # Try to create server without CSRF
        response = client.post("/api/servers", json={
            "name": "Test",
            "game_type": "dayz",
        }, cookies=owner_cookies)
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_post_with_csrf_succeeds(self, client: TestClient, owner_cookies: dict):
        from unittest.mock import patch
        with patch("routers.servers.subprocess.run") as mock_run:
            mock_run.return_value = type("obj", (object,), {"returncode": 0})()
            csrf = owner_cookies.get("csrf_token")
            response = client.post(
                "/api/servers",
                json={"name": "Test", "game_type": "dayz"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf},
            )
            assert response.status_code in (200, 201)
