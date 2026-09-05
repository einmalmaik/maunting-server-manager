"""REST-Router für den blinden, verschlüsselten Zero-Knowledge Passwort-Manager."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, verify_csrf
from models.user import User
from schemas.vault import (
    VaultHintSetRequest,
    VaultHintStatusResponse,
    VaultSaltResponse,
    VaultSaltSetRequest,
    VaultSyncRequest,
    VaultSyncResponse,
)
from services import vault_service
from services.panel_settings_service import PanelSettingsService

router = APIRouter(prefix="/api/vault", tags=["vault"])


def _check_vault_enabled() -> None:
    if PanelSettingsService.get("vault_enabled", "true") == "false":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Der Passwort-Manager ist in den Panel-Einstellungen deaktiviert.",
        )


@router.post("/sync", response_model=VaultSyncResponse)
def sync_vault_entries(
    payload: VaultSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> VaultSyncResponse:
    """Synchronisiert verschlüsselte Tresor-Einträge mit dem Server.

    CRITICAL SECURITY INVARIANTS:
    - Der Server verarbeitet ausschließlich Ciphertext (`sv-vault-v1:`).
    - Es werden keine Klardaten, URLs, Passwörter oder Tags übertragen.
    - Die `bucket_id` ist an das autorisierte Benutzerkonto gebunden (SEC-02).
    - Double-Submit CSRF-Schutz via `verify_csrf` (SEC-09).
    """
    _check_vault_enabled()
    try:
        return vault_service.sync_vault(db, current_user, payload)
    except vault_service.VaultBucketAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Fehler bei der Tresor-Synchronisation: {str(exc)}",
        ) from exc


@router.get("/salt", response_model=VaultSaltResponse)
def get_vault_salt(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VaultSaltResponse:
    """Ruft den am Benutzerkonto hinterlegten KDF-Salt für Multi-Device-Sync ab (SEC-04)."""
    _check_vault_enabled()
    return vault_service.get_vault_salt(db, current_user.id)


@router.post("/salt", response_model=VaultSaltResponse)
def set_vault_salt(
    payload: VaultSaltSetRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> VaultSaltResponse:
    """Hinterlegt den KDF-Salt des Benutzers beim initialen Setup (SEC-04, SEC-09)."""
    _check_vault_enabled()
    try:
        return vault_service.set_vault_salt(
            db,
            current_user.id,
            payload.kdf_salt,
            payload.bucket_id,
        )
    except vault_service.VaultBucketAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/hint")
def save_vault_hint(
    payload: VaultHintSetRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Hinterlegt einen Passwort-Hinweis für den Passwort-Manager."""
    _check_vault_enabled()
    vault_service.set_vault_hint(db, current_user.id, payload.hint)
    return {"status": "ok", "message": "Passwort-Hinweis erfolgreich hinterlegt."}


@router.get("/hint-status", response_model=VaultHintStatusResponse)
def get_vault_hint_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VaultHintStatusResponse:
    """Gibt den Status des Passwort-Hinweises und Cooldowns zurück."""
    _check_vault_enabled()
    return vault_service.get_vault_hint_status(db, current_user.id)


@router.post("/request-hint")
async def send_vault_hint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Sendet den hinterlegten Passwort-Hinweis an die E-Mail des Benutzers (max. 1x alle 10 Minuten)."""
    _check_vault_enabled()
    success, msg = await vault_service.request_vault_hint_email(db, current_user)
    if not success:
        # Falls Cooldown aktiv ist: 429 Too Many Requests
        if "10 Minuten" in msg:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    return {"status": "ok", "message": msg}
