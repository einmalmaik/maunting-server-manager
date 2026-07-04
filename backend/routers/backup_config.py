"""Backup Config Router — S3-Konfiguration und Backup-Passwort Endpunkte.

Alle Endpunkte erfordern panel.settings.write (Admin-only). Write-Endpunkte
zusaetzlich CSRF-Schutz. Credentials werden in GET-Antworten maskiert
(letzte 4 Zeichen). Das Backup-Passwort wird in KEINER API-Antwort
zurueckgegeben (write-only).

Sicherheits-Invarianten:
- Admin-only (panel.settings.write) auf allen Endpunkten.
- CSRF auf allen Write-Endpunkten.
- Keine Credentials/Passwoerter in Logs oder Fehlermeldungen.
- Generische Fehlermeldungen (kein Pfad/Secret-Leak).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from dependencies import require_global, verify_csrf
from services.backup_config_service import BackupConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup-config", tags=["backup-config"])


# ── Request/Response Schemas ─────────────────────────────────────────────


class S3ConfigRequest(BaseModel):
    """S3-Konfiguration. Endpoint und Region optional, Rest required."""
    endpoint: str = ""  # Optional (AWS S3 braucht keinen)
    access_key: str
    secret_key: str
    bucket: str
    region: str = ""  # Optional

    @field_validator("access_key", "secret_key", "bucket")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("darf nicht leer sein")
        return v.strip()


class S3ConfigResponse(BaseModel):
    endpoint: str
    access_key: str  # maskiert
    secret_key: str  # maskiert
    bucket: str
    region: str


class PasswordRequest(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Passwort darf nicht leer sein")
        return v


class StatusResponse(BaseModel):
    s3_configured: bool
    backup_password_set: bool
    last_panel_backup: str | None


class TestS3Response(BaseModel):
    ok: bool
    message: str = ""
    bucket: str | None = None


# ── Endpunkte ────────────────────────────────────────────────────────────


@router.get("", response_model=S3ConfigResponse)
def get_backup_config(_=Depends(require_global("panel.settings.write"))) -> dict:
    """Gibt S3-Config zurueck (Credentials maskiert, letzte 4 Zeichen)."""
    return BackupConfigService.get_s3_config()


@router.post("/s3", status_code=200)
def set_s3_config(
    req: S3ConfigRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Speichert S3-Config. Credentials verschluesselt via DIS (AAD=s3)."""
    BackupConfigService.set_s3_config(
        endpoint=req.endpoint,
        access_key=req.access_key,
        secret_key=req.secret_key,
        bucket=req.bucket,
        region=req.region or None,
    )
    return {"message": "S3-Konfiguration gespeichert"}


@router.post("/test-s3", status_code=200)
def test_s3_connection(
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Testet die S3-Verbindung mit den gespeicherten Credentials.

    Read-only (head_bucket). Gibt generische Fehlermeldungen zurueck
    (kein Credential-Leak).
    """
    if not BackupConfigService.is_s3_configured():
        raise HTTPException(
            status_code=400,
            detail="S3 ist nicht konfiguriert. Bitte zuerst S3-Einstellungen setzen.",
        )
    # Inline-Import gegen Zyklen (s3_service importiert panel_settings_service).
    from services.s3_service import S3NotConfiguredError, S3OperationError, S3Service

    try:
        result = S3Service.test_connection()
        return {"ok": True, "message": "Verbindung erfolgreich", "bucket": result.get("bucket")}
    except S3NotConfiguredError:
        raise HTTPException(
            status_code=400,
            detail="S3 ist nicht konfiguriert. Bitte zuerst S3-Einstellungen setzen.",
        )
    except S3OperationError as e:
        # Generische Nachricht — keine Credentials im String.
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        # Catch-All mit generischer Nachricht (kein Stacktrace/Path-Leak).
        logger.warning("S3 Verbindungstest fehlgeschlagen (unerwarteter Fehler)")
        raise HTTPException(status_code=502, detail="S3-Verbindungstest fehlgeschlagen")


@router.post("/password", status_code=200)
def set_backup_password(
    req: PasswordRequest,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Setzt das Backup-Passwort (verschluesselt via DIS, AAD=pw).

    Das Passwort wird in KEINER API-Antwort zurueckgegeben (write-only).
    Salt wird beim ersten Setzen generiert und bei Aenderung reused.
    """
    BackupConfigService.set_backup_password(req.password)
    return {"message": "Backup-Passwort gespeichert"}


@router.get("/status", response_model=StatusResponse)
def get_backup_status(_=Depends(require_global("panel.settings.write"))) -> dict:
    """Gibt Backup-System-Status zurueck (s3_configured, password_set, last_panel_backup)."""
    return BackupConfigService.get_status()
