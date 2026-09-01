import os
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from dependencies import require_global, verify_csrf
from schemas.panel_settings import (
    PanelSettingsResponse,
    PanelSettingsUpdate,
    TestEmailRequest,
    ResendKeyRequest,
    SteamApiKeyRequest,
    CurseForgeApiKeyRequest,
    CloudflareApiTokenRequest,
    SteamAccountRequest,
    GitHubTokenRequest,
    GitHubTokenStatus,
    SingraWidgetInstallIdRequest,
    SingraWebhookSecretRequest,
)
from models import User
from services import audit_service
from services.panel_settings_service import PanelSettingsService
from services.rate_limit_settings import (
    KEY_AUTH as RATE_LIMIT_AUTH_KEY,
    KEY_GLOBAL as RATE_LIMIT_GLOBAL_KEY,
    resolve_auth_limit,
    resolve_global_limit,
    validate_auth_limit,
    validate_global_limit,
)
from services.email_service import EmailService
from services.auth_service import AuthService
from services.steam_account_service import SteamAccountService
from services.steam_api_key_service import (
    current_source as steam_api_source,
    resolve_key as resolve_steam_api_key,
    set_panel_key,
    status as steam_api_status,
)
from services.curseforge_api_key_service import (
    current_source as curseforge_api_source,
    resolve_key as resolve_curseforge_api_key,
    set_panel_key as set_curseforge_panel_key,
    clear_panel_key as clear_curseforge_panel_key,
    status as curseforge_api_status,
)
from services.cloudflare_api_key_service import (
    current_source as cloudflare_api_source,
    resolve_key as resolve_cloudflare_api_key,
    set_panel_key as set_cloudflare_panel_key,
    clear_panel_key as clear_cloudflare_panel_key,
    status as cloudflare_api_status,
)
from services.github_token_service import status as github_token_status, set_panel_token as set_github_panel_token, clear_panel_token as clear_github_panel_token
from services import singra_webhook_secret_service as singra_secret
from services import singra_widget_install_service as singra_install

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _mask_secret(value: str) -> str:
    """Zeigt nur die letzten 4 Zeichen eines Secrets, falls vorhanden."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


@router.get("", response_model=PanelSettingsResponse)
def get_settings(db: Session = Depends(get_db), _=Depends(require_global("panel.settings.read"))) -> dict:
    """Liest alle Panel-Einstellungen (DB-Werte mit Fallback auf Defaults).

    Passwoerter und API-Keys werden maskiert zurueckgegeben.
    """
    all_db = PanelSettingsService.get_all()
    SteamAccountService.migrate_legacy_if_needed()
    steam_key = resolve_steam_api_key()
    api_st = steam_api_status()
    curseforge_key = resolve_curseforge_api_key()
    cf_st = curseforge_api_status()
    cf_token = resolve_cloudflare_api_key()
    cfl_st = cloudflare_api_status()
    return {
        "panel_url": all_db.get("panel_url", ""),
        "imprint_enabled": all_db.get("imprint_enabled", "false") == "true",
        "imprint_url": all_db.get("imprint_url", ""),
        "smtp_host": all_db.get("smtp_host", ""),
        "smtp_port": all_db.get("smtp_port", "587"),
        "smtp_user": all_db.get("smtp_user", ""),
        "smtp_password": _mask_secret(EmailService._get_setting("smtp_password")),
        "smtp_from": all_db.get("smtp_from", ""),
        "smtp_tls": all_db.get("smtp_tls", "true"),
        "resend_api_key": _mask_secret(EmailService._get_setting("resend_api_key")),
        "default_language": all_db.get("default_language", "de"),
        "email_configured": EmailService.is_configured(),
        "email_provider": EmailService._get_provider(),
        "steam_api_key": _mask_secret(steam_key),
        "steam_api_configured": bool(steam_key),
        "steam_api_source": api_st.get("source", "none"),
        "curseforge_api_key": _mask_secret(curseforge_key),
        "curseforge_api_configured": bool(curseforge_key),
        "curseforge_api_source": cf_st.get("source", "none"),
        "steam_account_username": SteamAccountService.get_username(),
        "steam_account_configured": SteamAccountService.is_configured(),
        **github_token_status_dict(),
        "time_format": all_db.get("time_format", "24h"),
        "support_widget_enabled": all_db.get("support_widget_enabled", "false") == "true",
        "support_widget_mode": all_db.get("support_widget_mode", "singra"),
        "support_widget_crisp_website_id": all_db.get("support_widget_crisp_website_id", ""),
        "support_widget_tawk_property_id": all_db.get("support_widget_tawk_property_id", ""),
        "support_widget_tawk_widget_id": all_db.get("support_widget_tawk_widget_id", ""),
        "support_widget_custom_snippet": all_db.get("support_widget_custom_snippet", ""),
        "singra_widget_install_configured": bool(singra_install.resolve_install_id()),
        "singra_widget_install_masked": _mask_secret(singra_install.resolve_install_id()),
        "singra_widget_install_source": singra_install.current_source(),
        "singra_webhook_secret_configured": bool(singra_secret.resolve_secret()),
        "singra_webhook_secret_source": singra_secret.current_source(),
        "updates_automatic": all_db.get("updates_automatic", "false") == "true",
        "desktop_app_download_enabled": all_db.get("desktop_app_download_enabled", "true") != "false",
        "calendar_enabled": all_db.get("calendar_enabled", "true") != "false",
        "notes_enabled": all_db.get("notes_enabled", "true") != "false",
        "captcha_enabled": all_db.get("captcha_enabled", "false") == "true",
        "captcha_provider": all_db.get("captcha_provider", "none"),
        "captcha_site_key": all_db.get("captcha_site_key", ""),
        "captcha_secret_key": _mask_secret(
            AuthService.decrypt_secret(
                all_db.get("captcha_secret_key_encrypted", ""),
                aad="msm:settings:captcha_secret_key"
            ) if all_db.get("captcha_secret_key_encrypted", "") else all_db.get("captcha_secret_key", "")
        ),
        "cloudflare_enabled": all_db.get("cloudflare_enabled", "true") != "false",
        "cloudflare_api_token": _mask_secret(cf_token),
        "cloudflare_api_configured": bool(cf_token),
        "cloudflare_api_source": cfl_st.get("source", "none"),
        "cloudflare_default_zone": all_db.get("cloudflare_default_zone", ""),
        "proactive_enabled": True,
        # Rate-Limits: immer resolved (Default wenn unset/invalid), nie Rohmüll
        "rate_limit_auth": resolve_auth_limit(all_db.get(RATE_LIMIT_AUTH_KEY, "")),
        "rate_limit_global": resolve_global_limit(all_db.get(RATE_LIMIT_GLOBAL_KEY, "")),
    }


@router.get("/public", status_code=200)
def get_public_settings() -> dict:
    """Öffentlich abfragbare Panel-Einstellungen (z. B. Desktop-Download-Banner, Kalender, Notizen)."""
    all_db = PanelSettingsService.get_all()
    return {
        "desktop_app_download_enabled": all_db.get("desktop_app_download_enabled", "true") != "false",
        "imprint_enabled": all_db.get("imprint_enabled", "false") == "true",
        "imprint_url": all_db.get("imprint_url", ""),
        "calendar_enabled": all_db.get("calendar_enabled", "true") != "false",
        "notes_enabled": all_db.get("notes_enabled", "true") != "false",
    }


def github_token_status_dict() -> dict:
    """Wird sowohl vom GET als auch standalone GET /github-token genutzt."""
    st = github_token_status()
    return {
        "github_token_configured": bool(st["configured"]),
        "github_token_source": st["source"],
    }


def _is_masked(value: str) -> bool:
    """Prueft ob ein Wert maskiert ist (von GET /settings zurueckgegeben)."""
    return bool(value) and value.startswith("*")


def _validate_imprint_url(value: str) -> str:
    """Validiert die optionale Betreiber-Impressum-URL."""
    url = value.strip()
    if not url:
        return ""
    if len(url) > 2048 or any(ord(ch) < 32 for ch in url):
        raise HTTPException(status_code=400, detail="Ungueltige Impressum-URL")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Ungueltige Impressum-URL")
    return url


@router.post("", status_code=200)
def update_settings(
    req: PanelSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Speichert Panel-Einstellungen in der Datenbank.

    Maskierte Werte (****1234) werden ignoriert — der Admin muss das
    Passwort/API-Key explizit neu eingeben, um es zu aendern.
    """
    from services.auth_service import AuthService
    data = req.model_dump(exclude_none=True)
    for key, value in data.items():
        if key == "time_format" and value not in ("24h", "12h"):
            raise HTTPException(status_code=400, detail="Ungueltiges Zeitformat")
        if key == "imprint_url":
            value = _validate_imprint_url(str(value))
        if key == "imprint_enabled":
            value = "true" if bool(value) else "false"
        if key == "support_widget_enabled":
            value = "true" if bool(value) else "false"
        if key == "updates_automatic":
            value = "true" if bool(value) else "false"
        if key == "calendar_enabled":
            value = "true" if bool(value) else "false"
        if key == "notes_enabled":
            value = "true" if bool(value) else "false"
        if key == "captcha_enabled":
            value = "true" if bool(value) else "false"
        if key == "captcha_provider":
            mode = str(value).strip().lower()
            if mode not in ("none", "turnstile", "hcaptcha", "recaptcha"):
                raise HTTPException(status_code=400, detail="Ungueltiger CAPTCHA-Anbieter")
            value = mode
        if key == "support_widget_mode":
            mode = str(value).strip().lower()
            if mode not in ("singra", "crisp", "tawk", "custom"):
                raise HTTPException(status_code=400, detail="Ungueltiger Support-Widget-Anbieter")
            value = mode
        if key in (
            "support_widget_crisp_website_id",
            "support_widget_tawk_property_id",
            "support_widget_tawk_widget_id",
        ):
            wid = str(value).strip()
            if len(wid) > 128:
                raise HTTPException(status_code=400, detail="Wert zu lang")
            value = wid
        if key == "support_widget_custom_snippet":
            snippet = str(value)
            if len(snippet) > 8192:
                raise HTTPException(status_code=400, detail="Snippet zu lang")
            value = snippet
        # Rate-Limits: serverseitige Range-Validierung vor dem Schreiben.
        # Ungültige Werte → 400, gespeicherte Werte bleiben unverändert.
        if key == RATE_LIMIT_AUTH_KEY:
            try:
                value = validate_auth_limit(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if key == RATE_LIMIT_GLOBAL_KEY:
            try:
                value = validate_global_limit(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if _is_masked(str(value)):
            continue
        if key == "smtp_password":
            enc = AuthService.encrypt_secret(str(value), aad="msm:settings:smtp_password")
            PanelSettingsService.set("smtp_password_encrypted", enc)
            PanelSettingsService.set("smtp_password", "")  # Lösche legacy plain-text
        elif key == "resend_api_key":
            enc = AuthService.encrypt_secret(str(value), aad="msm:settings:resend_api_key")
            PanelSettingsService.set("resend_api_key_encrypted", enc)
            PanelSettingsService.set("resend_api_key", "")  # Lösche legacy plain-text
        elif key == "captcha_secret_key":
            enc = AuthService.encrypt_secret(str(value), aad="msm:settings:captcha_secret_key")
            PanelSettingsService.set("captcha_secret_key_encrypted", enc)
            PanelSettingsService.set("captcha_secret_key", "")  # Lösche legacy plain-text
        elif key == "cloudflare_enabled":
            value = "true" if bool(value) else "false"
            PanelSettingsService.set(key, str(value))
        elif key == "cloudflare_default_zone":
            value = str(value).strip()[:253]
            PanelSettingsService.set(key, value)
        elif key == "proactive_enabled":
            PanelSettingsService.set("proactive_enabled", "true")
        else:
            PanelSettingsService.set(key, str(value))

    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="panel.settings.update",
        target_type="setting",
        target_id="global",
        details={"keys": sorted(data.keys())},
        commit=True,
    )
    return {"message": "Einstellungen gespeichert"}


@router.post("/test-email", status_code=200)
async def test_email(
    req: TestEmailRequest,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Sendet eine Test-E-Mail mit den aktuellen Einstellungen."""
    if not EmailService.is_configured():
        raise HTTPException(status_code=503, detail="E-Mail nicht konfiguriert")

    body = "Dies ist eine Test-E-Mail vom Maunting Service Manager.\n\nDie E-Mail-Konfiguration funktioniert korrekt."
    html = EmailService._base_template(
        "Test-E-Mail",
        f"""<h1 class=\"headline\" style=\"margin:0 0 12px 0;font-size:24px;font-weight:700;color:{EmailService.CYAN_ACCENT};line-height:1.3;\">Test-E-Mail</h1>
<p style=\"margin:0 0 20px 0;font-size:15px;color:{EmailService.SECONDARY_TEXT};line-height:1.6;\">Dies ist eine Test-E-Mail vom Maunting Service Manager.</p>
<p style=\"margin:0 0 20px 0;font-size:15px;color:{EmailService.PRIMARY_TEXT};line-height:1.6;\">Die E-Mail-Konfiguration funktioniert korrekt.</p>"""
    )

    ok = await EmailService.send_email(req.to, "Maunting Service Manager — Test", body, html)
    if not ok:
        raise HTTPException(status_code=503, detail="E-Mail konnte nicht versendet werden")
    return {"message": "Test-E-Mail gesendet"}


# ------------------------------------------------------------------
# Secure .env update for Resend API key
# ------------------------------------------------------------------

_ENV_PATH = Path(".env")


def _update_env_file(key: str, value: str) -> None:
    """Safely updates a single key in .env without destroying other variables.

    Raises OSError if the file is not writable.
    """
    if not _ENV_PATH.exists():
        raise OSError(".env file not found")

    content = _ENV_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()
    pattern = re.compile(rf'^{re.escape(key)}\s*=\s*')

    updated = False
    new_lines = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(f'{key}="{value}"')
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f'{key}="{value}"')

    # Ensure trailing newline
    final = "\n".join(new_lines) + "\n"
    _ENV_PATH.write_text(final, encoding="utf-8")


