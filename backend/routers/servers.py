import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Server, Permission, User
from schemas import ServerCreate, ServerResponse, ServerUpdate, ServerStatusResponse
from routers.auth import get_current_user, verify_csrf
from games import get_plugin

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
def create_server(req: ServerCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> Server:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann Server erstellen")

    install_dir = f"/opt/msm/servers/{req.game_type}_{db.query(Server).count() + 1}"
    linux_user = f"msm_srv_{db.query(Server).count() + 1}"

    # Linux-User erstellen
    try:
        subprocess.run(["useradd", "-r", "-m", "-d", install_dir, linux_user], check=True, capture_output=True)
    except subprocess.CalledProcessError:
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
def update_server(server_id: int, req: ServerUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> Server:
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
def delete_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann Server löschen")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    db.delete(server)
    db.commit()
    return {"message": "Server gelöscht"}


@router.post("/{server_id}/start")
def start_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    _check_perm(user, server_id, db, "can_start")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    result = plugin.start(server)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    server.status = "running"
    db.commit()
    return {"message": "Start-Befehl gesendet", "status": server.status, **result}


@router.post("/{server_id}/stop")
def stop_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    _check_perm(user, server_id, db, "can_stop")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    result = plugin.stop(server)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    server.status = "stopped"
    db.commit()
    return {"message": "Stop-Befehl gesendet", "status": server.status, **result}


@router.post("/{server_id}/restart")
def restart_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    _check_perm(user, server_id, db, "can_restart")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    stop_result = plugin.stop(server)
    if "error" in stop_result:
        raise HTTPException(status_code=500, detail=stop_result["error"])
    start_result = plugin.start(server)
    if "error" in start_result:
        raise HTTPException(status_code=500, detail=start_result["error"])
    server.status = "running"
    db.commit()
    return {"message": "Restart-Befehl gesendet", "status": server.status, "stop": stop_result, "start": start_result}


@router.get("/{server_id}/status", response_model=ServerStatusResponse)
def server_status(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db, "can_view_console")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
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
    plugin_status = plugin.get_status(server)
    server.status = plugin_status.status
    server.status_message = plugin_status.status_message or ""
    db.commit()
    return {
        "id": server.id,
        "status": plugin_status.status,
        "status_message": plugin_status.status_message,
        "cpu_percent": plugin_status.cpu_percent,
        "ram_mb": plugin_status.ram_mb,
        "disk_mb": plugin_status.disk_mb,
        "uptime_seconds": plugin_status.uptime_seconds,
        "players_online": plugin_status.players_online,
    }


@router.get("/{server_id}/logs")
def server_logs(server_id: int, lines: int = 100, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db, "can_view_logs")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if plugin:
        logs = plugin.get_logs(server, lines=lines)
        return {"logs": logs, "path": "plugin-provided"}
    # Fallback: generische Log-Pfade
    fallback_paths = [
        os.path.join(server.install_dir, "logs", "latest.log"),
        os.path.join(server.install_dir, "log_1.txt"),
        os.path.join(server.install_dir, "log", "script_1.log"),
    ]
    for path in fallback_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                return {"logs": "".join(all_lines[-lines:]), "path": path}
            except Exception:
                continue
    return {"logs": "", "path": "none"}
