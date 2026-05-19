from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import hash_password
from ..models import PanelSetting, User
from .deps import get_db

router = APIRouter()

_MIN_PASSWORD_LEN = 8
_MAX_USERNAME_LEN = 64
_SETUP_OWNER_LOCK_KEY = "setup.owner.created"


class SetupBody(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Username must not be empty.")
        if len(v) > _MAX_USERNAME_LEN:
            raise ValueError(f"Username must be {_MAX_USERNAME_LEN} characters or fewer.")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < _MIN_PASSWORD_LEN:
            raise ValueError(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
        return v


def _user_dict(user: User) -> dict:
    return {"id": user.id, "username": user.username}


@router.get("/status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    """Return whether initial setup is required (no users in DB)."""
    if db.scalar(select(PanelSetting).where(PanelSetting.key == _SETUP_OWNER_LOCK_KEY)) is not None:
        return {"needs_setup": False}
    count = db.scalar(select(func.count()).select_from(User)) or 0
    return {"needs_setup": count == 0}


@router.post("/create-owner")
def create_owner(body: SetupBody, request: Request, db: Session = Depends(get_db)) -> dict:
    """Create the initial owner account. Only works when no users exist."""
    count = db.scalar(select(func.count()).select_from(User)) or 0
    if count > 0:
        raise HTTPException(status_code=403, detail="Setup already completed.")
    existing_lock = db.scalar(select(PanelSetting).where(PanelSetting.key == _SETUP_OWNER_LOCK_KEY))
    if existing_lock is not None:
        raise HTTPException(status_code=403, detail="Setup already completed.")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role="owner",
        is_active=True,
    )
    setup_lock = PanelSetting(key=_SETUP_OWNER_LOCK_KEY, value=body.username)
    db.add(user)
    db.add(setup_lock)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail="Setup already completed.") from exc
    db.refresh(user)

    # Auto-login: set session so the user lands directly on the dashboard
    request.session.clear()
    request.session["user_id"] = user.id

    return {"user": _user_dict(user)}