@router.post("/resend-key", status_code=200)
def set_resend_key(
    req: ResendKeyRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Stores the Resend API key securely in the panel database (DIS-encrypted)."""
    if not req.resend_api_key.startswith("re_"):
        raise HTTPException(status_code=400, detail="Ungueltiger Resend API-Key")

    enc = AuthService.encrypt_secret(req.resend_api_key, aad="msm:email:resend_api_key")
    PanelSettingsService.set("resend_api_key_encrypted", enc)
    PanelSettingsService.set("resend_api_key", "")

    return {"message": "Resend API-Key gespeichert"}


@router.post("/steam-key", status_code=200)
def update_steam_key(
    req: SteamApiKeyRequest,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Stores the Steam Web API key securely in the panel database (DIS-encrypted)."""
    key = req.steam_api_key.strip()
    if not key or len(key) < 10:
        raise HTTPException(status_code=400, detail="Ungültiger Steam API-Key")

    set_panel_key(key)

    return {"message": "Steam API-Key gespeichert", "steam_api_source": steam_api_source()}


@router.post("/steam-account", status_code=200)
def update_steam_account(
    req: SteamAccountRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    try:
        SteamAccountService.set(req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Steam-Account gespeichert"}


@router.delete("/steam-account", status_code=200)
def delete_steam_account(
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    SteamAccountService.clear()
    return {"message": "Steam-Account entfernt"}


@router.post("/steam-key/test", status_code=200)
async def test_steam_key(
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.read")),
) -> dict:
    """Tests whether the configured Steam API key is valid."""
    import httpx

    key = resolve_steam_api_key()
    if not key:
        raise HTTPException(status_code=400, detail="Kein Steam API-Key konfiguriert")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.steampowered.com/ISteamWebAPIUtil/GetSupportedAPIList/v1/",
                params={"key": key},
            )
            if resp.status_code == 200:
                return {"message": "Steam API-Key ist gültig", "valid": True}
            else:
                return {"message": "Steam API-Key ist ungültig", "valid": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Test fehlgeschlagen: {e}")


@router.post("/curseforge-api-key", status_code=200)
def update_curseforge_api_key(
    req: CurseForgeApiKeyRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Stores the CurseForge API key securely in panel database (DIS-encrypted)."""
    key = req.curseforge_api_key.strip()
    if not key or len(key) < 10:
        raise HTTPException(status_code=400, detail="Ungültiger CurseForge API-Key")

    set_curseforge_panel_key(key)

    return {"message": "CurseForge API-Key gespeichert", "curseforge_api_source": curseforge_api_source()}


@router.delete("/curseforge-api-key", status_code=200)
def delete_curseforge_api_key(
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Removes the CurseForge API key from panel database."""
    clear_curseforge_panel_key()
    return {"message": "CurseForge API-Key entfernt", "curseforge_api_source": curseforge_api_source()}


@router.post("/cloudflare-token", status_code=200)
def set_cloudflare_token(
    req: CloudflareApiTokenRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Stores the Cloudflare API token securely in panel database (DIS-encrypted)."""
    key = req.cloudflare_api_token.strip()
    if not key or len(key) < 10:
        raise HTTPException(status_code=400, detail="Ungültiger Cloudflare API-Token")
    if any(c in key for c in ("\n", "\r", "\0")):
        raise HTTPException(status_code=400, detail="Ungültige Zeichen")
    if len(key) > 512:
        raise HTTPException(status_code=400, detail="Token zu lang")

    set_cloudflare_panel_key(key)
    return {"message": "Cloudflare API-Token gespeichert", **cloudflare_api_status()}


@router.delete("/cloudflare-token", status_code=200)
def delete_cloudflare_token(
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Removes the Cloudflare API token from panel database."""
    clear_cloudflare_panel_key()
    return {"message": "Cloudflare API-Token entfernt", **cloudflare_api_status()}


@router.post("/cloudflare-token/test", status_code=200)
async def test_cloudflare_token(
    _=Depends(require_global("panel.settings.read")),
) -> dict:
    from services.cloudflare_service import test_connection

    res = await test_connection()
    if res.get("ok"):
        return {"message": "Cloudflare API-Token ist gültig", "valid": True}
    return {"message": "Cloudflare API-Token pruefen fehlgeschlagen", "valid": False}


@router.get("/cloudflare-zones", status_code=200)
async def list_cloudflare_zones(
    _=Depends(require_global("panel.settings.read")),
) -> dict:
    from services.cloudflare_service import list_zones
    from services import permission_service
    from database import get_db as _get_db
    from dependencies import get_current_user

    try:
        zones = await list_zones()
        return {"zones": [{"id": z.get("id"), "name": z.get("name"), "status": z.get("status")} for z in zones]}
    except Exception:
        raise HTTPException(status_code=500, detail="Cloudflare Zonen konnten nicht geladen werden")


@router.post("/curseforge-key/test", status_code=200)
async def test_curseforge_key(
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.read")),
) -> dict:
    """Tests whether the configured CurseForge API key is valid."""
    key = resolve_curseforge_api_key()
    if not key:
        raise HTTPException(status_code=400, detail="Kein CurseForge API-Key konfiguriert")

    from services.curseforge_service import get_curseforge_service

    svc = await get_curseforge_service()
    res = await svc.test_connection()
    if res.get("ok"):
        return {"message": "CurseForge API-Key ist gültig", "valid": True}
    else:
        return {"message": f"CurseForge API-Key ist ungültig: {res.get('error')}", "valid": False}


# ------------------------------------------------------------------
# GitHub Personal Access Token (für source.type=github Blueprints)
# ------------------------------------------------------------------


@router.get("/github-token", response_model=GitHubTokenStatus)
def get_github_token(
    _=Depends(require_global("panel.settings.read")),
) -> dict:
    """Liefert nur Status (``configured``, ``source``) — niemals das Token selbst."""
    return github_token_status()


@router.post("/github-token", status_code=200)
def set_github_token(
    req: GitHubTokenRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Speichert ein GitHub-PAT in den Panel-Settings (DB).

    Format-Hinweis (keine harte Validierung, GitHub-Forge entscheidet):
    - Klassischer PAT: ``ghp_…`` oder ``gho_…`` mit ``repo``-Scope
    - Fine-grained: ``github_pat_…`` mit ``Contents: read``

    Liegt zusätzlich ``MSM_GITHUB_CLONE_TOKEN`` vor, gewinnt die ENV-Variable.
    """
    token = (req.github_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token darf nicht leer sein")
    if any(c in token for c in ("\n", "\r", "\0")):
        raise HTTPException(status_code=400, detail="Token enthält ungültige Zeichen")
    if len(token) > 512:
        raise HTTPException(status_code=400, detail="Token zu lang")
    set_github_panel_token(token)
    return {"message": "GitHub-Token gespeichert", **github_token_status_dict()}


@router.delete("/github-token", status_code=200)
def delete_github_token(
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Entfernt das panel-gesetzte PAT. ENV-Variablen bleiben unberührt."""
    clear_github_panel_token()
    return {"message": "GitHub-Token entfernt", **github_token_status_dict()}


@router.post("/github-token/test", status_code=200)
async def test_github_token(
    _=Depends(require_global("panel.settings.read")),
) -> dict:
    """Prüft das aktive GitHub-PAT gegen die ``/user``-API.

    Bei ungültigem/abgelaufenem Token antwortet GitHub mit 401.
    """
    import httpx

    from services.github_token_service import resolve_token

    token = resolve_token()
    if not token:
        raise HTTPException(status_code=400, detail="Kein GitHub-Token konfiguriert")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "msm-panel",
                },
            )
            if resp.status_code == 200:
                login = (resp.json() or {}).get("login", "?")
                return {"message": f"GitHub-Token ist gültig (login: {login})", "valid": True}
            if resp.status_code == 401:
                return {"message": "GitHub-Token ist ungültig oder abgelaufen", "valid": False}
            return {"message": f"GitHub-API unerwartet: HTTP {resp.status_code}", "valid": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Test fehlgeschlagen: {e}")


# ------------------------------------------------------------------
# Singra support widget (installation ID + inbound webhook secret)
# ------------------------------------------------------------------


@router.post("/singra-widget-install-id", status_code=200)
def set_singra_widget_install_id(
    req: SingraWidgetInstallIdRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Speichert die Widget-Installations-ID (DIS-verschlüsselt, wie Steam API-Key)."""
    raw = (req.install_id or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Installations-ID darf nicht leer sein")
    if any(c in raw for c in ("\n", "\r", "\0")):
        raise HTTPException(status_code=400, detail="Ungültige Zeichen")
    if len(raw) > 256:
        raise HTTPException(status_code=400, detail="Installations-ID zu lang")
    try:
        singra_install.set_panel_install_id(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Installations-ID ungültig")
    return {
        "message": "Widget-Installations-ID gespeichert",
        "configured": True,
        "source": singra_install.current_source(),
    }


@router.delete("/singra-widget-install-id", status_code=200)
def delete_singra_widget_install_id(
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    if singra_install.current_source() == "env":
        raise HTTPException(status_code=400, detail="Installations-ID wird per Umgebungsvariable verwaltet")
    singra_install.clear_panel_install_id()
    return {"message": "Widget-Installations-ID entfernt", **singra_install.status()}


@router.post("/singra-webhook-secret", status_code=200)
def set_singra_webhook_secret(
    req: SingraWebhookSecretRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Secret aus dem Singra-Widget-Panel (dort „Secret rotieren“) hier hinterlegen."""
    if singra_secret.current_source() == "env":
        raise HTTPException(
            status_code=400,
            detail="Webhook-Secret wird per Umgebungsvariable verwaltet (SINGRA_WEBHOOK_SECRET)",
        )
    raw = (req.webhook_secret or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Secret darf nicht leer sein")
    if any(c in raw for c in ("\n", "\r", "\0")):
        raise HTTPException(status_code=400, detail="Ungültige Zeichen")
    if len(raw) > 512:
        raise HTTPException(status_code=400, detail="Secret zu lang")
    try:
        singra_secret.set_panel_secret(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Secret ungültig")
    return {"message": "Webhook-Secret gespeichert", **singra_secret.status()}


@router.delete("/singra-webhook-secret", status_code=200)
def delete_singra_webhook_secret(
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    if singra_secret.current_source() == "env":
        raise HTTPException(status_code=400, detail="Webhook-Secret wird per Umgebungsvariable verwaltet")
    singra_secret.clear_panel_secret()
    return {"message": "Webhook-Secret entfernt", **singra_secret.status()}


@router.post("/singra-webhook-secret/rotate", status_code=200)
def rotate_singra_webhook_secret(
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Erzeugt ein neues Webhook-Secret (nur Panel-DB; ENV bleibt unberührt)."""
    if singra_secret.current_source() == "env":
        raise HTTPException(
            status_code=400,
            detail="Webhook-Secret wird per Umgebungsvariable verwaltet",
        )
    plain = singra_secret.rotate_panel_secret()
    return {
        "message": "Webhook-Secret rotiert",
        "secret": plain,
        **singra_secret.status(),
    }


@router.post("/singra-webhook/test", status_code=200)
async def test_singra_webhook(
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.read")),
    __=Depends(verify_csrf),
) -> dict:
    """Verarbeitet ein synthetisches webhook_test-Event (Signaturprüfung inklusive)."""
    import hashlib
    import hmac
    import json
    from datetime import datetime, timezone

    from services.singra_webhook_handler import handle_verified_payload, verify_request

    secret = singra_secret.resolve_secret()
    if not secret:
        raise HTTPException(status_code=400, detail="Webhook-Secret nicht konfiguriert")

    payload = {
        "event": "webhook_test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "ticketId": "00000000-0000-0000-0000-000000000000",
            "guestName": "MSM Test",
            "guestEmail": None,
            "subject": "Webhook test",
            "message": "Synthetic webhook_test from MSM panel settings.",
            "isStaff": False,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        },
    }
    body = json.dumps(payload, separators=(",", ":"))
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    err = verify_request(body.encode("utf-8"), ts, f"sha256={signature}")
    if err:
        return {"valid": False, "message": err}
    await handle_verified_payload(db, event_type="webhook_test", payload=payload)
    return {"valid": True, "message": "Webhook-Signatur und Verarbeitung OK"}
