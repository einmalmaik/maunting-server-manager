import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Server, Permission, User
from schemas import ServerCreate, ServerResponse, ServerUpdate, ServerStatusResponse
from routers.auth import get_current_user

router = APIRouter(prefix="/api/servers", tags=["servers"])


def _check_perm(user: User, server_id: int, db: Session, action: str) -> None:
    if user.is_owner:
        return
    perm = db.query(Permission).filter(
        Permission.user_id == user.id,
        Permission.server_id == server_id
    ).first()
    if not perm or not getattr(perm, action, False):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")


@router.get("", response_model=list[ServerResponse])
def list_servers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Server]:
    if user.is_owner:
        return db.query(Server).all()
    allowed_ids = [p.server_id for p in db.query(Permission).filter(Permission.user_id == user.id).all()]
    return db.query(Server).filter(Server.id.in_(allowed_ids)).all()


@router.post("", response_model=ServerResponse, status_code=201)
def create_server(req: ServerCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Server:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann Server erstellen")

    install_dir = f"/opt/msm/servers/{req.game_type}_{db.query(Server).count() + 1}"
    linux_user = f"msm_srv_{db.query(Server).count() + 1}"

    # Linux-User erstellen
    try:
        subprocess.run(["useradd", "-r", "-m", "-d", install_dir, linux_user], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        # User existiert vielleicht schon
        pass

    server = Server(
        name=req.name,
        game_type=req.game_type,
        install_dir=install_dir,
        linux_user=linux_user,
        status="stopped",
        auto_restart=req.auto_restart,
        restart_interval_hours=req.restart_interval_hours,
        restart_time_utc=req.restart_time_utc,
        cpu_limit_percent=req.cpu_limit_percent,
        ram_limit_mb=req.ram_limit_mb,
        disk_limit_gb=req.disk_limit_gb,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@router.get("/{server_id}", response_model=ServerResponse)
def get_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Server:
    _check_perm(user, server_id, db, "can_view_console")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


@router.patch("/{server_id}", response_model=ServerResponse)
def update_server(server_id: int, req: ServerUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Server:
    _check_perm(user, server_id, db, "can_edit_config")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    for key, val in req.model_dump(exclude_unset=True).items():
        setattr(server, key, val)
    db.commit()
    db.refresh(server)
    return server


@router.delete("/{server_id}")
def delete_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann Server löschen")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    db.delete(server)
    db.commit()
    return {"message": "Server gelöscht"}


@router.post("/{server_id}/start")
def start_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db, "can_start")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    # Plugin-Logik später
    server.status = "running"
    db.commit()
    return {"message": "Start-Befehl gesendet", "status": server.status}


@router.post("/{server_id}/stop")
def stop_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db, "can_stop")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    server.status = "stopped"
    db.commit()
    return {"message": "Stop-Befehl gesendet", "status": server.status}


@router.post("/{server_id}/restart")
def restart_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db, "can_restart")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    server.status = "running"
    db.commit()
    return {"message": "Restart-Befehl gesendet", "status": server.status}


@router.get("/{server_id}/status", response_model=ServerStatusResponse)
def server_status(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db, "can_view_console")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return {
        "id": server.id,
        "status": server.status,
        "status_message": server.status_message,
        "cpu_percent": None,
        "ram_mb": None,
        "disk_mb": None,
        "uptime_seconds": None,
        "players_online": None,
    }


@router.get("/{server_id}/logs")
def server_logs(server_id: int, lines: int = 100, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db, "can_view_logs")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    log_path = os.path.join(server.install_dir, "logs", "latest.log")
    if not os.path.exists(log_path):
        return {"logs": "", "path": log_path}
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
        return {"logs": "".join(all_lines[-lines:]), "path": log_path}
    except Exception:
        return {"logs": "", "path": log_path}
