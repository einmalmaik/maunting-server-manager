import os
import shutil
import subprocess
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Backup, Server, Permission, User
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


def _cleanup_old_backups(server_id: int, keep: int, db: Session) -> None:
    """Entfernt alte Backups über dem Retention-Limit."""
    old = db.query(Backup).filter(Backup.server_id == server_id)\
        .order_by(Backup.created_at.desc()).offset(keep).all()
    for b in old:
        if os.path.exists(b.filename):
            try:
                os.remove(b.filename)
            except OSError:
                pass
        db.delete(b)
    db.commit()


def _run_backup(server_id: int, db: Session, name: str | None = None) -> Backup | None:
    """Führt ein Backup aus und cleaned up alte Backups."""
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server or not os.path.isdir(server.install_dir):
        return None

    backup_dir = f"/opt/msm/backups/{server_id}"
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{server.name}_{timestamp}.tar.gz"
    filepath = os.path.join(backup_dir, filename)

    try:
        subprocess.run(
            ["tar", "-czf", filepath, "-C", server.install_dir, "."],
            check=True, capture_output=True, timeout=600,
            env={**os.environ, "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        )
        size_mb = os.path.getsize(filepath) // (1024 * 1024)
    except Exception:
        return None

    backup = Backup(server_id=server_id, filename=filepath, size_mb=size_mb, name=name or None)
    db.add(backup)
    db.commit()
    db.refresh(backup)

    # Retention: alte Backups löschen
    _cleanup_old_backups(server_id, server.backup_retention_count, db)
    return backup


def run_scheduled_backups(db: Session) -> None:
    """Führt fällige geplante Backups aus (wird vom Scheduler aufgerufen)."""
    servers = db.query(Server).filter(
        Server.backup_interval_hours.isnot(None),
        Server.backup_interval_hours > 0
    ).all()

    for server in servers:
        # Prüfe ob letztes Backup älter als das Intervall ist
        last = db.query(Backup).filter(Backup.server_id == server.id)\
            .order_by(Backup.created_at.desc()).first()
        if last and server.backup_interval_hours:
            next_due = last.created_at + timedelta(hours=server.backup_interval_hours)
            if datetime.now(timezone.utc) < next_due:
                continue
        _run_backup(server.id, db)





@router.get("/{server_id}", response_model=list[BackupResponse])
def list_backups(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_server_permission(user, server_id, db, "can_backup")
    return db.query(Backup).filter(Backup.server_id == server_id).order_by(Backup.created_at.desc()).all()


@router.post("/{server_id}")
def create_backup(server_id: int, body: CreateBackupRequest | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_backup")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    if not os.path.isdir(server.install_dir):
        raise HTTPException(status_code=400, detail="Server-Verzeichnis existiert nicht. Ist der Server installiert?")

    backup = _run_backup(server_id, db, name=body.name if body else None)
    if not backup:
        raise HTTPException(status_code=500, detail="Backup fehlgeschlagen")
    return {"message": "Backup erstellt", "backup_id": backup.id, "size_mb": backup.size_mb}


@router.get("/{server_id}/settings", response_model=BackupSettingsResponse)
def get_backup_settings(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_server_permission(user, server_id, db, "can_backup")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return BackupSettingsResponse(
        backup_on_start=server.backup_on_start,
        backup_interval_hours=server.backup_interval_hours,
        backup_retention_count=server.backup_retention_count,
    )


@router.patch("/{server_id}/settings")
def update_backup_settings(server_id: int, body: BackupSettingsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_backup")
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
def auto_backup(server_id: int, db: Session = Depends(get_db)) -> dict:
    """Interner Endpoint: Auto-Backup bei Server-Start (kein Auth-Check)."""
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server or not server.backup_on_start:
        return {"message": "Auto-Backup deaktiviert"}
    backup = _run_backup(server_id, db)
    if backup:
        return {"message": "Auto-Backup erstellt", "backup_id": backup.id}
    return {"message": "Auto-Backup fehlgeschlagen"}


@router.post("/{server_id}/restore/{backup_id}")
def restore_backup(server_id: int, backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Stellt ein Backup wieder her.

    Stoppt den Docker-Container VOR dem Extrahieren — sonst greift der laufende
    Server-Prozess auf Dateien zu, die wir gerade ersetzen, und das install_dir
    kann nicht atomar ersetzt werden. Container wird NICHT automatisch wieder
    gestartet; das übernimmt der Nutzer (UI bietet Start-Button).
    """
    require_server_permission(user, server_id, db, "can_restore")
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Wiederherstellung fehlgeschlagen: {e}")

    # Status zurücksetzen — Server ist jetzt installiert/stopped, nicht running
    server.status = "stopped"
    server.status_message = None
    db.commit()

    return {"message": "Backup wiederhergestellt"}


@router.delete("/{server_id}/{backup_id}")
def delete_backup(server_id: int, backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_backup")
    backup = db.query(Backup).filter(Backup.id == backup_id, Backup.server_id == server_id).first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden")
    if os.path.exists(backup.filename):
        os.remove(backup.filename)
    db.delete(backup)
    db.commit()
    return {"message": "Backup gelöscht"}
