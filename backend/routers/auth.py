import os
import re
from datetime import datetime, timedelta, timezone
import uuid
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config import settings
from middleware.rate_limit import auth_rate_limit
# Nur noch das Loeschen der Cookies passiert hier direkt. Das Setzen laeuft
# ausnahmslos ueber `issue_session`, damit kein Ausstellungsort die dort
# zugesicherte `jti` erneut vergessen kann.
from cookies import _clear_auth_cookies
from database import get_db
from dependencies import (
    get_current_user,
    get_current_owner,
    require_global,
    verify_csrf,
    _bearer_token,
    session_familie,
)
from models import User, EmailVerification
from services.dis_client import DisClient
from schemas import LoginRequest, LoginVerifyRequest, TokenResponse, RegistrationResponse, PasswordResetRequest, PasswordResetConfirm, ChangePasswordRequest, ChangeEmailRequest, DeleteAccountRequest, NativeRefreshRequest, LogoutRequest
from schemas import ResendVerificationRequest
from schemas.user import UserCreate, UserResponse, OwnerSetupRequest, SetupVerifyRequest, TimezoneUpdateRequest, LocationSharingUpdateRequest, AgentNameUpdateRequest, AiProviderChoiceRequest
from schemas.device_pairing import (
    PairedDevice,
    PairingCreated,
    PairingCreateRequest,
    PairingRedeemRequest,
)
from services import AuthService, EmailService, audit_service
from services import device_pairing_service
from services.email_verification_service import EmailVerificationService
from services.jwt_blacklist_service import blacklist_jwt
from services.backup_code_service import BackupCodeService
from services.permission_catalog import SYSTEM_ROLE_USER
from services.role_service import get_role_by_name, set_user_roles
from services.panel_settings_service import PanelSettingsService
from services.session_service import issue_session, SessionTokens
from services.totp_qr import qr_datenuri

from services.captcha_service import CaptchaService

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/captcha-config")
def get_captcha_config() -> dict:
    """Oeffentliche CAPTCHA-Konfiguration fuer das Frontend."""
    enabled = PanelSettingsService.get("captcha_enabled", "false") == "true"
    provider = PanelSettingsService.get("captcha_provider", "none")
    site_key = PanelSettingsService.get("captcha_site_key", "")
    return {
        "enabled": enabled,
        "provider": provider,
        "site_key": site_key,
    }


logger = logging.getLogger(__name__)

REGISTER_VERIFICATION_PURPOSE = "register"
LOGIN_VERIFICATION_PURPOSE = "login"
LOGIN_ACCEPTED_VERIFICATION_PURPOSES = [REGISTER_VERIFICATION_PURPOSE, LOGIN_VERIFICATION_PURPOSE]


def _log_smtp_missing(email: str) -> None:
    logger.warning("SMTP nicht konfiguriert. Verifikations-Code fuer %s nicht versendet.", email)


def _set_login_session(response: Response, db: Session, user: User) -> SessionTokens:
    """Duennes Alias auf die gemeinsame Sitzungsausstellung (siehe session_service)."""
    return issue_session(response, db, user)


def _native_token_body(tokens: SessionTokens) -> dict:
    """Antwort-Body für native Clients: die Tokens selbst statt Cookies.

    Der Browser-Flow bleibt cookie-only — dieser Body geht nur hinaus, wenn der
    Request ausdrücklich `native_client=True` trug (oder das Refresh-Token im
    Body kam). Kein csrf_token: native Clients authentifizieren per Bearer und
    sind vom Cookie-CSRF befreit (dependencies.verify_csrf); ein CSRF-Token
    ohne Cookie wäre nur ein Geheimnis mehr, das der Client aufbewahren müsste.
    """
    return {
        "access_token": tokens.access_token,
        "token_type": "bearer",
        "requires_2fa": False,
        "requires_verification": False,
        "refresh_token": tokens.refresh_token,
        "expires_in": settings.access_token_expire_minutes * 60,
    }


def _save_initial_email_config(req: OwnerSetupRequest) -> None:
    """Speichert die einmalige Setup-Konfiguration verschluesselt.

    Der Aufrufer stellt sicher, dass noch kein Owner existiert. Secrets werden
    weder zurueckgegeben noch geloggt und nur DIS-verschluesselt persistiert.
    """
    config = req.email_config
    if config is None:
        raise HTTPException(
            status_code=503,
            detail="E-Mail-Versand muss fuer die Verifikation eingerichtet werden.",
        )

    PanelSettingsService.set("smtp_from", str(config.from_address))
    encrypted = AuthService.encrypt_secret(
        config.resend_api_key,
        aad="msm:settings:resend_api_key",
    )
    PanelSettingsService.set("resend_api_key_encrypted", encrypted)
    PanelSettingsService.set("resend_api_key", "")
    PanelSettingsService.set("smtp_host", "")
    PanelSettingsService.set("smtp_user", "")
    PanelSettingsService.set("smtp_password_encrypted", "")
    PanelSettingsService.set("smtp_password", "")


