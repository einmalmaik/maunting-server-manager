"""REST-Router für den blinden, verschlüsselten Zero-Knowledge Passwort-Manager."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_owner, get_current_user
from models.user import User
from schemas.vault import (
    VaultHintSetRequest,
    VaultHintStatusResponse,
    VaultNodeAssignment,
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
) -> VaultSyncResponse:
    """Synchronisiert verschlüsselte Tresor-Einträge mit dem Server.

    CRITICAL SECURITY INVARIANTS:
    - Der Server verarbeitet ausschließlich Ciphertext (`sv-vault-v1:`).
    - Es werden keine Klardaten, URLs, Passwörter oder Tags übertragen.
    - Die `bucket_id` ist blind und wird clientseitig berechnet.
    """
    _check_vault_enabled()
    try:
        return vault_service.sync_vault(db, payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Fehler bei der Tresor-Synchronisation: {str(exc)}",
        ) from exc


@router.get("/node-assignment", response_model=VaultNodeAssignment)
def get_node_assignment(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> VaultNodeAssignment:
    """Liefert die Multi-Node Zuweisung für den Passwort-Manager."""
    return vault_service.get_vault_node_assignment(db)


@router.put("/node-assignment", response_model=VaultNodeAssignment)
def set_node_assignment(
    payload: VaultNodeAssignment,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_owner),
) -> VaultNodeAssignment:
    """Weist den Passwort-Manager einem dedizierten Node zu (nur Owner)."""
    try:
        return vault_service.set_vault_node_assignment(db, payload.node_id)
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

