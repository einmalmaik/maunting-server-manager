from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import User, Permission
from services.auth_service import AuthService


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("__Secure-access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert")
    payload = AuthService.decode_token(token)
    if not payload or "sub" not in payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Ungueltiges Token")
    jti = payload.get("jti")
    if jti and AuthService.is_jwt_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Token widerrufen")
    user = AuthService.get_user_by_username(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User nicht gefunden oder inaktiv")
    return user


def get_current_owner(user: User = Depends(get_current_user)) -> User:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner erlaubt")
    return user


def verify_csrf(request: Request) -> None:
    """Double-Submit-Cookie CSRF-Schutz. Nur fuer state-changing Requests."""
    csrf_cookie = request.cookies.get("__Secure-csrf_token")
    csrf_header = request.headers.get("x-csrf-token")
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(status_code=403, detail="CSRF-Token ungueltig")


def require_server_permission(user: User, server_id: int, db: Session, action: str | None = None) -> None:
    """Prueft, ob ein User Berechtigung fuer einen Server hat."""
    if user.is_owner:
        return
    perm = db.query(Permission).filter(
        Permission.user_id == user.id,
        Permission.server_id == server_id,
    ).first()
    if not perm:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if action is not None and not getattr(perm, action, False):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
