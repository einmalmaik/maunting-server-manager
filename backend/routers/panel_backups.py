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
    PanelBackupSettings,
    PanelBackupSettingsPatch,
    PanelRestorePrepResponse,
)
from services.panel_backup_service import (
    create_panel_backup,
    delete_panel_backup,
    get_panel_backup_settings,
    update_panel_backup_settings,
)
from services.panel_backup_service import (
    PanelRestoreDecryptError,
    PanelRestoreNotFoundError,
    PanelRestoreNoArchiveError,
    prepare_panel_restore,
)

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

    Bei DB-Dump-Fehler (pg_dump) wird 500 zurueckgegeben — kein
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


@router.get("/settings", response_model=PanelBackupSettings)
def get_panel_backup_settings_endpoint(
    _=Depends(require_global("panel.settings.write")),
) -> dict:
    """Gibt Panel-Backup-Settings zurueck (enabled, interval_hours, retention_count).

    Admin-only (panel.settings.write). Keine CSRF-Pruefung (GET ist read-only).

    Defaults bei fehlenden Werten: enabled=False, interval_hours=24,
    retention_count=7 (VAL-PANEL-SETTINGS-001).

    Response enthaelt KEINE Secrets (S3-Credentials, Passwort, Salt) —
    nur nicht-sensitive Scheduler/Retention-Konfiguration (VAL-PANEL-SETTINGS-003).
    """
    return get_panel_backup_settings()


@router.patch("/settings", response_model=PanelBackupSettings)
def patch_panel_backup_settings_endpoint(
    req: PanelBackupSettingsPatch,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Aktualisiert Panel-Backup-Settings (partial PATCH).

    Admin-only (panel.settings.write) + CSRF.

    Validierung (VAL-PANEL-SETTINGS-002):
      - interval_hours > 0 (sonst 400)
      - retention_count >= 1 (sonst 400)
    Partial PATCH: nur angegebene Felder werden aktualisiert.

    Nach erfolgreicher Aktualisierung wird der Scheduler live rescheduled
    (VAL-PANEL-SCHED-004): enabled=False entfernt den Job, Aenderung von
    interval_hours rescheduled den bestehenden Job.
    """
    from services.scheduler_service import sync_panel_backup_schedule

    try:
        updated = update_panel_backup_settings(
            enabled=req.enabled,
            interval_hours=req.interval_hours,
            retention_count=req.retention_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Scheduler live reschedule/remove (VAL-PANEL-SCHED-004).
    try:
        sync_panel_backup_schedule()
    except Exception as exc:
        logger.warning("Scheduler reschedule failed: %s", type(exc).__name__)

    return updated


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


@router.post(
    "/{backup_id}/prepare-restore",
    response_model=PanelRestorePrepResponse,
    status_code=200,
)
def prepare_restore_endpoint(
    backup_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.settings.write")),
    __=Depends(verify_csrf),
) -> dict:
    """Bereitet Panel-Restore vor (Download + Decrypt + Script-Generierung).

    Admin-only (panel.settings.write) + CSRF.

    Ablauf:
    - Lokale Datei vorhanden: direkt verwenden (kein S3, kein Decrypt).
    - Lokal fehlt, s3_key vorhanden: von S3 downloaden, via DIS entschluesseln.
    - Beides fehlt: 404 (keine Archiv-Quelle).

    Generiert ein ausfuehrbares bash-Script im Panel-Backup-Verzeichnis und
    gibt dessen Pfad sowie deutsche Anweisungen (mit sudo bash und Warnung)
    zurueck.

    Bei Entschluesselungsfehler (falsches Passwort): 400 mit klarer Meldung.
    Backup-Key wird immer invalidiert (try/finally, success und failure).
    """
    try:
        result = prepare_panel_restore(backup_id, db)
        return result
    except PanelRestoreNotFoundError:
        raise HTTPException(status_code=404, detail="Panel-Backup nicht gefunden")
    except PanelRestoreNoArchiveError:
        raise HTTPException(
            status_code=404,
            detail="Keine Archiv-Quelle verfuegbar (lokal und S3 fehlen)",
        )
    except PanelRestoreDecryptError:
        # Falsches Passwort / manipulierter Stream — klare Meldung, keine Secrets.
        raise HTTPException(
            status_code=400,
            detail="Entschluesselung fehlgeschlagen — Backup-Passwort pruefen",
        )
    except Exception as exc:
        logger.warning(
            "Panel-Restore-Vorbereitung fehlgeschlagen: %s", type(exc).__name__
        )
        raise HTTPException(
            status_code=500,
            detail="Restore-Vorbereitung fehlgeschlagen. Siehe Server-Logs.",
        )
