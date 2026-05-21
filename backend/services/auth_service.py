import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import hashlib
import secrets
import time
import uuid

from cryptography.fernet import Fernet
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from config import settings
from models import User, RefreshToken

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# ── JWT Blacklist (In-Memory, TTL-basiert) ──
_jwt_blacklist: dict[str, float] = {}
_BLACKLIST_TTL_SECONDS = 15 * 60  # 15 Minuten


def _cleanup_blacklist() -> None:
    """Entfernt Blacklist-Eintraege aelter als 15 Minuten."""
    now = time.time()
    expired = [jti for jti, ts in _jwt_blacklist.items() if now - ts > _BLACKLIST_TTL_SECONDS]
    for jti in expired:
        del _jwt_blacklist[jti]


class AuthService:
    # ── Password ──
    @staticmethod
    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)

    # ── 2FA Secret Encryption ──
    @staticmethod
    def _get_fernet() -> Fernet:
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
        return Fernet(key)

    @staticmethod
    def encrypt_2fa_secret(secret: str) -> str:
        f = AuthService._get_fernet()
        return f.encrypt(secret.encode()).decode()

    @staticmethod
    def decrypt_2fa_secret(encrypted: str) -> str:
        f = AuthService._get_fernet()
        return f.decrypt(encrypted.encode()).decode()

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

    @staticmethod
    def blacklist_jwt(jti: str) -> None:
        """Speichert JTI in der In-Memory-Blacklist mit aktuellem Timestamp."""
        _cleanup_blacklist()
        _jwt_blacklist[jti] = time.time()

    @staticmethod
    def is_jwt_blacklisted(jti: str) -> bool:
        """Prueft ob JTI in der Blacklist ist und noch nicht abgelaufen (TTL 15 Min)."""
        _cleanup_blacklist()
        return jti in _jwt_blacklist

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
        return db.query(User).filter(User.email == email).first()

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
