from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

import pyotp

from config import settings
from cookies import _set_auth_cookies, _clear_auth_cookies
from database import get_db
from dependencies import get_current_user, get_current_owner, verify_csrf
from models import User, EmailVerification
from schemas import LoginRequest, TokenResponse, PasswordResetRequest, PasswordResetConfirm, ChangePasswordRequest, ChangeEmailRequest
from schemas import ResendVerificationRequest
from schemas.user import UserCreate, UserResponse, OwnerSetupRequest, SetupVerifyRequest
from services import AuthService, EmailService
from services.email_verification_service import EmailVerificationService
from services.jwt_blacklist_service import blacklist_jwt
from services.backup_code_service import BackupCodeService
from services.permission_catalog import SYSTEM_ROLE_USER
from services.role_service import get_role_by_name

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    return {"setup_required": not AuthService.is_owner_exists(db)}


@router.post("/setup", status_code=201)
async def setup_owner(req: OwnerSetupRequest, db: Session = Depends(get_db)) -> dict:
    if AuthService.is_owner_exists(db):
        raise HTTPException(status_code=400, detail="Setup bereits abgeschlossen")
    if AuthService.get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Username bereits vergeben")
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")

    # Owner erstellen (noch nicht verifiziert)
    user = AuthService.create_owner(db, req.username, req.email, req.password)
    user.email_verified = False
    db.commit()

    # Verifikations-Code generieren und per Email senden
    code = EmailVerificationService.create_verification(db, req.email, "setup")
    if EmailService.is_configured():
        await EmailService.send_verification_code_email(req.email, req.username, code)
    else:
        import logging
        logging.warning("SMTP nicht konfiguriert. Verifikations-Code fuer %s: %s", req.email, code)
        # Setup-User und Verifikationseintrag wieder entfernen
        db.query(EmailVerification).filter(EmailVerification.email == req.email).delete()
        db.delete(user)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="SMTP nicht konfiguriert. Verifikation nicht moeglich."
        )

    return {"message": "Verifikations-Code gesendet", "requires_verification": True}


@router.post("/setup-verify")
def setup_verify(req: SetupVerifyRequest, db: Session = Depends(get_db)) -> dict:
    user = AuthService.get_user_by_email(db, req.email)
    if not user:
        raise HTTPException(status_code=400, detail="Ungueltige E-Mail")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Bereits verifiziert")

    valid = EmailVerificationService.verify_code(db, req.email, "setup", req.code)
    if not valid:
        raise HTTPException(status_code=400, detail="Ungueltiger oder abgelaufener Code")

    user.email_verified = True
    db.commit()
    return {"message": "E-Mail verifiziert", "setup_completed": True}


@router.post("/setup-resend")
async def setup_resend(req: OwnerSetupRequest, db: Session = Depends(get_db)) -> dict:
    user = AuthService.get_user_by_email(db, req.email)
    if not user or user.email_verified:
        raise HTTPException(status_code=400, detail="Ungueltige Anfrage")

    code = EmailVerificationService.create_verification(db, req.email, "setup")
    if EmailService.is_configured():
        await EmailService.send_verification_code_email(req.email, user.username, code)
    else:
        import logging
        logging.warning("SMTP nicht konfiguriert. Verifikations-Code fuer %s: %s", req.email, code)
        raise HTTPException(
            status_code=503,
            detail="SMTP nicht konfiguriert. Verifikation nicht moeglich."
        )

    return {"message": "Code erneut gesendet"}


