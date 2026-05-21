from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

import pyotp

from config import settings
from cookies import _set_auth_cookies, _clear_auth_cookies
from database import get_db
from dependencies import get_current_user, get_current_owner, verify_csrf
from models import User
from schemas import LoginRequest, TokenResponse, PasswordResetRequest, PasswordResetConfirm
from schemas.user import UserCreate, UserResponse, OwnerSetupRequest
from services import AuthService, EmailService

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
    token = AuthService.generate_token()
    user.email_verification_token = token
    user.email_verification_expires = datetime.now(timezone.utc) + timedelta(hours=24)
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

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account deaktiviert")

    if user.two_factor_enabled:
        if not req.otp_code:
            return {"requires_2fa": True, "access_token": "", "token_type": ""}
        secret = None
        if user.two_factor_secret_encrypted:
            secret = AuthService.decrypt_2fa_secret(user.two_factor_secret_encrypted)
        elif user.two_factor_secret:
            secret = user.two_factor_secret
        if not secret:
            raise HTTPException(status_code=401, detail="2FA-Secret nicht gefunden")
        totp = pyotp.TOTP(secret)
        if not totp.verify(req.otp_code):
            raise HTTPException(status_code=401, detail="Ungueltiger 2FA-Code")

    access_token = AuthService.create_access_token({"sub": user.username, "user_id": user.id, "jti": str(uuid.uuid4())})
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
    refresh_cookie = request.cookies.get("__Secure-refresh_token")
    if refresh_cookie:
        rt = AuthService.validate_refresh_token(db, refresh_cookie)
        if rt:
            AuthService.revoke_refresh_token(db, rt)
    access_cookie = request.cookies.get("__Secure-access_token")
    if access_cookie:
        payload = AuthService.decode_token(access_cookie)
        if payload and "user_id" in payload:
            AuthService.revoke_all_user_refresh_tokens(db, payload["user_id"])
        if payload and payload.get("jti"):
            AuthService.blacklist_jwt(payload["jti"])
    _clear_auth_cookies(response)
    return {"message": "Abgemeldet"}


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Rotiert Access-Token und Refresh-Token."""
    refresh_cookie = request.cookies.get("__Secure-refresh_token")
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="Kein Refresh-Token")
    rt = AuthService.validate_refresh_token(db, refresh_cookie)
    if not rt:
        raise HTTPException(status_code=401, detail="Ungueltiges Refresh-Token")
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
        User.password_reset_expires > datetime.now(timezone.utc),
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
    expires = user.email_verification_expires
    now = datetime.now(timezone.utc)
    if expires is None:
        raise HTTPException(status_code=400, detail="Verifikationstoken abgelaufen")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        raise HTTPException(status_code=400, detail="Verifikationstoken abgelaufen")
    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_expires = None
    db.commit()
    return {"message": "E-Mail verifiziert"}


@router.post("/2fa/setup")
def setup_2fa(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    secret = pyotp.random_base32()
    user.two_factor_secret_encrypted = AuthService.encrypt_2fa_secret(secret)
    user.two_factor_enabled = False
    db.commit()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Maunting Server Manager")
    return {"secret": secret, "uri": uri}


@router.post("/2fa/enable")
def enable_2fa(otp_code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    secret = None
    if user.two_factor_secret_encrypted:
        secret = AuthService.decrypt_2fa_secret(user.two_factor_secret_encrypted)
    elif user.two_factor_secret:
        secret = user.two_factor_secret
    if not secret:
        raise HTTPException(status_code=400, detail="2FA nicht eingerichtet")
    totp = pyotp.TOTP(secret)
    if not totp.verify(otp_code):
        raise HTTPException(status_code=400, detail="Ungueltiger Code")
    user.two_factor_enabled = True
    db.commit()
    return {"message": "2FA aktiviert"}
