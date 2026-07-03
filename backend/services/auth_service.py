import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import hashlib
import secrets

from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from config import settings
from models import User, RefreshToken
from services.dis_client import DisClient

# Temporaer fuer Passwort-Migration (passlib -> DIS Argon2id).
# Wird entfernt sobald alle User mindestens einmal eingeloggt waren
# und ihre Hashes im msm-pw-v1: Format vorliegen.
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


class AuthService:
    # ── Password ──
    @staticmethod
    def hash_password(password: str) -> str:
        return DisClient.hash_password(password)

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        # DIS-Hash (msm-pw-v1:) -> Sidecar verifiziert
        if DisClient.is_dis_hash(hashed):
            return DisClient.verify_password(plain, hashed)
        # Legacy passlib-Hash ($argon2...) -> passlib verifiziert (Migration)
        try:
            return _pwd_context.verify(plain, hashed)
        except Exception:
            return False

    @staticmethod
    def rehash_password_if_needed(db: Session, user: User, plain_password: str) -> None:
        """Re-hasht ein Passwort mit DIS wenn der Hash noch im legacy Format ist.

        Wird nach erfolgreichem Login aufgerufen (lazy Migration passlib -> DIS).
        """
        if not DisClient.is_dis_hash(user.password_hash):
            user.password_hash = DisClient.hash_password(plain_password)
            db.commit()

    # ── Secret Encryption (DIS AES-256-GCM) ──
    # Alle Secrets werden ueber den DIS Sidecar verschluesselt.
    # AAD (Associated Authenticated Data) bindet den Ciphertext an seinen
    # Context und verhindert Swap-Angriffe.

    @staticmethod
    def encrypt_secret(plaintext: str, aad: str | None = None) -> str:
        return DisClient.encrypt(plaintext, aad)

    @staticmethod
    def decrypt_secret(ciphertext: str, aad: str | None = None) -> str:
        return DisClient.decrypt(ciphertext, aad)

    # ── Access Token (JWT) ──
    @staticmethod
    def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
        to_encode.update({"exp": expire, "type": "access"})
        return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

    @staticmethod
    def decode_token(token: str) -> dict | None:
        try:
            return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        except JWTError:
            return None

    # ── Refresh Token (DB-gestuetzt, rotierbar, revozierbar) ──
    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def create_refresh_token(db: Session, user_id: int, family: str | None = None) -> str:
        """Erstellt ein neues Refresh-Token, speichert Hash in DB, gibt Plain-Token zurueck."""
        plain_token = secrets.token_urlsafe(32)
        token_hash = AuthService._hash_token(plain_token)
        token_family = family or secrets.token_urlsafe(16)

        expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)

        rt = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family=token_family,
            expires_at=expires_at,
        )
        db.add(rt)
        db.commit()
        return plain_token

    @staticmethod
    def validate_refresh_token(db: Session, plain_token: str) -> RefreshToken | None:
        """Prueft Plain-Token gegen DB-Hash. Gibt DB-Eintrag zurueck oder None."""
        token_hash = AuthService._hash_token(plain_token)
        rt = db.query(RefreshToken).filter(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.used_at.is_(None),
            RefreshToken.expires_at > datetime.now(timezone.utc),
        ).first()
        return rt

    @staticmethod
    def mark_refresh_token_used(db: Session, rt: RefreshToken) -> None:
        """Markiert ein Refresh-Token als verwendet (bei Rotation)."""
        rt.used_at = datetime.now(timezone.utc)
        db.commit()

    @staticmethod
    def revoke_refresh_token(db: Session, rt: RefreshToken) -> None:
        rt.revoked_at = datetime.now(timezone.utc)
        db.commit()

    @staticmethod
    def revoke_all_user_refresh_tokens(db: Session, user_id: int) -> None:
        db.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": datetime.now(timezone.utc)})
        db.commit()

    # ── CSRF Token ──
    @staticmethod
    def create_csrf_token() -> str:
        return secrets.token_urlsafe(32)

    # ── Generic Token ──
    @staticmethod
    def generate_token() -> str:
        return uuid4().hex

    # ── User CRUD ──
    @staticmethod
    def get_user_by_username(db: Session, username: str) -> User | None:
        return db.query(User).filter(User.username == username).first()

    @staticmethod
    def get_user_by_email(db: Session, email: str) -> User | None:
        return db.query(User).filter(User.email_hash == User._email_hash(email)).first()

    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> User | None:
        return db.query(User).filter(User.id == user_id).first()

    @staticmethod
    def is_owner_exists(db: Session) -> bool:
        return db.query(User).filter(User.is_owner == True).first() is not None

    @staticmethod
    def create_owner(db: Session, username: str, email: str, password: str) -> User:
        user = User(
            username=username,
            email=email,
            password_hash=AuthService.hash_password(password),
            is_owner=True,
            email_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def create_user(db: Session, username: str, email: str, password: str) -> User:
        user = User(
            username=username,
            email=email,
            password_hash=AuthService.hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def set_password_reset_token(db: Session, user: User) -> str:
        token = AuthService.generate_token()
        user.password_reset_token = token
        user.password_reset_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.commit()
        return token

    @staticmethod
    def reset_password(db: Session, user: User, new_password: str) -> None:
        user.password_hash = AuthService.hash_password(new_password)
        user.password_reset_token = None
        user.password_reset_expires = None
        # Sicherheit: Bei Passwort-Aenderung alle Refresh-Tokens revozieren
        AuthService.revoke_all_user_refresh_tokens(db, user.id)
        db.commit()

    @staticmethod
    def verify_current_2fa_code(user: User, otp_code: str) -> bool:
        if not user.two_factor_secret_encrypted:
            return False
        try:
            secret = AuthService.decrypt_secret(
                user.two_factor_secret_encrypted,
                aad=f"msm:user:{user.id}:2fa",
            )
            if not secret:
                return False
            return DisClient.verify_totp(secret, otp_code)
        except Exception:
            return False

    @staticmethod
    def delete_account_atomically(db: Session, user: User) -> None:
        from models.jwt_blacklist import JwtBlacklist
        from models.email_verification import EmailVerification
        from models.audit_log import AuditLog
        from models.server_permission import ServerPermission

        # Delete JwtBlacklist items
        db.query(JwtBlacklist).filter(JwtBlacklist.user_id == user.id).delete()
        
        # Delete EmailVerification items
        db.query(EmailVerification).filter(EmailVerification.email == user.email).delete()

        # Set user_id to None on AuditLog items
        db.query(AuditLog).filter(AuditLog.user_id == user.id).update({"user_id": None})

        # Set granted_by to None on ServerPermission items granted by this user
        db.query(ServerPermission).filter(ServerPermission.granted_by == user.id).update({"granted_by": None})

        # Finally delete user
        db.delete(user)
        db.commit()
