"""Account self-service API: password change and TOTP 2FA management."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..auth import hash_password, verify_password
from ..models import BackupCode, User
from ..permissions import get_effective_permissions
from .deps import get_current_user, get_db

router = APIRouter()
logger = logging.getLogger(__name__)

_MIN_PASSWORD_LEN = 8
_BACKUP_CODE_COUNT = 5
_BACKUP_CODE_SEGMENT_LENGTH = 4


def _import_pyotp():
    try:
        import pyotp
        return pyotp
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="2FA is not available. Install pyotp: pip install pyotp",
        )


def _generate_backup_codes() -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes: list[str] = []
    for _ in range(_BACKUP_CODE_COUNT):
        left = "".join(secrets.choice(alphabet) for _ in range(_BACKUP_CODE_SEGMENT_LENGTH))
        right = "".join(secrets.choice(alphabet) for _ in range(_BACKUP_CODE_SEGMENT_LENGTH))
        codes.append(f"{left}-{right}")
    return codes


def _normalize_backup_code(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _backup_code_count(user: User) -> int:
    return sum(1 for code in user.backup_codes if code.used_at is None)


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < _MIN_PASSWORD_LEN:
            raise ValueError(f"New password must be at least {_MIN_PASSWORD_LEN} characters.")
        return v


class Enable2FABody(BaseModel):
    secret: str
    code: str


class Disable2FABody(BaseModel):
    password: str
    code: str


@router.post("/account/change-password")
def change_password(
    body: ChangePasswordBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from current password.")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    logger.info("User %s changed their password.", user.username)
    return {"ok": True}


@router.get("/account/2fa/setup")
def setup_2fa(
    user: User = Depends(get_current_user),
) -> Any:
    if user.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="2FA is already enabled. Disable it first before setting up a new secret.",
        )
    pyotp = _import_pyotp()
    secret = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.username,
        issuer_name="Conan Exiles Panel",
    )
    return {
        "secret": secret,
        "uri": uri,
    }


@router.post("/account/2fa/enable")
def enable_2fa(
    body: Enable2FABody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    if user.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="2FA is already enabled. Disable it first before setting up a new secret.",
        )
    pyotp = _import_pyotp()
    totp = pyotp.TOTP(body.secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid verification code.")
    user.totp_secret = body.secret
    user.totp_enabled = True
    user.backup_codes_downloaded_at = None
    for backup_code in list(user.backup_codes):
        db.delete(backup_code)
    db.commit()
    logger.info("User %s enabled 2FA.", user.username)
    return {"ok": True}


@router.post("/account/2fa/backup-codes/download")
def download_backup_codes(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    if not user.totp_enabled:
        raise HTTPException(status_code=400, detail="Enable 2FA before generating backup codes.")
    if user.backup_codes_downloaded_at is not None:
        raise HTTPException(status_code=409, detail="Backup codes have already been downloaded.")

    codes = _generate_backup_codes()
    for backup_code in list(user.backup_codes):
        db.delete(backup_code)
    for code in codes:
        db.add(BackupCode(user_id=user.id, code_hash=hash_password(_normalize_backup_code(code))))
    user.backup_codes_downloaded_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    logger.info("User %s downloaded 2FA backup codes.", user.username)
    return {"codes": codes}


@router.post("/account/2fa/disable")
def disable_2fa(
    body: Disable2FABody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=400, detail="Incorrect password.")
    if not user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is not enabled.")
    pyotp = _import_pyotp()
    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid authenticator code.")
    user.totp_secret = None
    user.totp_enabled = False
    user.backup_codes_downloaded_at = None
    for backup_code in list(user.backup_codes):
        db.delete(backup_code)
    db.commit()
    logger.info("User %s disabled 2FA.", user.username)
    return {"ok": True}


@router.get("/account/me")
def get_me(user: User = Depends(get_current_user)) -> Any:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "permissions": sorted(get_effective_permissions(user)),
        "totp_enabled": user.totp_enabled,
        "backup_codes_downloaded": user.backup_codes_downloaded_at is not None,
        "backup_codes_remaining": _backup_code_count(user),
        "can_download_backup_codes": user.totp_enabled and user.backup_codes_downloaded_at is None,
        "is_active": user.is_active,
    }
