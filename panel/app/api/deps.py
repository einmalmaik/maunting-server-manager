from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_user_by_id
from ..config import get_settings
from ..database import SessionLocal
from ..models import User


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = get_user_by_id(db, user_id) if user_id else None
    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _server_exists(server_name: str | None) -> bool:
    if not server_name:
        return False

    from ..shell import get_server_dir

    return get_server_dir(server_name).is_dir()


def get_current_server(request: Request) -> str | None:
    """Return the active server name, or None if no valid server is available."""
    selected = request.session.get("current_server")
    if selected:
        if _server_exists(selected):
            return selected
        request.session.pop("current_server", None)
        return None

    default = get_settings().default_server_name
    if _server_exists(default):
        return default
    return None


def require_server(server: str | None = Depends(get_current_server)) -> str:
    """Dependency that raises HTTP 400 when no server is active."""
    if server is None:
        raise HTTPException(
            status_code=400,
            detail="No server selected. Please create or select a server first.",
        )
    return server