@router.post("/resend-verification")
async def resend_verification(req: ResendVerificationRequest, db: Session = Depends(get_db)) -> dict:
    """Neuen Verifizierungscode fuer einen unverifizierten User senden."""
    user = AuthService.get_user_by_email(db, req.email)
    if not user or user.email_verified:
        raise HTTPException(status_code=400, detail="Ungueltige Anfrage")

    code = EmailVerificationService.create_verification(db, req.email, "setup")
    if EmailService.is_configured():
        await EmailService.send_verification_code_email(req.email, user.username, code)
    else:
        import logging
        logging.warning("SMTP nicht konfiguriert. Verifikations-Code fuer %s: %s", req.email, code)
        raise HTTPException(
            status_code=503,
            detail="SMTP nicht konfiguriert. Verifikation nicht moeglich."
        )

    return {"message": "Code erneut gesendet"}


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(req: UserCreate, db: Session = Depends(get_db)) -> User:
    if AuthService.get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Username bereits vergeben")
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")
    user = AuthService.create_user(db, req.username, req.email, req.password)
    # Sicherer Default: System-Rolle `user`. Konsistent mit der Lifespan-
    # Migration und dem Admin-Create-Pfad. Verhindert Accounts mit role_id=NULL.
    default_role = get_role_by_name(db, SYSTEM_ROLE_USER)
    if default_role is not None:
        user.role_id = default_role.id
    token = AuthService.generate_token()
    user.email_verification_token = token
    user.email_verification_expires = datetime.now(timezone.utc) + timedelta(hours=24)
    db.commit()
    await EmailService.send_verification_email(user.email, user.username, token)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    user = AuthService.get_user_by_username(db, req.username)
    if not user or not AuthService.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Ungueltige Anmeldedaten")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account deaktiviert")

    if not user.email_verified:
        # Neuen Verifizierungscode generieren und senden
        code = EmailVerificationService.create_verification(db, user.email, "setup")
        if EmailService.is_configured():
            await EmailService.send_verification_code_email(user.email, user.username, code)
        else:
            import logging
            logging.warning("SMTP nicht konfiguriert. Verifikations-Code fuer %s: %s", user.email, code)
        return {"access_token": "", "token_type": "", "requires_2fa": False, "requires_verification": True, "email": user.email}

    if user.two_factor_enabled:
        if not req.otp_code:
            return {"requires_2fa": True, "access_token": "", "token_type": "", "requires_verification": False, "email": user.email}
        secret = None
        if user.two_factor_secret_encrypted:
            secret = AuthService.decrypt_2fa_secret(user.two_factor_secret_encrypted)
        if not secret:
            raise HTTPException(status_code=401, detail="2FA-Secret nicht gefunden")
        totp = pyotp.TOTP(secret)
        if not totp.verify(req.otp_code):
            # Backup-Code als Fallback pruefen
            backup_valid = BackupCodeService.validate_backup_code(db, user.id, req.otp_code)
            if not backup_valid:
                raise HTTPException(status_code=401, detail="Ungueltiger 2FA-Code oder Backup-Code")

    access_token = AuthService.create_access_token({"sub": user.username, "user_id": user.id, "jti": str(uuid.uuid4())})
    refresh_token = AuthService.create_refresh_token(db, user.id)
    csrf_token = AuthService.create_csrf_token()

    _set_auth_cookies(response, access_token, refresh_token, csrf_token)

    # Sicherheitsbenachrichtigung bei Login
    if EmailService.is_configured() and user.email_notifications:
        client_ip = request.client.host if request.client else "unbekannt"
        user_agent = request.headers.get("user-agent", "unbekannt")
        await EmailService.send_new_device_login_notification(user.email, user.username, client_ip, user_agent)

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
    user_id_to_revoke: int | None = None
    if access_cookie:
        payload = AuthService.decode_token(access_cookie)
        if payload:
            user_id_to_revoke = payload.get("user_id")
            if payload.get("jti"):
                expires = payload.get("exp")
                from datetime import datetime
                expires_dt = datetime.fromtimestamp(expires, tz=timezone.utc) if expires else None
                blacklist_jwt(db, payload["jti"], user_id_to_revoke, expires_dt)

    # Wenn Access-Token abgelaufen/ungueltig ist, versuche den User ueber den
    # Refresh-Token zu identifizieren, damit alle Sessions beendet werden.
    if user_id_to_revoke is None and refresh_cookie:
        rt_fallback = AuthService.validate_refresh_token(db, refresh_cookie)
        if rt_fallback:
            user_id_to_revoke = rt_fallback.user_id

    if user_id_to_revoke is not None:
        AuthService.revoke_all_user_refresh_tokens(db, user_id_to_revoke)

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


