import os
import shutil
import tarfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Backup, Server, User
from schemas import BackupResponse
from dependencies import get_current_user, verify_csrf, require_server_permission
from config import settings


def _is_loopback_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if settings.debug and host == "testclient":
        return True
    return host in {"127.0.0.1", "::1", "localhost"}


def _safe_extract_backup_tar(archive_path: str, destination: str) -> None:
    """Extract a backup tar without allowing paths or links to escape install_dir."""
    dest = os.path.abspath(destination)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = member.name
            if not name or "\x00" in name or os.path.isabs(name):
                raise ValueError("Unsicheres Backup-Archiv")
            target = os.path.abspath(os.path.join(dest, name))
            if os.path.commonpath([dest, target]) != dest:
                raise ValueError("Unsicheres Backup-Archiv")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError("Unsicheres Backup-Archiv")
        archive.extractall(dest, members=members, filter="data")


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

    Felder (Schritt 7 + Schritt 11):
    - active: bool
    - operation: "creating" | "uploading" | "downloading" | "restoring" | "decrypting" | null
    - phase: "create" | "upload" | "download" | "extract" | "decrypt" | null
      (Frontend nutzt phase fuer Label + Progress-Bar-Farbe; operation ist
       granularer fuer Status-Anzeige)
    - bytes_done: int (None wenn nicht messbar, z.B. local-Provider beim create)
    - bytes_total: int (None wenn noch nicht bekannt)
    - percent: int (0-100, None wenn nicht berechenbar)
    - started_at: ISO 8601
    - estimated_size_mb: int (vom letzten Backup, Anzeige in MB)

    Note (Issue 18): status is ephemeral (module dict); lost on backend restart
    (acceptable per original task).
    """
    require_server_permission(user, server_id, db, "server.backups.read")
    from services.backup_service import get_active_backup_status
    active = get_active_backup_status(server_id)
    if active:
        return {
            "active": True,
            "operation": active.get("operation"),
            "phase": active.get("phase"),
            "bytes_done": active.get("bytes_done"),
            "bytes_total": active.get("bytes_total"),
            "percent": active.get("percent"),
            "started_at": active.get("started_at"),
            "estimated_size_mb": active.get("estimated_size_mb"),
        }
    return {
        "active": False,
        "operation": None,
        "phase": None,
        "bytes_done": None,
        "bytes_total": None,
        "percent": None,
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
async def restore_backup(server_id: int, backup_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Stellt ein Backup wieder her.

    Stoppt den Docker-Container VOR dem Extrahieren — sonst greift der laufende
    Server-Prozess auf Dateien zu, die wir gerade ersetzen, und das install_dir
    kann nicht atomar ersetzt werden. Container wird NICHT automatisch wieder
    gestartet; das übernimmt der Nutzer (UI bietet Start-Button).

    Verwendet denselben Lifecycle-Lock wie Start/Stop/Restart, damit während
    des Restore kein paralleler Start gegen ein halb ersetztes install_dir läuft.

    Die Backup-Logik selbst (Provider-Download, Decryption, Extract,
    Metadata-Apply, Port-Reallocation, Status) liegt zentral in
    ``services.backup_service.restore_backup``. Hier ist nur noch das
    Docker-Lifecycle-Orchestration (Container stoppen/remove) +
    Lifecycle-Lock.
    """
    require_server_permission(user, server_id, db, "server.backups.restore")

    from services.server_lifecycle_service import acquire_lock_async, get_server_lifecycle_lock

    lock = get_server_lifecycle_lock(server_id)
    async with acquire_lock_async(lock):
        # Container stoppen, falls er läuft — Bind-Mount-Konsistenz
        from games.base import container_name_for
        from services import docker_service
        server = db.query(Server).filter(Server.id == server_id).first()
        if server:
            container = container_name_for(server.id)
            if docker_service.is_running(container):
                docker_service.stop(container, timeout=30)
            # Force-Remove, damit das install_dir nicht von einem (gestoppten) Container
            # beansprucht bleibt und der Container beim nächsten Start frisch kommt
            docker_service.remove(container, force=True)

        # Provider-Download, Decryption, Extract, Metadata-Apply, Ports, Status
        # — alles in restore_backup() (Single Source of Truth)
        from services.backup_service import restore_backup as service_restore_backup
        try:
            service_restore_backup(server_id, backup_id, db)
        except FileNotFoundError as e:
            # 404 — Server, Backup oder Backup-Datei nicht gefunden
            raise HTTPException(status_code=404, detail="Server oder Backup nicht gefunden") from e
        except Exception as e:
            # Generischer Fehler (Provider, Decryption, Tar-Sicherheitscheck,
            # Path-Traversal, generischer Extract-Error, ...). Wir geben
            # bewusst einen generischen 500 ohne Pfad-Leak zurueck.
            error_server = db.query(Server).filter(Server.id == server_id).first()
            if error_server:
                error_server.status = "error"
                error_server.status_message = "Wiederherstellung fehlgeschlagen"
                db.commit()
            raise HTTPException(
                status_code=500, detail="Wiederherstellung fehlgeschlagen"
            ) from e

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
