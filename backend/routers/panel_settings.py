from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_owner, verify_csrf
from schemas.panel_settings import PanelSettingsResponse, PanelSettingsUpdate, TestEmailRequest
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
def get_settings(db: Session = Depends(get_db), _=Depends(get_current_owner)) -> dict:
    """Liest alle Panel-Einstellungen (DB-Werte mit Fallback auf Defaults).

    Passwoerter und API-Keys werden maskiert zurueckgegeben.
    """
    all_db = PanelSettingsService.get_all()
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
    }


def _is_masked(value: str) -> bool:
    """Prueft ob ein Wert maskiert ist (von GET /settings zurueckgegeben)."""
    return bool(value) and value.startswith("*")


@router.post("", status_code=200)
def update_settings(
    req: PanelSettingsUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_owner),
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
    _=Depends(get_current_owner),
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
