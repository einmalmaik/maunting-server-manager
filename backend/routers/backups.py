import os
import shutil
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Backup, Server, User
from schemas import BackupResponse
from dependencies import get_current_user, verify_csrf, require_server_permission


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
    return db.query(Backup).filter(Backup.server_id == server_id).order_by(Backup.created_at.desc()).all()


@router.post("/{server_id}")
def create_backup(server_id: int, body: CreateBackupRequest | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.backups.create")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    # Kein Duplikat-Check mehr (Single Source of Truth im Service); generische Fehlermeldung
    # (verhindert Leak von install_dir / Pfaden in HTTP-Details und Logs).
    from services.backup_service import run_backup as central_run_backup
    try:
        backup = central_run_backup(server_id, db, name=body.name if body else None, timeout_seconds=600)
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
    if request.headers.get("X-MSM-Internal-Auto") != "1":
        raise HTTPException(status_code=403, detail="Interner Endpoint")

    # /auto kept for compat (original task spec: caller removed from base.py GamePlugin.start only).
    # Header guard is internal-only (no public callers post-cleanup). See Issue 9/15.

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server or not server.backup_on_start:
        return {"message": "Auto-Backup deaktiviert"}

    from services.backup_service import run_backup as central_run_backup
    import logging
    logger = logging.getLogger(__name__)
    try:
        backup = central_run_backup(server_id, db, timeout_seconds=300)
        return {"message": "Auto-Backup erstellt", "backup_id": backup.id}
    except Exception:
        # Niemals crashen des Callers (Plugins rufen fire-and-forget ohne Error-Handling)
        logger.warning("Auto-Backup fehlgeschlagen für Server %s (details redacted for security)", server_id)
        return {"message": "Auto-Backup fehlgeschlagen"}


@router.post("/{server_id}/restore/{backup_id}")
def restore_backup(server_id: int, backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Stellt ein Backup wieder her.

    Stoppt den Docker-Container VOR dem Extrahieren — sonst greift der laufende
    Server-Prozess auf Dateien zu, die wir gerade ersetzen, und das install_dir
    kann nicht atomar ersetzt werden. Container wird NICHT automatisch wieder
    gestartet; das übernimmt der Nutzer (UI bietet Start-Button).

    Note (Issue 8): does not acquire get_server_lifecycle_lock (pre-existing design;
    force-remove is idempotent; concurrent start/pre-start race window accepted as
    user-initiated op with manual restart after restore).
    """
    require_server_permission(user, server_id, db, "server.backups.restore")
    server = db.query(Server).filter(Server.id == server_id).first()
    backup = db.query(Backup).filter(Backup.id == backup_id, Backup.server_id == server_id).first()
    if not server or not backup:
        raise HTTPException(status_code=404, detail="Server oder Backup nicht gefunden")
    if not os.path.exists(backup.filename):
        raise HTTPException(status_code=404, detail="Backup-Datei nicht gefunden")

    # Container stoppen, falls er läuft — Bind-Mount-Konsistenz
    from games.base import container_name_for
    from services import docker_service
    container = container_name_for(server.id)
    if docker_service.is_running(container):
        docker_service.stop(container, timeout=30)
    # Force-Remove, damit das install_dir nicht von einem (gestoppten) Container
    # beansprucht bleibt und der Container beim nächsten Start frisch kommt
    docker_service.remove(container, force=True)

    # Live-Status für Restore (Estimate = Größe des zu restore-nden Backups)
    from services.backup_service import set_active_backup_status, clear_active_backup_status
    set_active_backup_status(server_id, "restoring", backup.size_mb)

    try:
        # Alte Daten sichern (rollback path)
        old_backup = f"{server.install_dir}_pre_restore_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        shutil.move(server.install_dir, old_backup)
        os.makedirs(server.install_dir, exist_ok=True)
        subprocess.run(
            ["tar", "-xzf", backup.filename, "-C", server.install_dir],
            check=True, capture_output=True, timeout=300,
            env={**os.environ, "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        )
    except Exception:
        # Security: niemals Pfade, install_dir oder Exception-Details leaken (generische Meldung)
        clear_active_backup_status(server_id)
        raise HTTPException(status_code=500, detail="Wiederherstellung fehlgeschlagen")
    finally:
        clear_active_backup_status(server_id)

    # Status zurücksetzen — Server ist jetzt installiert/stopped, nicht running
    server.status = "stopped"
    server.status_message = None
    db.commit()

    return {"message": "Backup wiederhergestellt"}


@router.delete("/{server_id}/{backup_id}")
def delete_backup(server_id: int, backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.backups.delete")
    backup = db.query(Backup).filter(Backup.id == backup_id, Backup.server_id == server_id).first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden")
    if os.path.exists(backup.filename):
        try:
            os.remove(backup.filename)
        except OSError:
            # Race oder Rechte-Problem: Record trotzdem löschen, keine Exception nach außen (200)
            pass
    db.delete(backup)
    db.commit()
    return {"message": "Backup gelöscht"}
