from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import User, Permission
from services.auth_service import AuthService
from services.jwt_blacklist_service import is_jwt_blacklisted


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("__Secure-access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert")
    payload = AuthService.decode_token(token)
    if not payload or "sub" not in payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Ungueltiges Token")
    jti = payload.get("jti")
    if jti and is_jwt_blacklisted(db, jti):
        raise HTTPException(status_code=401, detail="Token widerrufen")
    user = AuthService.get_user_by_username(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User nicht gefunden oder inaktiv")
    return user


def get_current_owner(user: User = Depends(get_current_user)) -> User:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner erlaubt")
    return user


def _all_cookie_values(request: Request, name: str) -> list[str]:
    """Liefert alle Werte unter `name` aus dem Cookie-Header.

    Starlette gibt ueber `request.cookies` nur einen Wert pro Name zurueck. Wenn
    ein Browser nach einer Pfad-Migration noch ein zweites Cookie mit demselben
    Namen unter einem anderen Pfad mitschickt (z. B. Path=/api aus einem
    frueheren Release zusaetzlich zu Path=/), geht der jeweils andere Wert
    verloren. Fuer die CSRF-Pruefung wollen wir alle Werte sehen.
    """
    raw = request.headers.get("cookie", "")
    values: list[str] = []
    for chunk in raw.split(";"):
        if "=" not in chunk:
            continue
        key, val = chunk.split("=", 1)
        if key.strip() == name:
            values.append(val.strip())
    return values


def verify_csrf(request: Request) -> None:
    """Double-Submit-Cookie CSRF-Schutz. Nur fuer state-changing Requests.

    Akzeptiert den Header-Wert, wenn er zu einem der vom Browser gesendeten
    CSRF-Cookies passt. Das ist noetig, weil nach einer Cookie-Pfad-Migration
    Browser zeitweise zwei Cookies mit demselben Namen unter verschiedenen
    Pfaden halten koennen — und Angreifer in beiden Faellen den Header-Wert
    nicht raten koennen (cross-origin kein Cookie-Zugriff).
    """
    csrf_header = request.headers.get("x-csrf-token")
    if not csrf_header:
        raise HTTPException(status_code=403, detail="CSRF-Token ungueltig")
    cookie_values = _all_cookie_values(request, "__Secure-csrf_token")
    if not cookie_values or csrf_header not in cookie_values:
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
