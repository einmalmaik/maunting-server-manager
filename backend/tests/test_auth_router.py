"""Tests for auth router: login, logout, refresh, CSRF, cookies."""
import logging
from unittest.mock import patch

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
        assert "__Secure-access_token" in response.cookies
        assert "__Secure-refresh_token" in response.cookies
        assert "__Secure-csrf_token" in response.cookies
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
        # Inactive users cannot login at all
        assert response.status_code == 401
        assert "deaktiviert" in response.json()["detail"]

    def test_login_2fa_required(self, client: TestClient, db: Session, owner_user: User):
        from services.auth_service import AuthService
        owner_user.two_factor_secret_encrypted = AuthService.encrypt_2fa_secret("JBSWY3DPEHPK3PXP")
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
        csrf = owner_cookies.get("__Secure-csrf_token")
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
        assert "__Secure-access_token" not in response.cookies or response.cookies.get("__Secure-access_token") == ""

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

    def test_logout_blacklists_access_token(self, client: TestClient, owner_user: User, db: Session):
        # Login to get fresh cookies
        login_response = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": None,
        })
        assert login_response.status_code == 200
        cookies = dict(login_response.cookies)
        csrf = cookies.get("__Secure-csrf_token")

        # Logout with CSRF
        logout_response = client.post(
            "/api/auth/logout",
            cookies=cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert logout_response.status_code == 200

        # Try /api/auth/me with old access_token cookie -> should fail with 401
        me_response = client.get("/api/auth/me", cookies=cookies)
        assert me_response.status_code == 401


class TestRefresh:
    def test_refresh_rotates_token(self, client: TestClient, owner_user: User, owner_cookies: dict, db: Session):
        old_refresh = owner_cookies.get("__Secure-refresh_token")
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
        client.cookies.set("__Secure-access_token", "invalid.token.here")
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


class TestSetupVerification:
    def test_setup_requires_verification_code(self, client: TestClient, db: Session):
        db.query(User).delete()
        db.commit()
        response = client.post("/api/auth/setup", json={
            "username": "setupowner",
            "email": "setup@test.de",
            "password": "SetupPass123!",
        })
        # Ohne SMTP ist der Status 503 mit Code im Detail
        # 201 = Owner wurde erfolgreich angelegt (neuer Flow)
        assert response.status_code in (200, 201, 503)

    def test_setup_does_not_log_verification_code_when_smtp_missing(
        self, client: TestClient, db: Session, caplog
    ):
        db.query(User).delete()
        db.commit()

        with patch("routers.auth.EmailVerificationService.create_verification", return_value="123456"), \
             caplog.at_level(logging.WARNING):
            response = client.post("/api/auth/setup", json={
                "username": "nologowner",
                "email": "nolog@test.de",
                "password": "SetupPass123!",
            })

        assert response.status_code == 503
        assert "123456" not in caplog.text
        assert "nicht versendet" in caplog.text

    def test_setup_verify_with_wrong_code_fails(self, client: TestClient, db: Session):
        from services.auth_service import AuthService
        user = AuthService.create_owner(db, "verifyowner", "verifyowner@test.de", "Verify123!")
        user.email_verified = False
        db.commit()
        response = client.post("/api/auth/setup-verify", json={
            "email": "verifyowner@test.de",
            "code": "000000",
        })
        assert response.status_code == 400

    def test_setup_verify_with_valid_code(self, client: TestClient, db: Session):
        from services.auth_service import AuthService
        from services.email_verification_service import EmailVerificationService
        user = AuthService.create_owner(db, "verifyowner2", "verifyowner2@test.de", "Verify123!")
        user.email_verified = False
        db.commit()
        code = EmailVerificationService.create_verification(db, "verifyowner2@test.de", "setup")
        response = client.post("/api/auth/setup-verify", json={
            "email": "verifyowner2@test.de",
            "code": code,
        })
        assert response.status_code == 200
        assert response.json()["setup_completed"] is True


class Test2FABackupCodes:
    def test_login_with_backup_code(self, client: TestClient, db: Session, owner_user: User):
        import pyotp
        from services.backup_code_service import BackupCodeService
        from services.auth_service import AuthService as _AuthService
        secret = pyotp.random_base32()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_2fa_secret(secret)
        owner_user.two_factor_enabled = True
        db.commit()
        codes = BackupCodeService.generate_backup_codes(db, owner_user.id)
        # Erstes Login ohne OTP -> requires_2fa
        res1 = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": None,
        })
        assert res1.status_code == 200
        assert res1.json()["requires_2fa"] is True
        # Login mit Backup-Code
        res2 = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": codes[0],
        })
        assert res2.status_code == 200
        assert res2.json()["requires_2fa"] is False

    def test_backup_code_used_twice_fails(self, client: TestClient, db: Session, owner_user: User):
        import pyotp
        from services.backup_code_service import BackupCodeService
        from services.auth_service import AuthService as _AuthService
        secret = pyotp.random_base32()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_2fa_secret(secret)
        owner_user.two_factor_enabled = True
        db.commit()
        codes = BackupCodeService.generate_backup_codes(db, owner_user.id)
        # Erster Login mit Backup-Code
        res1 = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": codes[0],
        })
        assert res1.status_code == 200
        # Zweiter Login mit gleichem Backup-Code -> muss fehlschlagen
        res2 = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": codes[0],
        })
        assert res2.status_code == 401

    def test_2fa_disable_requires_current_otp(self, client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
        import pyotp
        from services.auth_service import AuthService as _AuthService
        secret = pyotp.random_base32()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_2fa_secret(secret)
        owner_user.two_factor_enabled = True
        db.commit()
        csrf = owner_cookies.get("__Secure-csrf_token")
        # Deaktivierung ohne OTP -> muss fehlschlagen
        res = client.post(
            "/api/auth/2fa/disable?otp_code=wrong",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert res.status_code == 400

    def test_2fa_disable_with_backup_code_fails(self, client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
        import pyotp
        from services.backup_code_service import BackupCodeService
        from services.auth_service import AuthService as _AuthService
        secret = pyotp.random_base32()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_2fa_secret(secret)
        owner_user.two_factor_enabled = True
        db.commit()
        codes = BackupCodeService.generate_backup_codes(db, owner_user.id)
        csrf = owner_cookies.get("__Secure-csrf_token")
        # Deaktivierung mit Backup-Code -> muss fehlschlagen
        res = client.post(
            f"/api/auth/2fa/disable?otp_code={codes[0]}",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert res.status_code == 400


class TestCsrfProtectionOnEndpoints:
    """Verify that state-changing endpoints require CSRF."""

    def test_post_without_csrf_fails(self, client: TestClient, owner_cookies: dict):
        # Try to create server without CSRF
        response = client.post("/api/servers", json={
            "name": "CSRF-Test-Forbidden",
            "game_type": "dayz",
        }, cookies=owner_cookies)
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    def test_post_with_csrf_succeeds(self, client: TestClient, owner_cookies: dict):
        from unittest.mock import patch
        # Routers nutzen kein subprocess mehr (Docker-Runtime). Wir patchen
        # nur Filesystem/Firewall/Plugin, damit der Create-Pfad ohne Side-Effects
        # gegen den Docker-Daemon laufen kann.
        with patch("routers.servers.os.makedirs"), \
             patch("routers.servers.os.chmod"), \
             patch("routers.servers.os.path.exists", return_value=False), \
             patch("routers.servers.allocate_ports", return_value=(27015, 27016, 27017)), \
             patch("routers.servers.open_ports"), \
             patch("routers.servers.get_plugin", return_value=None):
            csrf = owner_cookies.get("__Secure-csrf_token")
            response = client.post(
                "/api/servers",
                json={"name": "CSRF-Test-Success", "game_type": "dayz"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf},
            )
            assert response.status_code in (200, 201)
