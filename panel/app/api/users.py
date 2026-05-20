"""User management API — owner/admin only."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import hash_password
from ..config import get_settings
from ..email_service import (
    compute_expires_at,
    generate_verification_token,
    send_verification_email,
    send_welcome_email,
)
from ..models import User
from ..notifications import notify_account_created, notify_password_reset
from ..permissions import (
    ALL_PERMISSIONS,
    P_USERS_MANAGE,
    P_USERS_VIEW,
    require_perm,
)
from .deps import get_db

router = APIRouter()
logger = logging.getLogger(__name__)

_MIN_PASSWORD_LEN = 8
_MAX_USERNAME_LEN = 64


# ── Helpers ────────────────────────────────────────────────────────────────────

def _user_dict(user: User) -> dict[str, Any]:
    perms: list[str] = []
    if user.permissions:
        try:
            perms = json.loads(user.permissions)
        except (json.JSONDecodeError, TypeError):
            perms = []
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "permissions": perms,
        "is_active": user.is_active,
        "totp_enabled": user.totp_enabled,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _require_admin_or_owner(current_user: User) -> None:
    if current_user.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Permission denied.")


def _require_owner(current_user: User) -> None:
    if current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can perform this action.")


# ── Schema ─────────────────────────────────────────────────────────────────────

class CreateUserBody(BaseModel):
    username: str
    email: str | None = None
    password: str
    role: str = "user"
    permissions: list[str] | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > _MAX_USERNAME_LEN:
            raise ValueError(f"Username must be 1–{_MAX_USERNAME_LEN} characters.")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < _MIN_PASSWORD_LEN:
            raise ValueError(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email address.")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("Role must be 'admin' or 'user'.")
        return v

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        unknown = set(v) - set(ALL_PERMISSIONS.keys())
        if unknown:
            raise ValueError(f"Unknown permissions: {', '.join(sorted(unknown))}")
        return v


class UpdateUserBody(BaseModel):
    email: str | None = None
    role: str | None = None
    permissions: list[str] | None = None
    is_active: bool | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email address.")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in ("admin", "user"):
            raise ValueError("Role must be 'admin' or 'user'.")
        return v

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        unknown = set(v) - set(ALL_PERMISSIONS.keys())
        if unknown:
            raise ValueError(f"Unknown permissions: {', '.join(sorted(unknown))}")
        return v


class ResetPasswordBody(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < _MIN_PASSWORD_LEN:
            raise ValueError(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
        return v


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(
    current_user: User = require_perm(P_USERS_VIEW),
    db: Session = Depends(get_db),
) -> Any:
    users = db.scalars(select(User).order_by(User.id)).all()
    return {"users": [_user_dict(u) for u in users]}


@router.get("/users/permissions")
def list_permissions(
    current_user: User = require_perm(P_USERS_VIEW),
) -> Any:
    return {"permissions": [{"key": k, "label": v} for k, v in ALL_PERMISSIONS.items()]}


@router.post("/users")
def create_user(
    body: CreateUserBody,
    current_user: User = require_perm(P_USERS_MANAGE),
    db: Session = Depends(get_db),
) -> Any:
    _require_admin_or_owner(current_user)

    # Only owner may create admin accounts
    if body.role == "admin" and current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can create admin accounts.")
    if body.permissions is not None and current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can set custom permissions.")

    existing = db.scalar(select(User).where(User.username == body.username))
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken.")

    if body.email:
        existing_email = db.scalar(select(User).where(User.email == body.email))
        if existing_email:
            raise HTTPException(status_code=409, detail="Email already in use.")

    stored_permissions = body.permissions
    if body.role == "admin":
        stored_permissions = None

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        permissions=json.dumps(stored_permissions) if stored_permissions is not None else None,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if user.email:
        token = generate_verification_token()
        user.verification_token = token
        user.verification_expires_at = compute_expires_at("verification")
        db.commit()
        settings = get_settings()
        base_url = settings.root_path if settings.root_path != "/" else ""
        panel_url = f"{settings.bind_host}:{settings.bind_port}"
        full_url = f"http://{panel_url}{base_url}"
        sent = send_verification_email(user.email, user.username, token, full_url)
        if not sent:
            send_welcome_email(user.email, user.username, full_url)
    else:
        notify_account_created(None, user.username)

    logger.info("User %s created by %s", user.username, current_user.username)
    return {"user": _user_dict(user)}


@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    current_user: User = require_perm(P_USERS_VIEW),
    db: Session = Depends(get_db),
) -> Any:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"user": _user_dict(user)}


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUserBody,
    current_user: User = require_perm(P_USERS_MANAGE),
    db: Session = Depends(get_db),
) -> Any:
    _require_admin_or_owner(current_user)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot modify the owner account.")

    # Only owner can change role or grant admin
    if body.role is not None and current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can change roles.")
    if body.permissions is not None and current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can change permissions.")

    if body.email is not None:
        if body.email:
            existing = db.scalar(select(User).where(User.email == body.email, User.id != user_id))
            if existing:
                raise HTTPException(status_code=409, detail="Email already in use.")
        user.email = body.email or None
    if body.role is not None:
        user.role = body.role
        if body.role == "admin":
            user.permissions = None
    if body.permissions is not None and user.role != "admin":
        user.permissions = json.dumps(body.permissions)
    if body.is_active is not None:
        user.is_active = body.is_active

    db.commit()
    db.refresh(user)
    return {"user": _user_dict(user)}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: User = require_perm(P_USERS_MANAGE),
    db: Session = Depends(get_db),
) -> Any:
    _require_owner(current_user)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account.")
    if user.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot delete the owner account.")
    db.delete(user)
    db.commit()
    logger.info("User %s deleted by %s", user.username, current_user.username)
    return {"ok": True}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    body: ResetPasswordBody,
    current_user: User = require_perm(P_USERS_MANAGE),
    db: Session = Depends(get_db),
) -> Any:
    _require_admin_or_owner(current_user)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.role == "owner" and current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can reset the owner password.")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    notify_password_reset(user.email, user.username)
    logger.info("Password reset for user %s by %s", user.username, current_user.username)
    return {"ok": True}
