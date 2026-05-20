from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

import pyotp

from database import get_db
from models import User
from schemas import LoginRequest, TokenResponse, PasswordResetRequest, PasswordResetConfirm
from schemas.user import UserCreate, UserResponse, OwnerSetupRequest
from services import AuthService, EmailService

router = APIRouter(prefix="/api/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = AuthService.decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Ungültiges Token")
    user = AuthService.get_user_by_username(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User nicht gefunden oder inaktiv")
    return user


def get_current_owner(user: User = Depends(get_current_user)) -> User:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner erlaubt")
    return user


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
def login(req: LoginRequest, db: Session = Depends(get_db)) -> dict:
    user = AuthService.get_user_by_username(db, req.username)
    if not user or not AuthService.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Ungültige Anmeldedaten")

    if user.two_factor_enabled:
        if not req.otp_code:
            return {"requires_2fa": True, "access_token": "", "token_type": ""}
        totp = pyotp.TOTP(user.two_factor_secret)
        if not totp.verify(req.otp_code):
            raise HTTPException(status_code=401, detail="Ungültiger 2FA-Code")

    token = AuthService.create_access_token({"sub": user.username, "user_id": user.id})
    return {"access_token": token, "token_type": "bearer"}


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
def reset_password(req: PasswordResetConfirm, db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter(
        User.password_reset_token == req.token,
        User.password_reset_expires > datetime.now(timezone.utc)
    ).first()

    if not user:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Token")

    AuthService.reset_password(db, user, req.new_password)
    return {"message": "Passwort zurückgesetzt"}


@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter(User.email_verification_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Ungültiger Token")
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
        raise HTTPException(status_code=400, detail="Ungültiger Code")
    user.two_factor_enabled = True
    db.commit()
    return {"message": "2FA aktiviert"}
