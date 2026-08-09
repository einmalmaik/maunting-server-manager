import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Backup, Server, User
from schemas import BackupResponse
from dependencies import get_current_user, verify_csrf, require_server_permission
from services.actor_context import ActorContext
from services.backup_restore_service import restore_server_backup
from config import settings

logger = logging.getLogger(__name__)


def _is_loopback_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if settings.debug and host == "testclient":
        return True
    return host in {"127.0.0.1", "::1", "localhost"}


class CreateBackupRequest(BaseModel):
    name: str | None = None


class BackupSettingsRequest(BaseModel):
    backup_on_start: bool | None = None
    backup_interval_hours: int | None = None
    backup_retention_count: int | None = None


class BackupSettingsResponse(BaseModel):
    backup_on_start: bool
    backup_interval_hours: int | None
    backup_retention_count: int

router = APIRouter(prefix="/api/backups", tags=["backups"])

# NOTE: Backup-Logik ist jetzt zentral in services/backup_service.py
# (Single Source of Truth). Frühere _run_backup / _cleanup / run_scheduled_backups entfernt.
@router.get("/{server_id}", response_model=list[BackupResponse])
def list_backups(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_server_permission(user, server_id, db, "server.backups.read")
    rows = db.query(Backup).filter(Backup.server_id == server_id).order_by(Backup.created_at.desc()).all()
    # local_exists pro Backup berechnen, damit das Frontend weiss, ob ein
    # Restore lokal moeglich ist oder nur aus der Cloud (S3) geht.
    return [
        BackupResponse(
            id=b.id,
            server_id=b.server_id,
            name=b.name,
            filename=b.filename,
            size_mb=b.size_mb,
            created_at=b.created_at,
            expires_at=b.expires_at,
            s3_key=b.s3_key,
            s3_bucket=b.s3_bucket,
            encrypted=b.encrypted,
            local_exists=os.path.exists(b.filename),
        )
        for b in rows
    ]


@router.post("/{server_id}")
def create_backup(server_id: int, body: CreateBackupRequest | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.backups.create")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    # Kein Duplikat-Check mehr (Single Source of Truth im Service); generische Fehlermeldung
    # (verhindert Leak von install_dir / Pfaden in HTTP-Details und Logs).
    from services.backup_orchestrator import create_server_backup
    try:
        backup = create_server_backup(server_id, db, name=body.name if body else None, timeout_seconds=600)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="Server-Verzeichnis existiert nicht. Ist der Server installiert?")
    except Exception:
        raise HTTPException(status_code=500, detail="Backup fehlgeschlagen")
    return {"message": "Backup erstellt", "backup_id": backup.id, "size_mb": backup.size_mb}


@router.get("/{server_id}/settings", response_model=BackupSettingsResponse)
def get_backup_settings(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_server_permission(user, server_id, db, "server.backups.read")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return BackupSettingsResponse(
        backup_on_start=server.backup_on_start,
        backup_interval_hours=server.backup_interval_hours,
        backup_retention_count=server.backup_retention_count,
    )


@router.get("/{server_id}/status")
def get_backup_status(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Live-Status für laufende Backup/Restore Operationen (Polling-UX).
    Note (Issue 18): status is ephemeral (module dict); lost on backend restart (acceptable per original task).
    """
    require_server_permission(user, server_id, db, "server.backups.read")
    from services.backup_service import get_active_backup_status
    active = get_active_backup_status(server_id)
    if active:
        return {
            "active": True,
            "operation": active.get("operation"),
            "started_at": active.get("started_at"),
            "estimated_size_mb": active.get("estimated_size_mb"),
        }
    return {
        "active": False,
        "operation": None,
        "started_at": None,
        "estimated_size_mb": None,
    }


@router.patch("/{server_id}/settings")
def update_backup_settings(server_id: int, body: BackupSettingsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.config.write")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    if body.backup_on_start is not None:
        server.backup_on_start = body.backup_on_start
    if body.backup_interval_hours is not None:
        server.backup_interval_hours = body.backup_interval_hours if body.backup_interval_hours > 0 else None
    if body.backup_retention_count is not None:
        server.backup_retention_count = max(1, body.backup_retention_count)
    db.commit()
    return {"message": "Einstellungen gespeichert"}


@router.post("/{server_id}/auto")
def auto_backup(server_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """Interner Endpoint (nur von GamePlugin.start via Loopback mit Header).
    Kein volles Auth.
    """
    if request.headers.get("X-MSM-Internal-Auto") != "1" or not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Interner Endpoint")

    # /auto kept for compat (original task spec: caller removed from base.py GamePlugin.start only).
    # Header guard is internal-only (no public callers post-cleanup). See Issue 9/15.

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server or not server.backup_on_start:
        return {"message": "Auto-Backup deaktiviert"}

    from services.backup_orchestrator import create_server_backup
    import logging
    logger = logging.getLogger(__name__)
    try:
        backup = create_server_backup(server_id, db, timeout_seconds=300)
        return {"message": "Auto-Backup erstellt", "backup_id": backup.id}
    except Exception:
        # Niemals crashen des Callers (Plugins rufen fire-and-forget ohne Error-Handling)
        logger.warning("Auto-Backup fehlgeschlagen für Server %s (details redacted for security)", server_id)
        return {"message": "Auto-Backup fehlgeschlagen"}


@router.post("/{server_id}/restore/{backup_id}")
def restore_backup(
    server_id: int,
    backup_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """HTTP-Rand des Restores.

    Die Orchestrierung — S3-Download, Entschluesselung, Lifecycle-Sperre,
    Container-Stop, sicheres Entpacken, Postgres-Restore und Rollback — steht in
    `services/backup_restore_service.py`. Dort, weil auch die KI ein Backup
    einspielen kann und dabei **denselben** Weg nehmen muss: ein zweiter Pfad
    wuerde die Reihenfolge nicht kennen, auf die es ankommt (herunterladen und
    entschluesseln, bevor der Container faellt).

    Frueher stand alles hier — rund dreihundert Zeilen. Dieselbe Aufteilung
    hatte `servers.py::delete_server` schon vorgemacht.
    """
    return restore_server_backup(
        db,
        server_id=server_id,
        backup_id=backup_id,
        actor=ActorContext.for_user(user),
    )


@router.post("/{server_id}/{backup_id}/upload-to-cloud")
def upload_to_cloud(server_id: int, backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Laedt ein bestehendes lokales Backup verschluesselt in S3 hoch.

    Setzt s3_key, encrypted=True. Idempotent (bereits hochgeladen → 2xx).
    Erfordert S3 konfiguriert + Backup-Passwort gesetzt (sonst 4xx).
    404 wenn Backup nicht gefunden oder lokale Datei fehlt.
    """
    require_server_permission(user, server_id, db, "server.backups.create")
    server = db.query(Server).filter(Server.id == server_id).first()
    backup = db.query(Backup).filter(Backup.id == backup_id, Backup.server_id == server_id).first()
    if not server or not backup:
        raise HTTPException(status_code=404, detail="Server oder Backup nicht gefunden")

    from services.backup_config_service import BackupConfigService

    # Idempotenz: bereits in S3 hochgeladen → 2xx ohne Re-Upload.
    if backup.s3_key and backup.encrypted:
        return {"message": "Backup bereits in Cloud hochgeladen"}

    # S3 + Passwort erforderlich.
    if not BackupConfigService.is_s3_configured():
        raise HTTPException(status_code=400, detail="S3 ist nicht konfiguriert")
    if not BackupConfigService.is_backup_password_set():
        raise HTTPException(status_code=400, detail="Backup-Passwort nicht gesetzt")

    # Lokale Datei muss existieren (Upload-Quelle).
    if not os.path.exists(backup.filename):
        raise HTTPException(status_code=404, detail="Backup-Datei nicht gefunden")

    from services.backup_orchestrator import upload_backup_to_cloud
    success = upload_backup_to_cloud(backup, db, server_id)
    if success:
        return {"message": "Backup in Cloud hochgeladen"}
    raise HTTPException(status_code=500, detail="Cloud-Upload fehlgeschlagen")


@router.delete("/{server_id}/{backup_id}")
def delete_backup(server_id: int, backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.backups.delete")
    backup = db.query(Backup).filter(Backup.id == backup_id, Backup.server_id == server_id).first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden")

    # S3-Delete (best-effort, nur wenn s3_key vorhanden).
    # S3-Fehler blockiert nicht das lokale Delete (Warning-Log, keine Secrets).
    if backup.s3_key:
        try:
            from services.s3_service import S3Service
            S3Service.delete_object(backup.s3_key, bucket=backup.s3_bucket)
        except Exception as exc:
            logger.warning(
                "S3-Delete fehlgeschlagen (Backup %s): %s",
                backup.id, type(exc).__name__,
            )

    if os.path.exists(backup.filename):
        try:
            os.remove(backup.filename)
        except OSError:
            # Race oder Rechte-Problem: Record trotzdem löschen, keine Exception nach außen (200)
            pass
    db.delete(backup)
    db.commit()
    return {"message": "Backup gelöscht"}
