"""Panel Backups Router — Panel Self-Backup Endpunkte.

Dieser Router enthaelt die Panel-Backup-Endpunkte:
- POST   /api/panel-backups          — Panel-Backup erstellen (M3 panel-backup-service)
- GET    /api/panel-backups          — Panel-Backups auflisten (sorted desc, keine sensitiven Pfade)
- DELETE /api/panel-backups/{id}     — Panel-Backup loeschen (lokal + S3 + DB, best-effort S3)

Prepare-Restore und Settings werden in separaten Features ergaenzt.

Alle Endpunkte erfordern panel.settings.write (Admin-only). Write-Endpunkte
zusaetzlich CSRF-Schutz.

Sicherheits-Invarianten:
- Admin-only (panel.settings.write) auf allen Endpunkten.
- CSRF auf allen Write-Endpunkten.
- Keine Secrets/Pfade in Logs oder Fehlermeldungen (generische Messages).
- Response enthaelt keine sensitiven Pfade (local_path, s3_key, s3_bucket).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import PanelBackup
from schemas.panel_backup import (
    PanelBackupCreateRequest,
    PanelBackupListItem,
    PanelBackupResponse,
)
from services.panel_backup_service import create_panel_backup, delete_panel_backup

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


@router.get("", response_model=list[PanelBackupListItem])
def list_panel_backups_endpoint(
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
) -> list[dict]:
    """Listet alle Panel-Backups auf (sortiert nach created_at desc).

    Admin-only (panel.settings.write). Keine CSRF-Pruefung (GET ist read-only).

    Response-Items enthalten KEINE sensitiven Pfade (local_path, s3_key,
    s3_bucket). s3_status ist ein nicht-sensitiver Indikator:
      - "cloud": verschluesselt in S3 hochgeladen
      - "local": nur lokal vorhanden
    """
    backups = (
        db.query(PanelBackup)
        .order_by(PanelBackup.created_at.desc())
        .all()
    )
    return [
        {
            "id": b.id,
            "name": b.name,
            "size_mb": b.size_mb,
            "db_type": b.db_type,
            "encrypted": b.encrypted,
            "s3_status": "cloud" if (b.encrypted and b.s3_key) else "local",
            "created_at": b.created_at,
        }
        for b in backups
    ]


@router.delete("/{backup_id}", status_code=200)
def delete_panel_backup_endpoint(
    backup_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Loescht ein Panel-Backup (lokal + S3 + DB, best-effort S3).

    Admin-only (panel.settings.write) + CSRF.

    Best-Effort S3: S3-Fehler blockieren nicht das lokale Loeschen.
    Idempotent: nicht-existente ID oder fehlende lokale Datei sind kein Fehler
    (gibt 200 mit deleted=False bzw. deleted=True zurueck).
    """
    deleted = delete_panel_backup(db, backup_id)
    if not deleted:
        # Idempotent — kein 404, da Loeschen einer nicht-existenten Ressource
        # den gewuenschten Endzustand (Ressource existiert nicht) herstellt.
        return {"deleted": False, "id": backup_id}
    return {"deleted": True, "id": backup_id}
