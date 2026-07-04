"""Panel Backups Router — Panel Self-Backup Endpunkte.

Dieser Router enthaelt zunaechst den POST-Endpunkt zur Erstellung eines
Panel-Backups (M3 panel-backup-service). List/Delete/Settings/Prepare-Restore
werden in separaten Features ergaenzt.

Alle Endpunkte erfordern panel.settings.write (Admin-only). Write-Endpunkte
zusaetzlich CSRF-Schutz.

Sicherheits-Invarianten:
- Admin-only (panel.settings.write) auf allen Endpunkten.
- CSRF auf allen Write-Endpunkten.
- Keine Secrets/Pfade in Logs oder Fehlermeldungen (generische Messages).
- Response enthaelt keine sensitiven Pfade (local_path, s3_key).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from schemas.panel_backup import PanelBackupCreateRequest, PanelBackupResponse
from services.panel_backup_service import create_panel_backup

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/panel-backups", tags=["panel-backups"])


@router.post("", response_model=PanelBackupResponse, status_code=201)
def create_panel_backup_endpoint(
    db: Session = Depends(get_db),
    req: PanelBackupCreateRequest | None = None,
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Erstellt ein Panel-Backup (DB-Dump + Configs + S3-Upload).

    Admin-only (panel.settings.write) + CSRF.

    Bei DB-Dump-Fehler (pg_dump/sqlite3) wird 500 zurueckgegeben — kein
    partieller Backup-Record wird angelegt. S3/DIS-Fehler blockieren nicht
    das lokale Backup (Best-Effort).
    """
    # Body ist optional (kein Name erforderlich).
    name = None
    if req is not None and req.name:
        name = req.name.strip() or None

    try:
        backup = create_panel_backup(db, name=name)
        return {
            "id": backup.id,
            "name": backup.name,
            "size_mb": backup.size_mb,
            "db_type": backup.db_type,
            "encrypted": backup.encrypted,
            "created_at": backup.created_at,
        }
    except Exception as exc:
        # Generische Fehlermeldung — kein Pfad/Secret-Leak.
        logger.warning("Panel-Backup-Erstellung fehlgeschlagen: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="Panel-Backup konnte nicht erstellt werden. Siehe Server-Logs.",
        )
