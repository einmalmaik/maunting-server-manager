import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from dependencies import require_global, verify_csrf
from schemas.panel_settings import PanelSettingsResponse, PanelSettingsUpdate, TestEmailRequest, ResendKeyRequest, SteamApiKeyRequest
from services.panel_settings_service import PanelSettingsService
from services.email_service import EmailService

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
    steam_key = settings.steam_api_key or os.getenv("STEAM_API_KEY", "")
    return {
        "panel_url": all_db.get("panel_url", ""),
        "smtp_host": all_db.get("smtp_host", ""),
        "smtp_port": all_db.get("smtp_port", "587"),
        "smtp_user": all_db.get("smtp_user", ""),
        "smtp_password": _mask_secret(all_db.get("smtp_password", "")),
        "smtp_from": all_db.get("smtp_from", ""),
        "smtp_tls": all_db.get("smtp_tls", "true"),
        "resend_api_key": _mask_secret(all_db.get("resend_api_key", "")),
        "default_language": all_db.get("default_language", "de"),
        "email_configured": EmailService.is_configured(),
        "email_provider": EmailService._get_provider(),
        "steam_api_key": _mask_secret(steam_key),
        "steam_api_configured": bool(steam_key),
    }


def _is_masked(value: str) -> bool:
    """Prueft ob ein Wert maskiert ist (von GET /settings zurueckgegeben)."""
    return bool(value) and value.startswith("*")


@router.post("", status_code=200)
def update_settings(
    req: PanelSettingsUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Speichert Panel-Einstellungen in der Datenbank.

    Maskierte Werte (****1234) werden ignoriert — der Admin muss das
    Passwort/API-Key explizit neu eingeben, um es zu aendern.
    """
    data = req.model_dump(exclude_none=True)
    for key, value in data.items():
        if _is_masked(str(value)):
            continue
        PanelSettingsService.set(key, str(value))
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

    body = "Dies ist eine Test-E-Mail vom Maunting Server Manager.\n\nDie E-Mail-Konfiguration funktioniert korrekt."
    html = EmailService._base_template(
        "Test-E-Mail",
        f"""<h1 class=\"headline\" style=\"margin:0 0 12px 0;font-size:24px;font-weight:700;color:{EmailService.CYAN_ACCENT};line-height:1.3;\">Test-E-Mail</h1>
<p style=\"margin:0 0 20px 0;font-size:15px;color:{EmailService.SECONDARY_TEXT};line-height:1.6;\">Dies ist eine Test-E-Mail vom Maunting Server Manager.</p>
<p style=\"margin:0 0 20px 0;font-size:15px;color:{EmailService.PRIMARY_TEXT};line-height:1.6;\">Die E-Mail-Konfiguration funktioniert korrekt.</p>"""
    )

    ok = await EmailService.send_email(req.to, "Maunting Server Manager — Test", body, html)
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
def update_resend_key(
    req: ResendKeyRequest,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Stores the Resend API key securely in .env instead of the database.

    Deletes any DB override so the .env value takes effect immediately.
    """
    if not req.resend_api_key.startswith("re_"):
        raise HTTPException(status_code=400, detail="Ungueltiger Resend API-Key")

    try:
        _update_env_file("MSM_RESEND_API_KEY", req.resend_api_key)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f".env Update fehlgeschlagen: {e}")

    # Remove DB override so .env takes precedence
    PanelSettingsService.set("resend_api_key", "")

    # Update in-memory settings for immediate effect (no restart required)
    settings.__dict__["resend_api_key"] = req.resend_api_key
    os.environ["MSM_RESEND_API_KEY"] = req.resend_api_key

    return {"message": "Resend API-Key gespeichert"}


@router.post("/steam-key", status_code=200)
def update_steam_key(
    req: SteamApiKeyRequest,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Stores the Steam Web API key securely in .env."""
    key = req.steam_api_key.strip()
    if not key or len(key) < 10:
        raise HTTPException(status_code=400, detail="Ungueltiger Steam API-Key")

    try:
        _update_env_file("STEAM_API_KEY", key)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f".env Update fehlgeschlagen: {e}")

    # Update in-memory for immediate effect
    settings.__dict__["steam_api_key"] = key
    os.environ["STEAM_API_KEY"] = key

    return {"message": "Steam API-Key gespeichert"}


@router.post("/steam-key/test", status_code=200)
async def test_steam_key(
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.read")),
) -> dict:
    """Tests whether the configured Steam API key is valid."""
    import httpx

    key = settings.steam_api_key or os.getenv("STEAM_API_KEY", "")
    if not key:
        raise HTTPException(status_code=400, detail="Kein Steam API-Key konfiguriert")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.steampowered.com/ISteamWebAPIUtil/GetSupportedAPIList/v1/",
                params={"key": key},
            )
            if resp.status_code == 200:
                return {"message": "Steam API-Key ist gueltig", "valid": True}
            else:
                return {"message": "Steam API-Key ist ungueltig", "valid": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Test fehlgeschlagen: {e}")