@router.patch("/me/notifications")
def update_notifications(
    enabled: bool,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    user.email_notifications = enabled
    db.commit()
    return {"email_notifications": user.email_notifications}


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Eigenes Passwort aendern. Erfordert aktuelles Passwort + 2FA-Code wenn 2FA aktiv."""
    if not AuthService.verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Aktuelles Passwort falsch")

    if user.two_factor_enabled:
        if not req.otp_code:
            raise HTTPException(status_code=401, detail="2FA-Code erforderlich")
        secret = AuthService.decrypt_2fa_secret(user.two_factor_secret_encrypted) if user.two_factor_secret_encrypted else None
        if not secret:
            raise HTTPException(status_code=401, detail="2FA-Secret nicht gefunden")
        totp = pyotp.TOTP(secret)
        if not totp.verify(req.otp_code):
            raise HTTPException(status_code=401, detail="Ungueltiger 2FA-Code")

    AuthService.reset_password(db, user, req.new_password)
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_password_changed_notification(user.email, user.username)
    return {"message": "Passwort geaendert"}


@router.post("/change-email")
async def change_email(
    req: ChangeEmailRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """E-Mail-Adresse aendern. Erfordert 2FA-Code wenn 2FA aktiv."""
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")

    if user.two_factor_enabled:
        if not req.otp_code:
            raise HTTPException(status_code=401, detail="2FA-Code erforderlich")
        secret = AuthService.decrypt_2fa_secret(user.two_factor_secret_encrypted) if user.two_factor_secret_encrypted else None
        if not secret:
            raise HTTPException(status_code=401, detail="2FA-Secret nicht gefunden")
        totp = pyotp.TOTP(secret)
        if not totp.verify(req.otp_code):
            raise HTTPException(status_code=401, detail="Ungueltiger 2FA-Code")

    user.email = req.email
    user.email_verified = False
    db.commit()
    # Verifizierungscode fuer neue E-Mail senden
    if EmailService.is_configured():
        code = EmailVerificationService.create_verification(db, req.email, "setup")
        await EmailService.send_verification_code_email(req.email, user.username, code)
    return {"message": "E-Mail geaendert. Bitte neue E-Mail verifizieren."}


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
async def enable_2fa(otp_code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if not user.two_factor_secret_encrypted:
        raise HTTPException(status_code=400, detail="2FA nicht eingerichtet")
    secret = AuthService.decrypt_2fa_secret(user.two_factor_secret_encrypted)
    totp = pyotp.TOTP(secret)
    if not totp.verify(otp_code):
        raise HTTPException(status_code=400, detail="Ungueltiger Code")
    user.two_factor_enabled = True
    db.commit()
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_2fa_status_notification(user.email, user.username, enabled=True)
    return {"message": "2FA aktiviert"}


@router.post("/2fa/disable")
async def disable_2fa(
    otp_code: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """2FA deaktivieren — ERFORDERT aktuellen 2FA-Code. Backup-Codes funktionieren NICHT."""
    if not user.two_factor_enabled or not user.two_factor_secret_encrypted:
        raise HTTPException(status_code=400, detail="2FA nicht aktiviert")
    secret = AuthService.decrypt_2fa_secret(user.two_factor_secret_encrypted)
    totp = pyotp.TOTP(secret)
    if not totp.verify(otp_code):
        raise HTTPException(status_code=400, detail="Ungueltiger 2FA-Code")
    user.two_factor_enabled = False
    user.two_factor_secret_encrypted = None
    BackupCodeService.clear_all_backup_codes(db, user.id)
    db.commit()
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_2fa_status_notification(user.email, user.username, enabled=False)
    return {"message": "2FA deaktiviert"}


@router.post("/2fa/backup/generate")
def generate_backup_codes(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    if not user.two_factor_enabled:
        raise HTTPException(status_code=400, detail="2FA muss aktiviert sein")
    codes = BackupCodeService.generate_backup_codes(db, user.id)
    return {"codes": codes, "count": len(codes)}
