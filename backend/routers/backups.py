import os
import shutil
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Backup, Server, Permission, User
from schemas import BackupResponse
from dependencies import get_current_user, verify_csrf, require_server_permission

router = APIRouter(prefix="/api/backups", tags=["backups"])





@router.get("/{server_id}", response_model=list[BackupResponse])
def list_backups(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_server_permission(user, server_id, db, "can_backup")
    return db.query(Backup).filter(Backup.server_id == server_id).order_by(Backup.created_at.desc()).all()


@router.post("/{server_id}")
def create_backup(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_backup")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    if not os.path.isdir(server.install_dir):
        raise HTTPException(status_code=400, detail="Server-Verzeichnis existiert nicht. Ist der Server installiert?")

    backup_dir = f"/opt/msm/backups/{server_id}"
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{server.name}_{timestamp}.tar.gz"
    filepath = os.path.join(backup_dir, filename)

    try:
        subprocess.run(
            ["tar", "-czf", filepath, "-C", server.install_dir, "."],
            check=True, capture_output=True, timeout=300,
            env={**os.environ, "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        )
        size_mb = os.path.getsize(filepath) // (1024 * 1024)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup fehlgeschlagen: {e}")

    backup = Backup(server_id=server_id, filename=filepath, size_mb=size_mb)
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return {"message": "Backup erstellt", "backup_id": backup.id, "size_mb": size_mb}


@router.post("/{server_id}/restore/{backup_id}")
def restore_backup(server_id: int, backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_restore")
    server = db.query(Server).filter(Server.id == server_id).first()
    backup = db.query(Backup).filter(Backup.id == backup_id, Backup.server_id == server_id).first()
    if not server or not backup:
        raise HTTPException(status_code=404, detail="Server oder Backup nicht gefunden")
    if not os.path.exists(backup.filename):
        raise HTTPException(status_code=404, detail="Backup-Datei nicht gefunden")

    try:
        # Alte Daten sichern
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
