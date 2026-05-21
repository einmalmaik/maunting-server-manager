from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

import pyotp

from config import settings
from database import get_db
from models import User
from schemas import LoginRequest, TokenResponse, PasswordResetRequest, PasswordResetConfirm
from schemas.user import UserCreate, UserResponse, OwnerSetupRequest
from services import AuthService, EmailService

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Cookie-Konfiguration ──
_COOKIE_CONFIG = {
    "access_token": {
        "httponly": True,
        "secure": not settings.debug,
        "samesite": "strict",
        "path": "/api",
    },
    "refresh_token": {
        "httponly": True,
        "secure": not settings.debug,
        "samesite": "strict",
        "path": "/api/auth",
    },
    "csrf_token": {
        "httponly": False,  # JS muss lesen koennen fuer Double-Submit
        "secure": not settings.debug,
        "samesite": "strict",
        "path": "/api",
    },
}


def _set_cookie(response: Response, key: str, value: str, max_age: int | None = None) -> None:
    cfg = _COOKIE_CONFIG[key]
    response.set_cookie(
        key=key,
        value=value,
        httponly=cfg["httponly"],
        secure=cfg["secure"],
        samesite=cfg["samesite"],
        path=cfg["path"],
        max_age=max_age,
    )


def _clear_auth_cookies(response: Response) -> None:
    for key in ("access_token", "refresh_token", "csrf_token"):
        response.delete_cookie(key=key, path=_COOKIE_CONFIG[key]["path"])


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str, csrf_token: str) -> None:
    _set_cookie(response, "access_token", access_token, max_age=settings.access_token_expire_minutes * 60)
    _set_cookie(response, "refresh_token", refresh_token, max_age=settings.refresh_token_expire_days * 24 * 60 * 60)
    _set_cookie(response, "csrf_token", csrf_token, max_age=settings.csrf_token_expire_minutes * 60)


# ── Dependencies ──
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert")
    payload = AuthService.decode_token(token)
    if not payload or "sub" not in payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Ungueltiges Token")
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
    csrf_cookie = request.cookies.get("csrf_token")
    csrf_header = request.headers.get("x-csrf-token")
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(status_code=403, detail="CSRF-Token ungueltig")


# ── Routes ──
@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    return {"setup_required": not AuthService.is_owner_exists(db)}


@router.post("/setup", status_code=201)
def setup_owner(req: OwnerSetupRequest, db: Session = Depends(get_db)) -> dict:
    if AuthService.is_owner_exists(db):
        raise HTTPException(status_code=400, detail="Setup bereits abgeschlossen")

    if AuthService.get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Username bereits vergeben")
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")

    AuthService.create_owner(db, req.username, req.email, req.password)
    return {"message": "Owner erstellt"}


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(req: UserCreate, db: Session = Depends(get_db)) -> User:
    if AuthService.get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Username bereits vergeben")
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")

    user = AuthService.create_user(db, req.username, req.email, req.password)

    # Email verification
    token = AuthService.generate_token()
    user.email_verification_token = token
    db.commit()

    await EmailService.send_verification_email(user.email, user.username, token)
    return user


@router.post("/login", response_model=TokenResponse)
def login(
    req: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    user = AuthService.get_user_by_username(db, req.username)
    if not user or not AuthService.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Ungueltige Anmeldedaten")

    if user.two_factor_enabled:
        if not req.otp_code:
            return {"requires_2fa": True, "access_token": "", "token_type": ""}
        totp = pyotp.TOTP(user.two_factor_secret)
        if not totp.verify(req.otp_code):
            raise HTTPException(status_code=401, detail="Ungueltiger 2FA-Code")

    # Sichere Cookies setzen
    access_token = AuthService.create_access_token({"sub": user.username, "user_id": user.id})
    refresh_token = AuthService.create_refresh_token(db, user.id)
    csrf_token = AuthService.create_csrf_token()

    _set_auth_cookies(response, access_token, refresh_token, csrf_token)

    return {"access_token": "", "token_type": "bearer", "requires_2fa": False}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Serverseitiges Logout: Refresh-Token revozieren, Cookies loeschen."""
    refresh_cookie = request.cookies.get("refresh_token")
    if refresh_cookie:
        rt = AuthService.validate_refresh_token(db, refresh_cookie)
        if rt:
            AuthService.revoke_refresh_token(db, rt)

    # Auch Access-Token payload pruefen und ggf. alle Tokens des Users revozieren
    access_cookie = request.cookies.get("access_token")
    if access_cookie:
        payload = AuthService.decode_token(access_cookie)
        if payload and "user_id" in payload:
            AuthService.revoke_all_user_refresh_tokens(db, payload["user_id"])

    _clear_auth_cookies(response)
    return {"message": "Abgemeldet"}


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Rotiert Access-Token und Refresh-Token."""
    refresh_cookie = request.cookies.get("refresh_token")
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="Kein Refresh-Token")

    rt = AuthService.validate_refresh_token(db, refresh_cookie)
    if not rt:
        raise HTTPException(status_code=401, detail="Ungueltiges Refresh-Token")

    # Token-Rotation: Altes markieren, neues in gleicher Family erstellen
    family = rt.family
    AuthService.mark_refresh_token_used(db, rt)

    user = AuthService.get_user_by_id(db, rt.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User nicht gefunden oder inaktiv")

    access_token = AuthService.create_access_token({"sub": user.username, "user_id": user.id})
    new_refresh = AuthService.create_refresh_token(db, user.id, family=family)
    csrf_token = AuthService.create_csrf_token()

    _set_auth_cookies(response, access_token, new_refresh, csrf_token)
    return {"message": "Token refreshed"}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/forgot-password")
async def forgot_password(req: PasswordResetRequest, db: Session = Depends(get_db)) -> dict:
    user = AuthService.get_user_by_email(db, req.email)
    if not user:
        # Security: Gleiche Antwort, damit nicht gescannt werden kann
        return {"message": "Falls die E-Mail existiert, wurde eine Nachricht gesendet"}

    token = AuthService.set_password_reset_token(db, user)
    await EmailService.send_password_reset_email(user.email, user.username, token)
    return {"message": "Falls die E-Mail existiert, wurde eine Nachricht gesendet"}


@router.post("/reset-password")
def reset_password(
    req: PasswordResetConfirm,
    db: Session = Depends(get_db),
) -> dict:
    user = db.query(User).filter(
        User.password_reset_token == req.token,
        User.password_reset_expires > datetime.now(timezone.utc)
    ).first()

    if not user:
        raise HTTPException(status_code=400, detail="Ungueltiger oder abgelaufener Token")

    AuthService.reset_password(db, user, req.new_password)
    return {"message": "Passwort zurueckgesetzt"}


@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter(User.email_verification_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Ungueltiger Token")
    user.email_verified = True
    user.email_verification_token = None
    db.commit()
    return {"message": "E-Mail verifiziert"}


@router.post("/2fa/setup")
def setup_2fa(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    secret = pyotp.random_base32()
    user.two_factor_secret = secret
    user.two_factor_enabled = False
    db.commit()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Maunting Server Manager")
    return {"secret": secret, "uri": uri}


@router.post("/2fa/enable")
def enable_2fa(otp_code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if not user.two_factor_secret:
        raise HTTPException(status_code=400, detail="2FA nicht eingerichtet")
    totp = pyotp.TOTP(user.two_factor_secret)
    if not totp.verify(otp_code):
        raise HTTPException(status_code=400, detail="Ungueltiger Code")
    user.two_factor_enabled = True
    db.commit()
    return {"message": "2FA aktiviert"}
