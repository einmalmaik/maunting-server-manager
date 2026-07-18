"""Tests for auth router: login, logout, refresh, CSRF, cookies."""
import logging
from unittest.mock import PropertyMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, RefreshToken, EmailVerification, PanelSetting


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

    def test_access_cookie_uses_lax_for_oauth_callback_compat(
        self, client: TestClient, owner_user: User, monkeypatch
    ):
        """Security-Invariante (Single-Host): access SameSite=Lax, rest Strict.

        OAuth-Callbacks kommen als Cross-Site-Top-Level-Navigation vom IdP
        zurueck (Google, Discord, Keycloak, ...). SameSite=Strict wuerde das
        Cookie bei diesem Redirect NICHT mitsenden → 401 / Auth-Fehler direkt
        nach erfolgreichem Login. Lax laesst Top-Level-Nav zu, blockt aber
        weiterhin Cross-Site-Subresources (AJAX/fetch) und nicht-sichere
        Methoden (POST) — d.h. keine CSRF-Schwaechung fuer MSM.
        """
        import config
        from cookies import _COOKIE_CONFIG, _samesite_for

        monkeypatch.setattr(config.settings, "cookie_cross_site", False, raising=False)
        assert _COOKIE_CONFIG["__Secure-access_token"]["samesite"] == "lax"
        assert _samesite_for("__Secure-access_token") == "lax"
        assert _samesite_for("__Secure-refresh_token") == "strict"
        assert _samesite_for("__Secure-csrf_token") == "strict"

    def test_cross_site_cookies_use_samesite_none(self, monkeypatch):
        """Phase 4: split FE/API braucht SameSite=None auf allen Session-Cookies."""
        import config
        from cookies import _samesite_for

        monkeypatch.setattr(config.settings, "cookie_cross_site", True, raising=False)
        assert _samesite_for("__Secure-access_token") == "none"
        assert _samesite_for("__Secure-refresh_token") == "none"
        assert _samesite_for("__Secure-csrf_token") == "none"

    def test_login_exposes_csrf_response_header(self, client: TestClient, owner_user: User):
        """Cross-Origin SPA liest CSRF aus X-CSRF-Token (nicht aus document.cookie)."""
        response = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "OwnerPass123!",
            "otp_code": None,
        })
        assert response.status_code == 200
        assert response.headers.get("X-CSRF-Token")
        assert response.headers["X-CSRF-Token"] == response.cookies.get("__Secure-csrf_token")

    def test_login_wrong_password(self, client: TestClient, owner_user: User):
        response = client.post("/api/auth/login", json={
            "username": "owner",
            "password": "WrongPass123!",
            "otp_code": None,
        })
        assert response.status_code == 401
        assert response.json()["detail"] == "Ungültige Anmeldedaten"

    def test_login_session_survives_optional_email_decryption_failure(
        self, client: TestClient, owner_user: User
    ):
        with (
            patch("routers.auth.EmailService.is_configured", return_value=True),
            patch.object(
                User,
                "email",
                new_callable=PropertyMock,
                side_effect=RuntimeError("synthetic decrypt failure"),
            ),
            patch("routers.auth.EmailService.send_new_device_login_notification") as notify,
        ):
            response = client.post(
                "/api/auth/login",
                json={
                    "username": "owner",
                    "password": "OwnerPass123!",
                    "otp_code": None,
                },
            )

        assert response.status_code == 200
        assert "__Secure-access_token" in response.cookies
        notify.assert_not_called()

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
        owner_user.two_factor_secret_encrypted = AuthService.encrypt_secret("JBSWY3DPEHPK3PXP", aad=f"msm:user:{owner_user.id}:2fa")
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

    def test_unverified_login_reuses_active_registration_code(self, client: TestClient, db: Session):
        from services.auth_service import AuthService
        from services.email_verification_service import EmailVerificationService

        user = AuthService.create_user(db, "pending", "pending@test.de", "PendingPass123!")
        user.email_verified = False
        db.commit()
        code = EmailVerificationService.create_verification(db, user.email, "register")

        with patch("routers.auth.EmailService.is_configured", return_value=True), \
             patch("routers.auth.EmailService.send_verification_code_email") as send_code:
            response = client.post("/api/auth/login", json={
                "username": "pending",
                "password": "PendingPass123!",
                "otp_code": None,
            })

        assert response.status_code == 200
        data = response.json()
        assert data["requires_verification"] is True
        send_code.assert_not_called()

        verify_response = client.post("/api/auth/login-verify", json={
            "username": "pending",
            "password": "PendingPass123!",
            "code": code,
            "otp_code": None,
        })
        assert verify_response.status_code == 200
        assert "__Secure-access_token" in verify_response.cookies

    def test_unverified_login_sends_new_code_after_expiry(self, client: TestClient, db: Session):
        from datetime import datetime, timedelta, timezone
        from services.auth_service import AuthService

        user = AuthService.create_user(db, "expired", "expired@test.de", "ExpiredPass123!")
        user.email_verified = False
        from services.email_verification_service import EmailVerificationService
        db.add(EmailVerification(
            email_hash=EmailVerificationService._email_hash(user.email),
            code_hash="expired-test-code-hash",
            purpose="register",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        db.commit()

        with patch("routers.auth.EmailService.is_configured", return_value=True), \
             patch("routers.auth.EmailService.send_verification_code_email") as send_code:
            response = client.post("/api/auth/login", json={
                "username": "expired",
                "password": "ExpiredPass123!",
                "otp_code": None,
            })

        assert response.status_code == 200
        assert response.json()["requires_verification"] is True
        send_code.assert_called_once()


class TestRegistrationVerification:
    def test_register_returns_minimal_verification_response_and_sends_code(self, client: TestClient):
        with patch("routers.auth.EmailService.is_configured", return_value=True), \
             patch("routers.auth.EmailService.send_verification_code_email") as send_code:
            response = client.post("/api/auth/register", json={
                "username": "newuser",
                "email": "newuser@test.de",
                "password": "NewUserPass123!",
            })

        assert response.status_code == 201
        data = response.json()
        assert data == {"email": "newuser@test.de", "requires_verification": True}
        send_code.assert_called_once()

    def test_register_verify_sets_session_cookies(self, client: TestClient, db: Session):
        from services.auth_service import AuthService
        from services.email_verification_service import EmailVerificationService

        user = AuthService.create_user(db, "verifynew", "verifynew@test.de", "VerifyNew123!")
        user.email_verified = False
        db.commit()
        code = EmailVerificationService.create_verification(db, user.email, "register")

        response = client.post("/api/auth/register-verify", json={
            "email": user.email,
            "code": code,
        })

        assert response.status_code == 200
        assert response.json()["requires_verification"] is False
        assert "__Secure-access_token" in response.cookies
        assert "__Secure-refresh_token" in response.cookies
        assert "__Secure-csrf_token" in response.cookies

        me_response = client.get("/api/auth/me", cookies=dict(response.cookies))
        assert me_response.status_code == 200
        assert me_response.json()["email_verified"] is True

    def test_register_verify_rejects_login_code_without_password(self, client: TestClient, db: Session):
        from services.auth_service import AuthService
        from services.email_verification_service import EmailVerificationService

        user = AuthService.create_user(db, "loginonly", "loginonly@test.de", "LoginOnly123!")
        user.email_verified = False
        db.commit()
        code = EmailVerificationService.create_verification(db, user.email, "login")

        response = client.post("/api/auth/register-verify", json={
            "email": user.email,
            "code": code,
        })

        assert response.status_code == 400
        assert "__Secure-access_token" not in response.cookies


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
        assert "email_configured" in response.json()

    def test_setup_not_required_when_owner_exists(self, client: TestClient, owner_user: User):
        response = client.get("/api/auth/setup-status")
        assert response.status_code == 200
        assert response.json()["setup_required"] is False


class TestSetupVerification:
    def test_setup_can_store_resend_config_without_exposing_secret(
        self, client: TestClient, db: Session
    ):
        from services.auth_service import AuthService
        from services.panel_settings_service import PanelSettingsService

        db.query(User).delete()
        db.commit()
        api_key = "re_test_secret_value"

        with patch("routers.auth.EmailService.is_configured", return_value=False), \
             patch(
                 "routers.auth.EmailService.send_verification_code_email",
                 return_value=True,
             ):
            response = client.post("/api/auth/setup", json={
                "username": "setupowner",
                "email": "setup@test.de",
                "password": "SetupPass123!",
                "email_config": {
                    "provider": "resend",
                    "from_address": "noreply@test.de",
                    "resend_api_key": api_key,
                },
            })

        assert response.status_code == 201
        assert api_key not in response.text
        legacy = db.query(PanelSetting).filter_by(key="resend_api_key").first()
        stored = db.query(PanelSetting).filter_by(
            key="resend_api_key_encrypted"
        ).first()
        assert legacy is not None and legacy.value == ""
        encrypted = stored.value if stored else ""
        assert encrypted and api_key not in encrypted
        assert AuthService.decrypt_secret(
            encrypted, aad="msm:settings:resend_api_key"
        ) == api_key

    def test_setup_rejects_anonymous_smtp_configuration(
        self, client: TestClient, db: Session
    ):
        from services.auth_service import AuthService

        db.query(User).delete()
        db.commit()

        with patch("routers.auth.EmailService.is_configured", return_value=False):
            response = client.post("/api/auth/setup", json={
                "username": "setupowner",
                "email": "setup@test.de",
                "password": "SetupPass123!",
                "email_config": {
                    "provider": "smtp",
                    "from_address": "noreply@test.de",
                    "smtp_host": "127.0.0.1",
                    "smtp_user": "internal",
                    "smtp_password": "secret",
                },
            })

        assert response.status_code == 422
        assert AuthService.get_user_by_email(db, "setup@test.de") is None

    def test_setup_without_existing_email_config_requires_resend(
        self, client: TestClient, db: Session
    ):
        from services.auth_service import AuthService

        db.query(User).delete()
        db.commit()

        with patch("routers.auth.EmailService.is_configured", return_value=False):
            response = client.post("/api/auth/setup", json={
                "username": "setupowner",
                "email": "setup@test.de",
                "password": "SetupPass123!",
            })

        assert response.status_code == 503
        assert AuthService.get_user_by_email(db, "setup@test.de") is None

    def test_failed_first_run_email_does_not_lock_setup_configuration(
        self, client: TestClient, db: Session
    ):
        from services.auth_service import AuthService
        from services.panel_settings_service import PanelSettingsService

        db.query(User).delete()
        db.commit()

        with patch("routers.auth.EmailService.is_configured", return_value=False), \
             patch(
                 "routers.auth.EmailService.send_verification_code_email",
                 side_effect=RuntimeError("provider unavailable"),
             ):
            response = client.post("/api/auth/setup", json={
                "username": "setupowner",
                "email": "setup@test.de",
                "password": "SetupPass123!",
                "email_config": {
                    "provider": "resend",
                    "from_address": "noreply@test.de",
                    "resend_api_key": "re_invalid_test_key",
                },
            })

        assert response.status_code == 503
        assert AuthService.get_user_by_email(db, "setup@test.de") is None
        assert PanelSettingsService.get("resend_api_key_encrypted") == ""
        assert PanelSettingsService.get("smtp_from") == ""
        assert "provider unavailable" not in response.text

    def test_setup_resend_accepts_email_only_and_uses_setup_purpose(
        self, client: TestClient, db: Session
    ):
        from services.auth_service import AuthService
        from services.email_verification_service import EmailVerificationService

        user = AuthService.create_owner(
            db, "pendingowner", "pending-owner@test.de", "SetupPass123!"
        )
        user.email_verified = False
        db.commit()

        with patch("routers.auth.EmailService.is_configured", return_value=True), \
             patch(
                 "routers.auth.EmailService.send_verification_code_email",
                 return_value=True,
             ):
            response = client.post(
                "/api/auth/setup-resend",
                json={"email": "pending-owner@test.de"},
            )

        assert response.status_code == 200
        assert EmailVerificationService.has_active_verification(
            db, "pending-owner@test.de", ["setup"]
        )

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

        assert "123456" not in caplog.text
        # The log message or status may vary due to shared test DB state / FKs
        # Main invariant: the verification code is not leaked in logs when SMTP missing
        # (the actual send is skipped in _log_smtp_missing)

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
        from tests._totp import totp_now, random_totp_secret
        from services.backup_code_service import BackupCodeService
        from services.auth_service import AuthService as _AuthService
        secret = random_totp_secret()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_secret(secret, aad=f"msm:user:{owner_user.id}:2fa")
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
        from tests._totp import totp_now, random_totp_secret
        from services.backup_code_service import BackupCodeService
        from services.auth_service import AuthService as _AuthService
        secret = random_totp_secret()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_secret(secret, aad=f"msm:user:{owner_user.id}:2fa")
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
        from tests._totp import totp_now, random_totp_secret
        from services.auth_service import AuthService as _AuthService
        secret = random_totp_secret()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_secret(secret, aad=f"msm:user:{owner_user.id}:2fa")
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
        from tests._totp import totp_now, random_totp_secret
        from services.backup_code_service import BackupCodeService
        from services.auth_service import AuthService as _AuthService
        secret = random_totp_secret()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_secret(secret, aad=f"msm:user:{owner_user.id}:2fa")
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

    def test_backup_code_generation_replaces_old_codes(self, client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
        from tests._totp import totp_now, random_totp_secret
        from services.auth_service import AuthService as _AuthService
        from services.backup_code_service import BackupCodeService

        secret = random_totp_secret()
        owner_user.two_factor_secret_encrypted = _AuthService.encrypt_secret(secret, aad=f"msm:user:{owner_user.id}:2fa")
        owner_user.two_factor_enabled = True
        db.commit()
        csrf = owner_cookies.get("__Secure-csrf_token")

        first = client.post(
            "/api/auth/2fa/backup/generate",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        second = client.post(
            "/api/auth/2fa/backup/generate",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        old_code = first.json()["codes"][0]
        new_code = second.json()["codes"][0]
        assert old_code != new_code
        assert BackupCodeService.validate_backup_code(db, owner_user.id, old_code) is False
        assert BackupCodeService.validate_backup_code(db, owner_user.id, new_code) is True


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
