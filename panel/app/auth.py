from __future__ import annotations

from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User


password_hasher = PasswordHasher()

# Pre-computed dummy hash used in authenticate_user to keep response time
# constant regardless of whether the username exists (prevents timing-based
# username enumeration).
_DUMMY_HASH = password_hasher.hash("dummy")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active:
        # Always run a dummy verification so the response time is the same
        # whether or not the username exists (prevents timing-based enumeration).
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_id(db: Session, user_id: int | None) -> User | None:
    if user_id is None:
        return None
    return db.get(User, user_id)
