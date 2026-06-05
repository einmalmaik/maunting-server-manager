import pyotp
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, RefreshToken, JwtBlacklist, EmailVerification, AuditLog, OAuthUserLink, OAuthProvider
from services.auth_service import AuthService
from services.backup_code_service import BackupCodeService


class TestAccountDeletion:
    @pytest.fixture
    def normal_user(self, db: Session) -> User:
        """Fixture for a normal (non-owner) user."""
        user = db.query(User).filter(User.username == "normal_test_user").first()
        if not user:
            user = AuthService.create_user(db, "normal_test_user", "normal@test.de", "UserPass123!")
            user.email_verified = True
            db.commit()
        return user

    @pytest.fixture
    def normal_cookies(self, client: TestClient, normal_user: User) -> dict:
        """Login normal user and return cookies (including CSRF token)."""
        response = client.post("/api/auth/login", json={
            "username": "normal_test_user",
            "password": "UserPass123!",
            "otp_code": None,
        })
        assert response.status_code == 200
        return dict(response.cookies)

    def test_delete_normal_account_success(self, client: TestClient, db: Session, normal_user: User, normal_cookies: dict):
        csrf = normal_cookies.get("__Secure-csrf_token")
        user_id = normal_user.id
        user_email = normal_user.email
        
        # Add some related data to test atomic deletion
        rt = RefreshToken(
            user_id=user_id,
            token_hash="test_rt_hash",
            family="test_fam",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1)
        )
        db.add(rt)
        
        jwt_bl = JwtBlacklist(jti="test_jti", user_id=user_id, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        db.add(jwt_bl)
        
        ev = EmailVerification(
            email=user_email,
            code_hash="test_hash",
            purpose="register",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        db.add(ev)
        
        log = AuditLog(user_id=user_id, action="test_action")
        db.add(log)
        
        db.commit()

        # Send delete request
        response = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": "UserPass123!", "confirmation": "delete", "otp_code": None},
            cookies=normal_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Account gelöscht"

        # Verify cookies are cleared
        assert "__Secure-access_token" not in response.cookies or response.cookies.get("__Secure-access_token") == ""

        # Verify DB is cleaned up
        assert db.query(User).filter(User.id == user_id).first() is None
        assert db.query(RefreshToken).filter(RefreshToken.user_id == user_id).first() is None
        assert db.query(JwtBlacklist).filter(JwtBlacklist.user_id == user_id).first() is None
        assert db.query(EmailVerification).filter(EmailVerification.email == user_email).first() is None
        
        # Audit log must remain but user_id set to None
        db_log = db.query(AuditLog).filter(AuditLog.action == "test_action").first()
        assert db_log is not None
        assert db_log.user_id is None

    def test_delete_fails_with_wrong_password(self, client: TestClient, normal_user: User, normal_cookies: dict):
        csrf = normal_cookies.get("__Secure-csrf_token")
        response = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": "WrongPass123!", "confirmation": "delete", "otp_code": None},
            cookies=normal_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 401
        assert "Passwort ungültig" in response.json()["detail"]

    def test_delete_requires_otp_when_2fa_enabled(self, client: TestClient, db: Session, normal_user: User, normal_cookies: dict):
        # Enable 2FA for normal user
        secret = pyotp.random_base32()
        normal_user.two_factor_secret_encrypted = AuthService.encrypt_2fa_secret(secret)
        normal_user.two_factor_enabled = True
        db.commit()

        csrf = normal_cookies.get("__Secure-csrf_token")
        user_id = normal_user.id
        
        # Try without OTP
        response = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": "UserPass123!", "confirmation": "delete", "otp_code": None},
            cookies=normal_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 401
        assert "2FA-Code erforderlich" in response.json()["detail"]

        # Try with invalid OTP
        response = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": "UserPass123!", "confirmation": "delete", "otp_code": "000000"},
            cookies=normal_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 401
        assert "Ungültiger 2FA-Code" in response.json()["detail"]

        # Try with backup code (must be rejected)
        codes = BackupCodeService.generate_backup_codes(db, user_id)
        response = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": "UserPass123!", "confirmation": "delete", "otp_code": codes[0]},
            cookies=normal_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        # Rejects in Pydantic schema or verify_current_2fa_code because otp_code doesn't match TOTP
        assert response.status_code in (401, 422)

        # Try with valid TOTP code
        totp = pyotp.TOTP(secret)
        response = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": "UserPass123!", "confirmation": "delete", "otp_code": totp.now()},
            cookies=normal_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200

    def test_owner_cannot_delete_self(self, client: TestClient, owner_user: User, owner_cookies: dict):
        csrf = owner_cookies.get("__Secure-csrf_token")
        response = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": "OwnerPass123!", "confirmation": "delete", "otp_code": None},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 403
        assert "Owner-Account kann nicht gelöscht werden" in response.json()["detail"]

    def test_social_only_account_can_delete_without_password(self, client: TestClient, db: Session):
        """Social-only accounts (with OAuthUserLink) can delete without current password.
        Only confirmation + (if 2FA) otp required. Central logic in router.
        """
        from services.auth_service import AuthService
        # Fresh user for this test (avoid pollution from other tests that delete)
        user = AuthService.create_user(db, "social_delete_test", "socialdel@test.de", "TempPass123!")
        user.email_verified = True
        db.commit()
        db.refresh(user)
        user_id = user.id  # capture numeric id before delete removes the row

        # Add discord link (real built-in provider) to mark as social
        provider = db.query(OAuthProvider).filter(OAuthProvider.slug == "discord").first()
        if not provider:
            provider = OAuthProvider(slug="discord", name="Discord", preset="discord", client_id="dummy-test", enabled=True)
            db.add(provider)
            db.commit()
            db.refresh(provider)

        link = OAuthUserLink(
            provider_id=provider.id, 
            user_id=user.id, 
            subject="social-xyz", 
            email_at_link=user.email,
            username_at_link=user.username
        )
        db.add(link)
        db.commit()

        # Login to get cookies + csrf
        login = client.post("/api/auth/login", json={
            "username": "social_delete_test",
            "password": "TempPass123!",
            "otp_code": None,
        })
        assert login.status_code == 200
        cookies = dict(login.cookies)
        csrf = cookies.get("__Secure-csrf_token")

        # Delete WITHOUT password, WITH confirmation
        resp = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": None, "confirmation": "delete", "otp_code": None},
            cookies=cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("message") == "Account gelöscht"
        assert db.query(User).filter(User.id == user_id).first() is None

    def test_delete_fails_without_confirmation_word(self, client: TestClient, db: Session):
        from services.auth_service import AuthService
        user = AuthService.create_user(db, "confirmfail_test", "cfail@test.de", "Pass1234!")
        user.email_verified = True
        db.commit()
        db.refresh(user)
        user_id = user.id  # capture numeric id before delete removes the row

        login = client.post("/api/auth/login", json={"username": "confirmfail_test", "password": "Pass1234!", "otp_code": None})
        assert login.status_code == 200
        cookies = dict(login.cookies)
        csrf = cookies.get("__Secure-csrf_token")

        resp = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": "Pass1234!", "confirmation": "wrongword", "otp_code": None},
            cookies=cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        assert "Bestätigung delete erforderlich" in resp.json().get("detail", "")


    def test_social_only_via_custom_provider_can_delete_without_password(self, client: TestClient, db: Session):
        """Social-only accounts using a *custom* OAuth provider (custom_oauth2 or custom_oidc)
        must also be able to delete without providing current password.
        The decision is based solely on existence of any OAuthUserLink, independent of preset.
        """
        from services.auth_service import AuthService
        user = AuthService.create_user(db, "custom_social_delete", "customsocial@test.de", "TempPass123!")
        user.email_verified = True
        db.commit()
        db.refresh(user)
        user_id = user.id

        # Create a custom provider (simulates admin-added custom_oauth2)
        provider = db.query(OAuthProvider).filter(OAuthProvider.slug == "custom_test").first()
        if not provider:
            provider = OAuthProvider(
                slug="custom_test",
                name="Custom Test",
                preset="custom_oauth2",
                client_id="dummy-custom",
                # For custom the other endpoints are set in UI, but not needed for link test
                enabled=True,
            )
            db.add(provider)
            db.commit()
            db.refresh(provider)

        # Create the link exactly as oauth_service.link_provider_to_user would
        link = OAuthUserLink(
            provider_id=provider.id,
            user_id=user.id,
            subject="custom-subject-xyz",
            email_at_link=user.email,
            username_at_link=user.username,
        )
        db.add(link)
        db.commit()

        # Login (the temp password is only for this login, deletion must skip it)
        login = client.post("/api/auth/login", json={
            "username": "custom_social_delete",
            "password": "TempPass123!",
            "otp_code": None,
        })
        assert login.status_code == 200
        cookies = dict(login.cookies)
        csrf = cookies.get("__Secure-csrf_token")

        # Delete WITHOUT password, only confirmation
        resp = client.request(
            "DELETE",
            "/api/auth/delete-account",
            json={"password": None, "confirmation": "delete", "otp_code": None},
            cookies=cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("message") == "Account gelöscht"
        assert db.query(User).filter(User.id == user_id).first() is None
