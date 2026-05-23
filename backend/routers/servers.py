import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Server, Permission, User
from schemas import ServerCreate, ServerResponse, ServerUpdate, ServerStatusResponse
from dependencies import get_current_user, verify_csrf, require_server_permission
from games import get_plugin
from games.base import container_name_for
from services import EmailService, docker_service
from services.firewall_service import close_ports, open_ports
from services.port_allocation_service import allocate_ports

router = APIRouter(prefix="/api/servers", tags=["servers"])





@router.get("", response_model=list[ServerResponse])
def list_servers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Server]:
    if user.is_owner:
        return db.query(Server).all()
    allowed_ids = [p.server_id for p in db.query(Permission).filter(Permission.user_id == user.id).all()]
    return db.query(Server).filter(Server.id.in_(allowed_ids)).all()


@router.post("", response_model=ServerResponse, status_code=201)
async def create_server(req: ServerCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> Server:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann Server erstellen")

    base_dir = os.path.abspath(settings.servers_dir)
    count = db.query(Server).count() + 1
    install_dir = os.path.join(base_dir, f"{req.game_type}_{count}")

    # Verzeichnis anlegen — wird vom Panel-User (`msm`) angelegt und ist von dort
    # rw, während der Container das Volume mit derselben UID/GID mountet (siehe
    # docker_service.host_uid_gid()). Kein useradd, kein chown via sudo nötig.
    try:
        os.makedirs(install_dir, exist_ok=True)
        os.chmod(install_dir, 0o750)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"install_dir konnte nicht angelegt werden: {e}")

    # Ports automatisch vergeben (oder vom Nutzer übernehmen)
    try:
        game_port, query_port, rcon_port = allocate_ports(
            db,
            requested_game_port=req.game_port,
            requested_query_port=req.query_port,
            requested_rcon_port=req.rcon_port,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    server = Server(
        name=req.name,
        game_type=req.game_type,
        install_dir=install_dir,
        status="stopped",
        auto_restart=req.auto_restart,
        restart_interval_hours=req.restart_interval_hours,
        restart_time_utc=req.restart_time_utc,
        cpu_limit_percent=req.cpu_limit_percent,
        ram_limit_mb=req.ram_limit_mb,
        disk_limit_gb=req.disk_limit_gb,
        game_port=game_port,
        query_port=query_port,
        rcon_port=rcon_port,
        public_bind_ip=req.public_bind_ip,
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    # Stabilen Container-Namen cachen (Debug/Audit).
    server.container_name = container_name_for(server.id)
    db.commit()
    db.refresh(server)

    # Firewall-Regeln anlegen (nur auf Linux mit UFW)
    open_ports(server.name, game_port, query_port, rcon_port)

    # Auto-Install: Plugin startet Installation im Hintergrund
    plugin = get_plugin(req.game_type)
    if plugin:
        server.status = "installing"
        server.status_message = "Installation gestartet"
        db.commit()
        plugin.install(server)

    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_installed_notification(user.email, user.username, server.name)

    return server


@router.get("/{server_id}", response_model=ServerResponse)
def get_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Server:
    require_server_permission(user, server_id, db, "can_view_console")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


@router.patch("/{server_id}", response_model=ServerResponse)
def update_server(server_id: int, req: ServerUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> Server:
    require_server_permission(user, server_id, db, "can_edit_config")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    old_ports = (server.game_port, server.query_port, server.rcon_port)

    # ── Port-Änderung: validieren + Firewall aktualisieren ──
    port_fields = {"game_port", "query_port", "rcon_port"}
    changed_ports = port_fields & set(req.model_dump(exclude_unset=True).keys())

    if changed_ports:
        # Validierung: keine Konflikte mit anderen Servern
        try:
            new_game, new_query, new_rcon = allocate_ports(
                db,
                requested_game_port=req.game_port if req.game_port is not None else server.game_port,
                requested_query_port=req.query_port if req.query_port is not None else server.query_port,
                requested_rcon_port=req.rcon_port if req.rcon_port is not None else server.rcon_port,
                exclude_server_id=server.id,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))

        # Werte überschreiben (damit setattr korrekt arbeitet)
        if req.game_port is not None:
            req.game_port = new_game
        if req.query_port is not None:
            req.query_port = new_query
        if req.rcon_port is not None:
            req.rcon_port = new_rcon

    # Standard-Update
    for key, val in req.model_dump(exclude_unset=True).items():
        setattr(server, key, val)
    db.commit()
    db.refresh(server)

    if changed_ports:
        # Alte Firewall-Regeln schließen, neue öffnen
        close_ports(
            game_port=old_ports[0] or 0,
            query_port=old_ports[1],
            rcon_port=old_ports[2],
        )
        open_ports(server.name, server.game_port, server.query_port, server.rcon_port)

        # Container neu starten, damit neue Ports/Limits greifen — nur, wenn er
        # gerade läuft. Der Plugin-Default-Start zieht den Container ohnehin mit
        # frischen Flags hoch.
        plugin = get_plugin(server.game_type)
        if plugin and docker_service.is_running(container_name_for(server.id)):
            plugin.stop(server)
            plugin.start(server)

    return server


@router.delete("/{server_id}")
def delete_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann Server löschen")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    # Container stoppen + entfernen (idempotent — kein Fehler, wenn nicht da)
    container = container_name_for(server.id)
    docker_service.remove(container, force=True)

    # Firewall-Regeln schließen
    close_ports(
        game_port=server.game_port or 0,
        query_port=server.query_port,
        rcon_port=server.rcon_port,
    )

    # Install-Verzeichnis aufräumen
    install_dir = server.install_dir
    if os.path.exists(install_dir):
        try:
            shutil.rmtree(install_dir)
        except OSError:
            pass

    db.delete(server)
    db.commit()
    return {
        "message": "Server gelöscht",
        "cleanup": {"container_removed": container, "dir_removed": install_dir},
    }


@router.post("/{server_id}/start")
async def start_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_start")
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
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_status_notification(user.email, user.username, server.name, "gestartet")
    return {"message": "Start-Befehl gesendet", "status": server.status, **result}


@router.post("/{server_id}/stop")
async def stop_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_stop")
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
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_status_notification(user.email, user.username, server.name, "gestoppt")
    return {"message": "Stop-Befehl gesendet", "status": server.status, **result}


@router.post("/{server_id}/restart")
async def restart_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_restart")
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
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_status_notification(user.email, user.username, server.name, "neugestartet")
    return {"message": "Restart-Befehl gesendet", "status": server.status, "stop": stop_result, "start": start_result}


@router.get("/{server_id}/status", response_model=ServerStatusResponse)
def server_status(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    require_server_permission(user, server_id, db, "can_view_console")
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
    # installing/updating/error nicht ueberschreiben — Background-Thread oder
    # Admin setzen den Status selbst zurueck, wenn die Operation abgeschlossen ist
    if server.status not in ("installing", "updating", "error"):
        server.status = plugin_status.status
        server.status_message = plugin_status.message or ""
    db.commit()
    return {
        "id": server.id,
        "status": server.status,
        "status_message": server.status_message,
        "cpu_percent": plugin_status.cpu_percent,
        "ram_mb": plugin_status.ram_mb,
        "disk_mb": plugin_status.disk_mb,
        "uptime_seconds": plugin_status.uptime_seconds,
        "players_online": plugin_status.players_online,
    }


@router.post("/{server_id}/install")
def install_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann Server installieren")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    server.status = "installing"
    server.status_message = "Installation gestartet"
    db.commit()
    result = plugin.install(server)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return {"message": "Installation gestartet", **result}


@router.get("/{server_id}/console")
def server_console(server_id: int, lines: int = 200, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    require_server_permission(user, server_id, db, "can_view_console")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    console_log = ""
    if plugin:
        console_log = plugin.get_console_log(server, lines=lines)
    return {"logs": console_log}


@router.get("/{server_id}/logs")
def server_logs(server_id: int, lines: int = 100, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    require_server_permission(user, server_id, db, "can_view_logs")
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
