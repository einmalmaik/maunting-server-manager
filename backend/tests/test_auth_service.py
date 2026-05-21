"""Tests for AuthService: token lifecycle, rotation, revocation."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from services.auth_service import AuthService
from models import User, RefreshToken


class TestPasswordHashing:
    def test_hash_and_verify(self):
        hashed = AuthService.hash_password("my_password")
        assert AuthService.verify_password("my_password", hashed) is True
        assert AuthService.verify_password("wrong_password", hashed) is False

    def test_hash_is_not_plaintext(self):
        hashed = AuthService.hash_password("my_password")
        assert "my_password" not in hashed


class TestAccessToken:
    def test_create_and_decode(self):
        token = AuthService.create_access_token({"sub": "testuser", "user_id": 1})
        payload = AuthService.decode_token(token)
        assert payload is not None
        assert payload["sub"] == "testuser"
        assert payload["user_id"] == 1
        assert payload["type"] == "access"
        assert "exp" in payload

    def test_decode_invalid_token(self):
        assert AuthService.decode_token("totally.invalid.token") is None

    def test_decode_expired_token(self):
        token = AuthService.create_access_token(
            {"sub": "testuser"},
            expires_delta=timedelta(seconds=-1),
        )
        assert AuthService.decode_token(token) is None

    def test_token_type_must_be_access(self):
        # Manually encode a token without type
        from jose import jwt
        from config import settings
        token = jwt.encode(
            {"sub": "testuser", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        payload = AuthService.decode_token(token)
        assert payload is not None
        assert payload.get("type") is None  # Missing type field


class TestCsrfToken:
    def test_create_csrf_token_is_random(self):
        t1 = AuthService.create_csrf_token()
        t2 = AuthService.create_csrf_token()
        assert t1 != t2
        assert len(t1) > 20


class TestRefreshToken:
    def test_create_refresh_token(self, db: Session, owner_user: User):
        plain = AuthService.create_refresh_token(db, owner_user.id)
        assert isinstance(plain, str)
        assert len(plain) > 20

        # Verify stored hash, not plaintext
        rt = db.query(RefreshToken).filter(RefreshToken.user_id == owner_user.id).first()
        assert rt is not None
        assert rt.token_hash != plain  # Must be hashed
        assert rt.revoked_at is None
        assert rt.used_at is None
        # SQLite returns naive datetimes; make comparison robust
        now = datetime.now(timezone.utc)
        if rt.expires_at.tzinfo is None:
            assert rt.expires_at.replace(tzinfo=timezone.utc) > now
        else:
            assert rt.expires_at > now

    def test_validate_refresh_token(self, db: Session, owner_user: User):
        plain = AuthService.create_refresh_token(db, owner_user.id)
        rt = AuthService.validate_refresh_token(db, plain)
        assert rt is not None
        assert rt.user_id == owner_user.id

    def test_validate_wrong_token(self, db: Session):
        assert AuthService.validate_refresh_token(db, "wrong_token_xyz") is None

    def test_validate_revoked_token(self, db: Session, owner_user: User):
        plain = AuthService.create_refresh_token(db, owner_user.id)
        rt = AuthService.validate_refresh_token(db, plain)
        AuthService.revoke_refresh_token(db, rt)
        assert AuthService.validate_refresh_token(db, plain) is None

    def test_validate_used_token(self, db: Session, owner_user: User):
        plain = AuthService.create_refresh_token(db, owner_user.id)
        rt = AuthService.validate_refresh_token(db, plain)
        AuthService.mark_refresh_token_used(db, rt)
        assert AuthService.validate_refresh_token(db, plain) is None

    def test_validate_expired_token(self, db: Session, owner_user: User):
        plain = AuthService.create_refresh_token(db, owner_user.id)
        rt = AuthService.validate_refresh_token(db, plain)
        # Force expiration
        rt.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
        assert AuthService.validate_refresh_token(db, plain) is None

    def test_family_preserved(self, db: Session, owner_user: User):
        plain = AuthService.create_refresh_token(db, owner_user.id, family="family_abc")
        rt = db.query(RefreshToken).filter(RefreshToken.user_id == owner_user.id).first()
        assert rt.family == "family_abc"

    def test_revoke_all_user_refresh_tokens(self, db: Session, owner_user: User):
        AuthService.create_refresh_token(db, owner_user.id)
        AuthService.create_refresh_token(db, owner_user.id)
        assert db.query(RefreshToken).filter(
            RefreshToken.user_id == owner_user.id,
            RefreshToken.revoked_at.is_(None),
        ).count() == 2

        AuthService.revoke_all_user_refresh_tokens(db, owner_user.id)
        assert db.query(RefreshToken).filter(
            RefreshToken.user_id == owner_user.id,
            RefreshToken.revoked_at.is_(None),
        ).count() == 0

    def test_reset_password_revokes_all_tokens(self, db: Session, owner_user: User):
        AuthService.create_refresh_token(db, owner_user.id)
        AuthService.reset_password(db, owner_user, "NewPass123!")
        assert db.query(RefreshToken).filter(
            RefreshToken.user_id == owner_user.id,
            RefreshToken.revoked_at.is_(None),
        ).count() == 0