def _clear_failed_initial_email_config() -> None:
    """Entfernt nur die im fehlgeschlagenen First-Run gesetzten Werte."""
    PanelSettingsService.set("smtp_from", "")
    PanelSettingsService.set("resend_api_key_encrypted", "")
    PanelSettingsService.set("resend_api_key", "")


@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    return {
        "setup_required": not AuthService.is_owner_exists(db),
        "email_configured": EmailService.is_configured(),
    }


@router.post("/setup", status_code=201, dependencies=[Depends(auth_rate_limit)])
async def setup_owner(req: OwnerSetupRequest, db: Session = Depends(get_db)) -> dict:
    if AuthService.is_owner_exists(db):
        raise HTTPException(status_code=400, detail="Setup bereits abgeschlossen")
    if AuthService.get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Username bereits vergeben")
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")

    configured_during_request = not EmailService.is_configured()
    if configured_during_request:
        _save_initial_email_config(req)

    # Owner erstellen (noch nicht verifiziert)
    user = AuthService.create_owner(db, req.username, req.email, req.password)
    user.email_verified = False
    db.commit()

    # Verifikations-Code generieren und per Email senden
    code = EmailVerificationService.create_verification(db, req.email, "setup")
    try:
        email_sent = await EmailService.send_verification_code_email(
            req.email, req.username, code
        )
    except Exception:
        email_sent = False
    if not email_sent:
        _log_smtp_missing(req.email)
        # Setup-User und Verifikationseintrag wieder entfernen
        db.query(EmailVerification).filter(
            EmailVerification.email_hash == EmailVerificationService._email_hash(req.email)
        ).delete()
        db.delete(user)
        db.commit()
        if configured_during_request:
            _clear_failed_initial_email_config()
        raise HTTPException(
            status_code=503,
            detail="Verifikations-E-Mail konnte nicht gesendet werden. Einstellungen pruefen."
        )

    return {"message": "Verifikations-Code gesendet", "requires_verification": True}


@router.post("/setup-verify", dependencies=[Depends(auth_rate_limit)])
def setup_verify(req: SetupVerifyRequest, db: Session = Depends(get_db)) -> dict:
    user = AuthService.get_user_by_email(db, req.email)
    if not user:
        raise HTTPException(status_code=400, detail="Ungültige E-Mail")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Bereits verifiziert")

    valid = EmailVerificationService.verify_code(db, req.email, "setup", req.code)
    if not valid:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Code")

    user.email_verified = True
    db.commit()
    return {"message": "E-Mail verifiziert", "setup_completed": True}


@router.post("/setup-resend", dependencies=[Depends(auth_rate_limit)])
async def setup_resend(req: ResendVerificationRequest, db: Session = Depends(get_db)) -> dict:
    user = AuthService.get_user_by_email(db, req.email)
    if not user or user.email_verified:
        raise HTTPException(status_code=400, detail="Ungültige Anfrage")

    code = EmailVerificationService.create_verification(db, req.email, "setup")
    if not EmailService.is_configured() or not await EmailService.send_verification_code_email(
        req.email, user.username, code
    ):
        _log_smtp_missing(req.email)
        raise HTTPException(
            status_code=503,
            detail="SMTP nicht konfiguriert. Verifikation nicht möglich."
        )

    return {"message": "Code erneut gesendet"}


@router.post("/resend-verification", dependencies=[Depends(auth_rate_limit)])
async def resend_verification(req: ResendVerificationRequest, db: Session = Depends(get_db)) -> dict:
    """Neuen Verifizierungscode für einen unverifizierten User senden."""
    user = AuthService.get_user_by_email(db, req.email)
    if not user or user.email_verified:
        raise HTTPException(status_code=400, detail="Ungültige Anfrage")

    code = EmailVerificationService.create_verification(db, req.email, LOGIN_VERIFICATION_PURPOSE)
    if EmailService.is_configured():
        await EmailService.send_verification_code_email(req.email, user.username, code)
    else:
        _log_smtp_missing(req.email)
        raise HTTPException(
            status_code=503,
            detail="SMTP nicht konfiguriert. Verifikation nicht möglich."
        )

    return {"message": "Code erneut gesendet"}


@router.post("/register", response_model=RegistrationResponse, status_code=201, dependencies=[Depends(auth_rate_limit)])
async def register(
    req: UserCreate,
    request: Request,
    db: Session = Depends(get_db)
) -> dict:
    await CaptchaService.verify_token(req.captcha_token, client_ip=request.client.host if request.client else None)
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
    db.commit()
    if default_role is not None:
        set_user_roles(db, user, [default_role.id])
    
    code = EmailVerificationService.create_verification(db, user.email, REGISTER_VERIFICATION_PURPOSE)
    if EmailService.is_configured():
        await EmailService.send_verification_code_email(user.email, user.username, code)
    else:
        _log_smtp_missing(user.email)
        
    return {"email": user.email, "requires_verification": True}


