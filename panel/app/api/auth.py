from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import authenticate_user, verify_password
from ..models import AuthThrottle, BackupCode, User
from ..permissions import get_effective_permissions
from .deps import get_current_user, get_db

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_LOGIN_ATTEMPTS = 5
_MAX_2FA_ATTEMPTS = 5
_LOGIN_BLOCK_MINUTES = 15
_TWO_FA_BLOCK_MINUTES = 10


class LoginBody(BaseModel):
    username: str
    password: str


class TwoFABody(BaseModel):
    code: str


def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "totp_enabled": user.totp_enabled,
        "permissions": sorted(get_effective_permissions(user)),
    }


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _client_ip(request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if client_host in {"127.0.0.1", "::1", "localhost"} and forwarded:
        forwarded_chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if forwarded_chain:
            return forwarded_chain[-1]
    return client_host


def _get_throttle(db: Session, scope: str) -> AuthThrottle | None:
    return db.query(AuthThrottle).filter(AuthThrottle.scope == scope).one_or_none()


def _ensure_not_blocked(db: Session, scope: str, message: str) -> None:
    throttle = _get_throttle(db, scope)
    now = _now_utc()
    if throttle is None:
        return
    if throttle.blocked_until and throttle.blocked_until > now:
        raise HTTPException(status_code=429, detail=message)
    if throttle.blocked_until and throttle.blocked_until <= now:
        db.delete(throttle)
        db.commit()


def _record_failure(db: Session, scope: str, *, limit: int, block_minutes: int) -> None:
    throttle = _get_throttle(db, scope)
    now = _now_utc()
    if throttle is None:
        throttle = AuthThrottle(scope=scope, failures=0)
        db.add(throttle)
    throttle.failures += 1
    throttle.last_failed_at = now
    if throttle.failures >= limit:
        throttle.blocked_until = now + timedelta(minutes=block_minutes)
    db.commit()


def _clear_throttle(db: Session, scope: str) -> None:
    throttle = _get_throttle(db, scope)
    if throttle is not None:
        db.delete(throttle)
        db.commit()


def _normalize_backup_code(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _use_backup_code(db: Session, user: User, code: str) -> bool:
    normalized = _normalize_backup_code(code)
    if not normalized:
        return False
    for backup_code in user.backup_codes:
        if backup_code.used_at is not None:
            continue
        if verify_password(backup_code.code_hash, normalized):
            backup_code.used_at = _now_utc()
            db.commit()
            return True
    return False


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    username = body.username.strip()
    throttle_scope = f"login:{username.lower()}:{_client_ip(request)}"
    _ensure_not_blocked(db, throttle_scope, "Too many failed login attempts. Please try again later.")

    user = authenticate_user(db, username, body.password)
    if user is None:
        _record_failure(db, throttle_scope, limit=_MAX_LOGIN_ATTEMPTS, block_minutes=_LOGIN_BLOCK_MINUTES)
        logger.warning("Failed login attempt for username: %s", username)
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    request.session.clear()  # prevent session fixation
    _clear_throttle(db, throttle_scope)

    if user.totp_enabled:
        request.session["pending_2fa_user_id"] = user.id
        logger.info("Login pending 2FA for user_id=%s", user.id)
        return {"needs_2fa": True}

    request.session["user_id"] = user.id
    logger.info("Successful login for user_id=%s", user.id)
    return {"user": _user_dict(user)}


@router.post("/2fa")
def verify_2fa(body: TwoFABody, request: Request, db: Session = Depends(get_db)):
    pending_id = request.session.get("pending_2fa_user_id")
    if not pending_id:
        raise HTTPException(status_code=400, detail="No pending 2FA login.")

    user = db.get(User, pending_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=401, detail="User not found or inactive.")

    if not user.totp_secret:
        request.session.clear()
        raise HTTPException(status_code=500, detail="2FA configuration error.")

    throttle_scope = f"2fa:{user.id}:{_client_ip(request)}"
    _ensure_not_blocked(db, throttle_scope, "Too many failed 2FA attempts. Please try again later.")

    pyotp = None
    try:
        import pyotp as _pyotp
        pyotp = _pyotp
    except ImportError:
        pyotp = None

    try:
        valid = pyotp is not None and pyotp.TOTP(user.totp_secret).verify(body.code, valid_window=1)
    except (ValueError, TypeError):
        logger.exception("2FA verification error for user %s", user.username)
        _record_failure(db, throttle_scope, limit=_MAX_2FA_ATTEMPTS, block_minutes=_TWO_FA_BLOCK_MINUTES)
        raise HTTPException(status_code=401, detail="Invalid 2FA code.")

    used_backup_code = False
    if not valid:
        used_backup_code = _use_backup_code(db, user, body.code)
        if not used_backup_code:
            _record_failure(db, throttle_scope, limit=_MAX_2FA_ATTEMPTS, block_minutes=_TWO_FA_BLOCK_MINUTES)
            raise HTTPException(status_code=401, detail="Invalid 2FA code.")

    request.session.clear()
    request.session["user_id"] = user.id
    _clear_throttle(db, throttle_scope)
    logger.info("Successful 2FA verification for user_id=%s backup_code=%s", user.id, used_backup_code)
    return {"user": _user_dict(user)}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"user": _user_dict(user)}
