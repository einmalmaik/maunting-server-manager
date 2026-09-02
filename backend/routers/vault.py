"""REST-Router für den blinden, verschlüsselten Zero-Knowledge Passwort-Manager."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_owner, get_current_user
from models.user import User
from schemas.vault import (
    VaultNodeAssignment,
    VaultSyncRequest,
    VaultSyncResponse,
)
from services import vault_service

router = APIRouter(prefix="/api/vault", tags=["vault"])


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