@router.post("/register-verify", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
async def register_verify(
    req: SetupVerifyRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    user = AuthService.get_user_by_email(db, req.email)
    if not user:
        raise HTTPException(status_code=400, detail="Ungültige E-Mail")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Bereits verifiziert")

    valid = EmailVerificationService.verify_code(db, req.email, REGISTER_VERIFICATION_PURPOSE, req.code)
    if not valid:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Code")

    user.email_verified = True
    db.commit()
    tokens = _set_login_session(response, db, user)

    # Email-Benachrichtigung für erfolgreiche Registrierung (asynchron im Hintergrund)
    if EmailService.is_configured() and user.email_notifications and user.email:
        background_tasks.add_task(
            EmailService.send_account_registered_notification,
            user.email,
            user.username,
        )

    if req.native_client:
        return _native_token_body(tokens)
    return {"access_token": "", "token_type": "bearer", "requires_2fa": False, "requires_verification": False}


@router.post("/login-verify", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
def login_verify(
    req: LoginVerifyRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    user = AuthService.get_user_by_username(db, req.username)
    if not user or not AuthService.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Ungültige Anmeldedaten")
    AuthService.rehash_password_if_needed(db, user, req.password)
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account deaktiviert")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Bereits verifiziert")

    valid = EmailVerificationService.verify_code_for_purposes(
        db,
        user.email,
        LOGIN_ACCEPTED_VERIFICATION_PURPOSES,
        req.code,
    )
    if not valid:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Code")

    user.email_verified = True
    db.commit()

    if user.two_factor_enabled:
        if not req.otp_code:
            return {"access_token": "", "token_type": "", "requires_2fa": True, "requires_verification": False, "email": user.email}
        if not AuthService.verify_current_2fa_code(user, req.otp_code):
            backup_valid = BackupCodeService.validate_backup_code(db, user.id, req.otp_code)
            if not backup_valid:
                raise HTTPException(status_code=401, detail="Ungültiger 2FA-Code oder Backup-Code")

    tokens = _set_login_session(response, db, user)
    if req.native_client:
        return _native_token_body(tokens)
    return {"access_token": "", "token_type": "bearer", "requires_2fa": False, "requires_verification": False}


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
async def login(
    req: LoginRequest,
    response: Response,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    await CaptchaService.verify_token(req.captcha_token, client_ip=request.client.host if request.client else None)
    user = AuthService.get_user_by_username(db, req.username)
    if not user or not AuthService.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Ungültige Anmeldedaten")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account deaktiviert")

    if not user.email_verified:
        has_pending_code = EmailVerificationService.has_active_verification(
            db,
            user.email,
            LOGIN_ACCEPTED_VERIFICATION_PURPOSES,
        )
        code = None if has_pending_code else EmailVerificationService.create_verification(db, user.email, LOGIN_VERIFICATION_PURPOSE)
        if code and EmailService.is_configured():
            await EmailService.send_verification_code_email(user.email, user.username, code)
        elif code:
            _log_smtp_missing(user.email)
        return {"access_token": "", "token_type": "", "requires_2fa": False, "requires_verification": True, "email": user.email}

    if user.two_factor_enabled:
        if not req.otp_code:
            return {"requires_2fa": True, "access_token": "", "token_type": "", "requires_verification": False, "email": user.email}
        if not AuthService.verify_current_2fa_code(user, req.otp_code):
            # Backup-Code als Fallback pruefen
            backup_valid = BackupCodeService.validate_backup_code(db, user.id, req.otp_code)
            if not backup_valid:
                raise HTTPException(status_code=401, detail="Ungültiger 2FA-Code oder Backup-Code")

    tokens = _set_login_session(response, db, user)

    # Sicherheitsbenachrichtigung bei Login (asynchron im Hintergrund, blockiert Login-Antwort nicht)
    try:
        should_notify = EmailService.is_configured() and user.email_notifications and bool(user.email)
        user_email = user.email if should_notify else None
    except Exception:
        user_email = None

    if user_email:
        client_ip = request.client.host if request.client else "unbekannt"
        user_agent = request.headers.get("user-agent", "unbekannt")
        background_tasks.add_task(
            EmailService.send_new_device_login_notification,
            user_email,
            user.username,
            client_ip,
            user_agent,
        )

    if req.native_client:
        return _native_token_body(tokens)
    return {"access_token": "", "token_type": "bearer", "requires_2fa": False}


# ── Geraetekopplung (Smart System) ───────────────────────────────────────────
#
# Der einzige Weg, wie die Desktop-App eine Sitzung bekommt. Sie kennt weder
# Passwort noch 2FA-Code, und genau das ist der Punkt: bei aktivem Captcha
# verlangt `/login` ein Turnstile-Token, das ein Tauri-WebView nicht besorgen
# kann (Cloudflare-Schluessel haengen an Domains, `tauri.localhost` ist keine).
# Statt die Anmeldestrecke im Desktop-Fenster nachzubauen, laedt der bereits
# angemeldete Mensch sein Geraet ein.
#
# `/devices/redeem` haengt unter `auth_rate_limit`, um Brute-Force-Versuche
# auf Kopplungscodes zu verhindern.


@router.post("/devices/pairing", response_model=PairingCreated)
def create_device_pairing(
    req: PairingCreateRequest,
    user: User = Depends(require_global("ai.chat.use")),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Erzeugt einen Kopplungscode. Er steht **nur** in dieser Antwort.

    `ai.chat.use` als Schranke: ohne dieses Recht kann die App nichts, was sie
    ausmacht. Wer es nicht hat, soll erst gar keinen Zugang erzeugen koennen.
    """
    einladung, code = device_pairing_service.anlegen(db, user, req.label)
    return {
        "code": code,
        "expires_at": einladung.expires_at,
        "label": einladung.label,
        "qr_data_uri": qr_datenuri(code, error="h", border=1),
    }


@router.get("/devices/pairing/{code}/status")
def get_device_pairing_status(
    code: str,
    user: User = Depends(require_global("ai.chat.use")),
    db: Session = Depends(get_db),
) -> dict:
    """Prueft den Einloesestatus eines erzeugten Kopplungscodes fuer das Panel."""
    return device_pairing_service.status(db, user, code)


@router.post("/devices/redeem", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
def redeem_device_pairing(
    req: PairingRedeemRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Loest einen Kopplungscode ein und gibt dafuer eine Sitzung.

    Kein Captcha: der Code ist der Beweis, und er stammt aus einer bereits
    angemeldeten Sitzung — die Pruefungen, die Captcha ersetzen soll, sind dort
    schon gelaufen.

    Die Antwort auf einen unbrauchbaren Code ist immer dieselbe, egal ob er
    unbekannt, abgelaufen oder schon benutzt ist. Wer raet, soll aus der
    Antwort nicht lernen, ob er nah dran war.
    """
    einladung = device_pairing_service.einloesen(db, req.code)
    if einladung is None:
        raise HTTPException(status_code=400, detail="Kopplungscode ungültig oder abgelaufen")
    user = AuthService.get_user_by_id(db, einladung.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Kopplungscode ungültig oder abgelaufen")

    # Erst hier entsteht die Sitzung, und mit ihr die Familie. `geraet` macht
    # sie zur Desktop-Sitzung: nur sie bekommt die Werkzeuge fuer den Rechner
    # angeboten (`ai_tool_registry.herkunft_schnitt`).
    tokens = issue_session(response, db, user, geraet="desktop")
    rt = AuthService.validate_refresh_token(db, tokens.refresh_token)
    if rt is not None:
        if req.label.strip():
            einladung.label = req.label.strip()[: device_pairing_service.MAX_BEZEICHNUNG]
        device_pairing_service.familie_vermerken(db, einladung, rt.family)
        device_pairing_service.aktivitaet_vermerken(rt.family)
    return _native_token_body(tokens)


@router.post("/devices/heartbeat")
def device_heartbeat(
    user: User = Depends(get_current_user),
    familie: str | None = Depends(session_familie),
) -> dict:
    """Aktualisiert die letzte Aktivitaet eines gekoppelten Geraets."""
    if familie:
        device_pairing_service.aktivitaet_vermerken(familie)
    return {"status": "ok"}


@router.get("/devices", response_model=list[PairedDevice])
def list_devices(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Die gekoppelten Geraete dieses Benutzers samt Aktivitaetsstatus und letztem Login."""
    return device_pairing_service.geraete_details(db, user)


@router.delete("/devices/{family}")
def revoke_device(
    family: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Sperrt ein Geraet aus und vergisst seinen Namen.

    Zwei Schritte, und der erste zaehlt: die Refresh-Familie wird widerrufen,
    damit das Geraet keine neue Sitzung mehr holen kann. Das laufende
    Access-Token bleibt bis zu seinem Ablauf gueltig — dieselbe Regel wie
    ueberall sonst; ein Widerruf wirkt spaetestens beim naechsten Erneuern.
    """
    AuthService.revoke_refresh_family(db, user.id, family)
    if device_pairing_service.vergessen(db, user, family) is None:
        raise HTTPException(status_code=404, detail="Gerät nicht gefunden")
    return {"message": "Gerät entkoppelt"}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    req: LogoutRequest | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Serverseitiges Logout: Refresh-Token revozieren, Cookies loeschen.

    Native Clients (Bearer statt Cookies) melden sich ueber denselben Endpunkt
    ab: das Access-Token kommt aus dem Authorization-Header, das Refresh-Token
    optional im Body. Der Widerruf laeuft danach identisch — jti auf die
    Blacklist, alle Refresh-Tokens der Familie revozieren.
    """
    refresh_value = (req.refresh_token if req else None) or request.cookies.get("__Secure-refresh_token")
    family_to_revoke: str | None = None
    user_id_to_revoke: int | None = None
    if refresh_value:
        rt = AuthService.validate_refresh_token(db, refresh_value)
        if rt:
            user_id_to_revoke = rt.user_id
            family_to_revoke = rt.family
            AuthService.revoke_refresh_token(db, rt)

    access_value = _bearer_token(request) or request.cookies.get("__Secure-access_token")
    if access_value:
        payload = AuthService.decode_token(access_value, verify_exp=False)
        if payload:
            if user_id_to_revoke is None:
                user_id_to_revoke = payload.get("user_id")
            if not family_to_revoke and payload.get("familie"):
                family_to_revoke = payload.get("familie")
            if payload.get("jti"):
                expires = payload.get("exp")
                from datetime import datetime
                expires_dt = datetime.fromtimestamp(expires, tz=timezone.utc) if expires else None
                blacklist_jwt(db, payload["jti"], user_id_to_revoke, expires_dt)

    # Wenn Access-Token abgelaufen/ungueltig ist oder Familie nicht enthaelt,
    # versuche den User und die Familie ueber den Refresh-Token zu ermitteln.
    if (user_id_to_revoke is None or not family_to_revoke) and refresh_value:
        rt_fallback = AuthService.find_any_refresh_token(db, refresh_value)
        if rt_fallback:
            if user_id_to_revoke is None:
                user_id_to_revoke = rt_fallback.user_id
            if not family_to_revoke:
                family_to_revoke = rt_fallback.family

    if user_id_to_revoke is not None:
        if family_to_revoke:
            AuthService.revoke_refresh_family(db, user_id_to_revoke, family_to_revoke)
        else:
            AuthService.revoke_all_user_refresh_tokens(db, user_id_to_revoke)

    _clear_auth_cookies(response)
    return {"message": "Abgemeldet"}


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    req: NativeRefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Rotiert Access-Token und Refresh-Token.

    Browser schicken das Refresh-Token als Cookie, native Clients im Body —
    beide Wege laufen durch dieselbe Validierung, dieselbe Familie und
    dieselbe Wiederverwendungserkennung. Wer das Token im Body schickt, bekommt
    die neuen Tokens auch im Body zurueck (Cookies kann er nicht lesen).
    """
    body_token = req.refresh_token if req else None
    refresh_value = body_token or request.cookies.get("__Secure-refresh_token")
    if not refresh_value:
        raise HTTPException(status_code=401, detail="Kein Refresh-Token")
    rt = AuthService.validate_refresh_token(db, refresh_value)
    if not rt:
        recent_rt = AuthService.find_recently_used_refresh_token(db, refresh_value, max_age_seconds=30)
        if recent_rt:
            user = AuthService.get_user_by_id(db, recent_rt.user_id)
            if user and user.is_active:
                family = recent_rt.family
                device_pairing_service.aktivitaet_vermerken(family)
                tokens = issue_session(response, db, user, family=family, geraet=recent_rt.geraet)
                if body_token:
                    return _native_token_body(tokens)
                return {"message": "Token refreshed"}

        # Replay-Schutz: Wurde das Token bereits verwendet oder widerrufen,
        # wird die gesamte Familie unverzüglich revoziert (RFC 6749 BCP).
        used_rt = AuthService.find_any_refresh_token(db, refresh_value)
        if used_rt and (used_rt.used_at is not None or used_rt.revoked_at is not None):
            AuthService.revoke_refresh_family(db, used_rt.user_id, used_rt.family)

        raise HTTPException(status_code=401, detail="Ungültiges Refresh-Token")
    family = rt.family
    device_pairing_service.aktivitaet_vermerken(family)
    AuthService.mark_refresh_token_used(db, rt)
    user = AuthService.get_user_by_id(db, rt.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User nicht gefunden oder inaktiv")
    # Die Rotation stellt eine vollwertige Sitzung aus und geht deshalb ueber
    # denselben Weg wie jeder Login. Vorher baute sie die drei Token selbst —
    # und liess dabei die `jti` weg. Folge: ab dem ersten Refresh konnte der
    # Logout das Access-Token nicht mehr auf die Blacklist setzen, ein
    # entwendetes Cookie blieb bis zum Ablauf voll gueltig. Die Familie wird
    # weitergereicht, damit die Wiederverwendungserkennung nicht abreisst — und
    # das Geraet mit ihr, sonst waere eine gekoppelte Sitzung nach dem ersten
    # Erneuern eine gewoehnliche Panel-Sitzung und verloere die Werkzeuge fuer
    # den Rechner des Benutzers.
    tokens = issue_session(response, db, user, family=family, geraet=rt.geraet)
    if body_token:
        return _native_token_body(tokens)
    return {"message": "Token refreshed"}


@router.get("/me", response_model=UserResponse)
def me(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
) -> User:
    # Cross-Origin SPA: CSRF-Cookie ist nicht per document.cookie lesbar.
    # Echo des Cookie-Werts als Header (bereits vom Browser mitgeschickt).
    csrf = request.cookies.get("__Secure-csrf_token")
    if csrf:
        response.headers["X-CSRF-Token"] = csrf
    return user


@router.patch("/me/notifications")
def update_notifications(
    enabled: bool | None = None,
    ai: bool | None = None,
    device: bool | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Schaltet E-Mail-, KI- und Geräte-Meldungen einzeln.

    ``enabled`` steuert die E-Mails, ``ai`` steuert KI-Hinweise im Panel,
    ``device`` steuert Pop-up-Meldungen auf Windows- und Android-Geräten.
    Alle drei sind optional: wer nur eines umlegt, rührt die anderen nicht an.
    """
    if enabled is not None:
        user.email_notifications = enabled
    if ai is not None:
        user.ai_notifications = ai
    if device is not None:
        user.device_notifications = device
    db.commit()
    return {
        "email_notifications": user.email_notifications,
        "ai_notifications": user.ai_notifications,
        "device_notifications": user.device_notifications,
    }


@router.patch("/me/timezone")
def update_timezone(
    req: TimezoneUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Setzt die kanonische IANA-Zeitzone des Benutzers (z. B. 'Europe/Berlin')."""
    user.time_zone = req.time_zone
    db.commit()
    return {
        "time_zone": user.time_zone,
    }


@router.patch("/me/location-sharing")
def update_location_sharing(
    req: LocationSharingUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Merkt ausschließlich die Einwilligung zur einmaligen Standortnutzung."""
    user.location_sharing_enabled = req.enabled
    db.commit()
    return {"location_sharing_enabled": user.location_sharing_enabled}


@router.patch("/me/agent-name")
def update_agent_name(
    req: AgentNameUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Setzt den Rufnamen des Assistenten (None/leer = Standardname 'Singra').

    Der Name landet im Lageblock (services/ai_lage.py), nie im statischen
    Systemprompt — sonst waere der Prompt je Benutzer verschieden und das
    Prompt-Caching des Anbieters tot. Was als Name erlaubt ist, entscheidet
    allein das Schema (schemas/user.py): eine Zeile, keine Steuerzeichen.
    """
    user.agent_name = req.agent_name
    db.commit()
    return {"agent_name": user.agent_name}


@router.patch("/me/ai-provider")
def update_ai_provider(
    req: AiProviderChoiceRequest,
    # Dieselbe Schranke wie die Auswahlliste (`/api/ai/providers`): wer den
    # Chat nicht nutzen darf, hat auch keine Modellwahl zu speichern.
    user: User = Depends(require_global("ai.chat.use")),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Setzt den bevorzugten KI-Zugang des Benutzers (None = keine Wahl).

    Die Wahl folgt dem Konto statt dem Browser: die Desktop-App hat einen
    eigenen localStorage und lief ohne dieses Feld still auf dem erstbesten
    Zugang. Chat (AiChat) und Sprachmodus (routers/ai_voice.sprachzugang)
    lesen die Wahl, wenn der Client keine explizite mitschickt.

    Geprüft wird dieselbe Grenze wie beim Senden einer Nachricht: der Zugang
    muss existieren, aktiv sein und Chat sprechen — was dort ein 404 wäre,
    wird hier gar nicht erst gespeichert.
    """
    if req.provider_id is not None:
        # Weicher Import: auth darf die KI-Dienste nicht beim Modulstart laden
        # (Startkosten, Importzyklen) — nur dieser eine Pfad braucht sie.
        from models.ai_provider import AiProvider
        from services import ai_provider_service

        provider = db.get(AiProvider, req.provider_id)
        if provider is None or not provider.enabled or not ai_provider_service.fuer_chat(provider):
            raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    user.ai_provider_id = req.provider_id
    db.commit()
    return {"ai_provider_id": user.ai_provider_id}


# ── Profilbild (Avatar) ──────────────────────────────────────────────────

MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_AVATAR_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _get_avatars_dir() -> str:
    base = settings.panel_config_dir if settings.panel_config_dir else "."
    avatars_dir = os.path.join(base, "data", "avatars")
    os.makedirs(avatars_dir, exist_ok=True)
    return avatars_dir


def _validate_image_bytes(content: bytes, mime_type: str) -> bool:
    if len(content) > MAX_AVATAR_BYTES or len(content) < 8:
        return False
    # Magic number checks
    if mime_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff"):
        return True
    if mime_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if mime_type == "image/gif" and (content.startswith(b"GIF87a") or content.startswith(b"GIF89a")):
        return True
    if mime_type == "image/webp" and content.startswith(b"RIFF") and b"WEBP" in content[8:16]:
        return True
    return False


@router.post("/me/avatar", response_model=UserResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> User:
    """Lädt ein eigenes Profilbild hoch (max. 5 MB)."""
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Bildformat. Erlaubt sind JPEG, PNG, WebP und GIF.",
        )
    content = await file.read()
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Bild darf maximal 5 MB groß sein.")
    if not _validate_image_bytes(content, content_type):
        raise HTTPException(status_code=400, detail="Ungültige oder beschädigte Bilddatei.")

    avatars_dir = _get_avatars_dir()
    if user.avatar_url:
        old_filename = user.avatar_url.split("/")[-1]
        if old_filename and re.match(r"^[a-zA-Z0-9_\-\.]+$", old_filename):
            old_path = os.path.join(avatars_dir, old_filename)
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass

    ext = ALLOWED_AVATAR_TYPES[content_type]
    filename = f"avatar_{user.id}_{uuid.uuid4().hex[:12]}{ext}"
    file_path = os.path.join(avatars_dir, filename)
    with open(file_path, "wb") as f:
        f.write(content)

    user.avatar_url = f"/api/auth/avatar/{filename}"
    db.commit()
    db.refresh(user)
    return user


@router.delete("/me/avatar", response_model=UserResponse)
def delete_avatar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> User:
    """Entfernt das eigene Profilbild."""
    if user.avatar_url:
        avatars_dir = _get_avatars_dir()
        old_filename = user.avatar_url.split("/")[-1]
        if old_filename and re.match(r"^[a-zA-Z0-9_\-\.]+$", old_filename):
            old_path = os.path.join(avatars_dir, old_filename)
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        user.avatar_url = None
        db.commit()
        db.refresh(user)
    return user


@router.get("/avatar/{filename}")
def get_avatar(filename: str):
    """Liefert ein gespeichertes Profilbild aus."""
    if not re.match(r"^avatar_\d+_[a-zA-Z0-9]+\.(jpg|jpeg|png|webp|gif)$", filename):
        raise HTTPException(status_code=404, detail="Profilbild nicht gefunden")
    avatars_dir = _get_avatars_dir()
    file_path = os.path.join(avatars_dir, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Profilbild nicht gefunden")
    return FileResponse(
        file_path,
        headers={
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        },
    )



@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Eigenes Passwort ändern. Erfordert aktuelles Passwort + 2FA-Code wenn 2FA aktiv."""
    if not AuthService.verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Aktuelles Passwort falsch")

    if user.two_factor_enabled:
        if not req.otp_code:
            raise HTTPException(status_code=401, detail="2FA-Code erforderlich")
        if not AuthService.verify_current_2fa_code(user, req.otp_code):
            raise HTTPException(status_code=401, detail="Ungültiger 2FA-Code")

    AuthService.reset_password(db, user, req.new_password)
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="auth.password.change",
        target_type="user",
        target_id=user.id,
        details={"username": user.username},
        commit=True,
    )
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_password_changed_notification(user.email, user.username)
    return {"message": "Passwort geändert"}


@router.post("/change-email")
async def change_email(
    req: ChangeEmailRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """E-Mail-Adresse ändern. Erfordert 2FA-Code wenn 2FA aktiv."""
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")

    if user.two_factor_enabled:
        if not req.otp_code:
            raise HTTPException(status_code=401, detail="2FA-Code erforderlich")
        if not AuthService.verify_current_2fa_code(user, req.otp_code):
            raise HTTPException(status_code=401, detail="Ungültiger 2FA-Code")

    user.email = req.email
    user.email_verified = False
    db.commit()
    # Verifizierungscode fuer neue E-Mail senden
    if EmailService.is_configured():
        code = EmailVerificationService.create_verification(db, req.email, "setup")
        await EmailService.send_verification_code_email(req.email, user.username, code)
    return {"message": "E-Mail geändert. Bitte neue E-Mail verifizieren."}


@router.post("/forgot-password", dependencies=[Depends(auth_rate_limit)])
async def forgot_password(
    req: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db)
) -> dict:
    await CaptchaService.verify_token(req.captcha_token, client_ip=request.client.host if request.client else None)
    user = AuthService.get_user_by_email(db, req.email)
    if not user:
        return {"message": "Falls die E-Mail existiert, wurde eine Nachricht gesendet"}
    token = AuthService.set_password_reset_token(db, user)
    await EmailService.send_password_reset_email(user.email, user.username, token)
    return {"message": "Falls die E-Mail existiert, wurde eine Nachricht gesendet"}


@router.post("/reset-password", dependencies=[Depends(auth_rate_limit)])
async def reset_password(
    req: PasswordResetConfirm,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    await CaptchaService.verify_token(req.captcha_token, client_ip=request.client.host if request.client else None)
    token_hash = AuthService._hash_reset_token(req.token)
    user = db.query(User).filter(
        User.password_reset_token == token_hash,
        User.password_reset_expires > datetime.now(timezone.utc),
    ).first()
    if not user:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener Token")
    AuthService.reset_password(db, user, req.new_password)
    return {"message": "Passwort zurückgesetzt"}
@router.delete("/delete-account")
def delete_account(
    req: DeleteAccountRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Eigenes Konto loeschen.

    Zentrale Logik:
    - Lokale Accounts (keine OAuth-Links): aktuelles Passwort erforderlich.
    - Social-Only Accounts (haben OAuthUserLink): Passwort-Schritt wird übersprungen
      (die aktuelle Session beweist Besitz via Social-Login).
    - 2FA wird **niemals** übersprungen, wenn aktiv (auch nicht bei Social).
    - Immer: exaktes Wort "delete" als confirmation (Frontend verhindert Paste).
    """
    from models import OAuthUserLink

    # Zentrale Entscheidung: braucht dieser User ein Passwort für Löschung?
    has_oauth_links = (
        db.query(OAuthUserLink)
        .filter(OAuthUserLink.user_id == user.id)
        .first()
    ) is not None

    if not has_oauth_links:
        # Lokaler Account: Passwort zwingend
        if not req.password:
            raise HTTPException(status_code=400, detail="Passwort erforderlich")
        if not AuthService.verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Passwort ungültig")
    # else: Social-Only -> Passwort überspringen (wie gewünscht)

    # Immer Bestätigungswort "delete" (nicht kopierbar im Frontend)
    if (req.confirmation or "").strip().lower() != "delete":
        raise HTTPException(status_code=400, detail="Bestätigung delete erforderlich")

    # 2FA: niemals überspringen wenn aktiv
    if user.two_factor_enabled:
        if not req.otp_code:
            raise HTTPException(status_code=401, detail="2FA-Code erforderlich")
        if not AuthService.verify_current_2fa_code(user, req.otp_code):
            raise HTTPException(status_code=401, detail="Ungültiger 2FA-Code")

    # 3. Owner-Sperre: Owner-Account darf nicht geloescht werden
    if user.is_owner:
        raise HTTPException(status_code=403, detail="Owner-Account kann nicht gelöscht werden")

    # 4. Atomar loeschen
    AuthService.delete_account_atomically(db, user)

    # 5. Cookies loeschen
    _clear_auth_cookies(response)

    return {"message": "Account gelöscht"}

@router.post("/2fa/setup")
def setup_2fa(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Legt ein neues TOTP-Geheimnis an und gibt es einmalig im Klartext zurueck.

    `verify_csrf` ist hier Pflicht, weil der Endpunkt zustandsaendernd ist und
    keinen Body braucht. Im unterstuetzten Cross-Domain-Betrieb
    (`settings.cookie_cross_site`) stehen alle Auth-Cookies auf SameSite=None;
    ein fremdes `<form method="POST">` erreicht die Route dann ohne Preflight,
    und CORS verhindert nur das Lesen der Antwort, nicht die Ausfuehrung. Ohne
    die Pruefung koennte eine beliebige Seite das TOTP-Geheimnis des Opfers
    austauschen.

    Ein bereits aktives 2FA wird ausserdem nicht mehr stillschweigend
    abgeschaltet. Der zweite Faktor darf nur dort fallen, wo der aktuelle Code
    nachgewiesen wird — das ist `/2fa/disable`. Wer neu einrichten will, geht
    denselben Weg: erst deaktivieren, dann aufsetzen.
    """
    if user.two_factor_enabled:
        raise HTTPException(
            status_code=400,
            detail="2FA ist bereits aktiv. Bitte zuerst deaktivieren.",
        )
    secret = DisClient.generate_totp_secret()
    user.two_factor_secret_encrypted = AuthService.encrypt_secret(secret, aad=f"msm:user:{user.id}:2fa")
    user.two_factor_enabled = False
    db.commit()
    uri = DisClient.build_totp_uri("Maunting Service Manager", user.email, secret)
    # Der QR-Code entsteht hier und nicht im Browser: die Antwort traegt das
    # Geheimnis ohnehin, ein zusaetzliches Bild verraet also nichts Neues — im
    # Gegensatz zum frueheren Weg ueber einen fremden Bilddienst, der es aus dem
    # Panel herausgetragen hat. Siehe services/totp_qr.py.
    return {"secret": secret, "uri": uri, "qr_data_uri": qr_datenuri(uri)}


@router.post("/2fa/enable")
async def enable_2fa(
    otp_code: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
) -> dict:
    """Aktiviert 2FA nach Nachweis eines gueltigen Codes.

    Der Code allein haelt hier zwar schon jeden Fremdaufruf auf, aber der
    Endpunkt ist zustandsaendernd — und genau die Uneinheitlichkeit war der
    Grund, warum `/2fa/setup` beim Nachziehen des CSRF-Schutzes uebersehen
    wurde. Deshalb gilt die Pflicht jetzt fuer alle Auth-Endpunkte ohne
    Ausnahme.
    """
    if not user.two_factor_secret_encrypted:
        raise HTTPException(status_code=400, detail="2FA nicht eingerichtet")
    if not AuthService.verify_current_2fa_code(user, otp_code):
        raise HTTPException(status_code=400, detail="Ungültiger Code")
    user.two_factor_enabled = True
    db.commit()
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="auth.2fa.enable",
        target_type="user",
        target_id=user.id,
        details={"username": user.username},
        commit=True,
    )
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
    if not AuthService.verify_current_2fa_code(user, otp_code):
        raise HTTPException(status_code=400, detail="Ungültiger 2FA-Code")
    user.two_factor_enabled = False
    user.two_factor_secret_encrypted = None
    BackupCodeService.clear_all_backup_codes(db, user.id)
    db.commit()
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="auth.2fa.disable",
        target_type="user",
        target_id=user.id,
        details={"username": user.username},
        commit=True,
    )
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
