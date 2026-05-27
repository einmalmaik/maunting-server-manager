import asyncio
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Server, User
from schemas import ServerCreate, ServerResponse, ServerUpdate, ServerStatusResponse
from dependencies import get_current_user, require_global, require_server_permission, verify_csrf
from services import permission_service
from blueprints.schema import BlueprintSourceType, _is_safe_relative_path
from games import get_plugin
from games.base import container_name_for, _console_log_path
from services import EmailService, docker_service
from services.docker_iptables_service import accept_server as iptables_accept_server
from services.docker_iptables_service import revoke_server as iptables_revoke_server
from services.firewall_service import close_ports, open_ports
from services.network_interfaces_service import default_bind_ip, list_host_interfaces
from services.port_allocation_service import PortConflictError, allocate_ports

router = APIRouter(prefix="/api/servers", tags=["servers"])





@router.get("", response_model=list[ServerResponse])
def list_servers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Server]:
    return permission_service.list_visible_servers(db, user)


@router.post("", response_model=ServerResponse, status_code=201)
async def create_server(req: ServerCreate, db: Session = Depends(get_db), user: User = Depends(require_global("servers.create")), _: None = Depends(verify_csrf)) -> Server:

    base_dir = os.path.abspath(settings.servers_dir)

    # Bind-IP und Ports zuerst validieren (keine Seiteneffekte auf FS oder DB).
    # 127.0.0.1 / 0.0.0.0 verboten (Validator + hier).
    bind_ip = req.public_bind_ip or default_bind_ip()
    try:
        game_port, query_port, rcon_port = allocate_ports(
            db,
            requested_game_port=req.game_port,
            requested_query_port=req.query_port,
            requested_rcon_port=req.rcon_port,
            bind_ip=bind_ip or "0.0.0.0",
        )
    except PortConflictError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Placeholder-Row zuerst einfügen, um stabile PK (server.id) zu erhalten.
    # Danach install_dir = f".../{game_type}_{id}" — kollisionsfrei über alle Zeit,
    # auch nach DELETEs (Count-basiert war die Ursache für dayz_1-Reuse).
    # Placeholder wird bei Konflikt sofort wieder gelöscht (nie sichtbar für User).
    server = Server(
        name=req.name,
        game_type=req.game_type,
        install_dir="/tmp/msm-pending-create",
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
        public_bind_ip=bind_ip,
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    install_dir = os.path.join(base_dir, f"{req.game_type}_{server.id}")

    # Vorheriges Verzeichnis auf Host prüfen (verwaist von abgebrochenem Install,
    # manuellem Eingriff oder root-owned SteamCMD-Artifact). Saubere 409 statt
    # mysteriösem EPERM auf chmod.
    if os.path.exists(install_dir):
        db.delete(server)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail=(
                f"Server-Verzeichnis existiert bereits auf dem Host: {install_dir}. "
                "Möglicherweise verwaist aus einem vorherigen (fehlgeschlagenen) "
                "Installationsversuch. Bitte manuell aufräumen oder Support kontaktieren."
            ),
        )

    # Verzeichnis anlegen — wird vom Panel-User (`msm`) angelegt und ist von dort
    # rw, während der Container das Volume mit derselben UID/GID mountet (siehe
    # docker_service.host_uid_gid()). Kein useradd, kein chown via sudo nötig.
    # exist_ok=False ist jetzt sicher (Guard oben).
    try:
        os.makedirs(install_dir, exist_ok=False)
        os.chmod(install_dir, 0o750)
    except OSError as e:
        # Bei jedem FS-Fehler die (noch nie sichtbare) Placeholder-Row entfernen.
        db.delete(server)
        db.commit()
        raise HTTPException(status_code=500, detail=f"install_dir konnte nicht angelegt werden: {e}")

    # install_dir endgültig setzen + persistieren.
    server.install_dir = install_dir
    db.commit()
    db.refresh(server)

    # Stabilen Container-Namen cachen (Debug/Audit).
    server.container_name = container_name_for(server.id)
    db.commit()
    db.refresh(server)

    # Auto-Install: Plugin startet Installation im Hintergrund.
    # Firewall-Regeln werden ERST beim Start angelegt (Lifecycle-Kopplung).
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
    require_server_permission(user, server_id, db, "server.view")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


@router.patch("/{server_id}", response_model=ServerResponse)
def update_server(server_id: int, req: ServerUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> Server:
    require_server_permission(user, server_id, db, "server.config.write")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    old_ports = (server.game_port, server.query_port, server.rcon_port)
    old_bind_ip = server.public_bind_ip

    payload = req.model_dump(exclude_unset=True)
    port_fields = {"game_port", "query_port", "rcon_port"}
    resource_fields = {"cpu_limit_percent", "ram_limit_mb", "disk_limit_gb"}
    changed_ports = port_fields & set(payload.keys())
    bind_ip_changed = "public_bind_ip" in payload and payload["public_bind_ip"] != old_bind_ip
    network_change = bool(changed_ports) or bind_ip_changed
    if network_change:
        require_server_permission(user, server_id, db, "server.network.manage")
    if resource_fields & set(payload.keys()):
        require_server_permission(user, server_id, db, "server.resources.manage")

    # ── Port-/Bind-Aenderung: validieren ──
    if changed_ports:
        # Bind-IP fuer den Host-Check: neue Vorgabe (falls mitgegeben) oder Bestand.
        bind_ip_for_check = payload.get("public_bind_ip", old_bind_ip) or "0.0.0.0"
        try:
            new_game, new_query, new_rcon = allocate_ports(
                db,
                requested_game_port=req.game_port if req.game_port is not None else server.game_port,
                requested_query_port=req.query_port if req.query_port is not None else server.query_port,
                requested_rcon_port=req.rcon_port if req.rcon_port is not None else server.rcon_port,
                exclude_server_id=server.id,
                bind_ip=bind_ip_for_check,
            )
        except PortConflictError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
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
    for key, val in payload.items():
        setattr(server, key, val)
    db.commit()
    db.refresh(server)

    if network_change:
        # Alte Firewall- und iptables-Regeln entfernen, neue anlegen — ABER
        # nur, wenn der Server gerade laeuft. Fuer gestoppte Server bleiben die
        # Regeln zu (Lifecycle-Kopplung).
        plugin = get_plugin(server.game_type)
        was_running = plugin is not None and docker_service.is_running(container_name_for(server.id))

        if was_running:
            close_ports(
                game_port=old_ports[0] or 0,
                query_port=old_ports[1],
                rcon_port=old_ports[2],
            )
            iptables_revoke_server(
                server.name,
                old_bind_ip or "",
                old_ports[0] or 0, old_ports[1], old_ports[2],
            )
            # Container stoppen — Plugin.start() legt ihn mit den neuen Ports/
            # Bind-Werten frisch an.
            plugin.stop(server)
            open_ports(server.name, server.game_port, server.query_port, server.rcon_port)
            iptables_accept_server(
                server.name,
                server.public_bind_ip or "",
                server.game_port, server.query_port, server.rcon_port,
            )
            plugin.start(server)

    return server


@router.delete("/{server_id}")
def delete_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Löscht einen Server vollständig:

    1. Docker-Container stoppen + entfernen (idempotent, force=True killt auch
       laufende Container).
    2. UFW-Regeln für Ports schließen.
    3. Install-Verzeichnis (Bind-Mount-Quelle) vom Host entfernen.
    4. Backup-Verzeichnis (alle TAR-Archive) vom Host entfernen — DB-Cascade
       räumt die Backup-Records selbst.
    5. MSM-Console-Log-Verzeichnis entfernen.
    6. DB-Eintrag löschen (Cascade entfernt Permissions/Mods/Backups).
    """
    if not permission_service.has_global_permission(db, user, "servers.delete"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    # 1. Container stoppen + entfernen (idempotent — force killt running)
    container = container_name_for(server.id)
    docker_service.remove(container, force=True)

    # 2. Firewall- und iptables-Regeln schließen
    close_ports(
        game_port=server.game_port or 0,
        query_port=server.query_port,
        rcon_port=server.rcon_port,
    )
    iptables_revoke_server(
        server.name,
        server.public_bind_ip or "",
        server.game_port, server.query_port, server.rcon_port,
    )

    # 3. Install-Verzeichnis physisch löschen
    install_dir = server.install_dir
    dir_removed = False
    if install_dir and os.path.exists(install_dir):
        try:
            shutil.rmtree(install_dir)
            dir_removed = True
        except OSError:
            pass

    # 4. Backup-Verzeichnis (Files) löschen — DB-Cascade räumt Records
    backup_dir = f"/opt/msm/backups/{server.id}"
    backups_removed = False
    if os.path.exists(backup_dir):
        try:
            shutil.rmtree(backup_dir)
            backups_removed = True
        except OSError:
            pass

    # 5. MSM-Console-Log-Verzeichnis räumen
    console_log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs",
        str(server.id),
    )
    if os.path.exists(console_log_dir):
        try:
            shutil.rmtree(console_log_dir)
        except OSError:
            pass

    # 6. DB-Eintrag löschen (Cascade entfernt Permissions/Mods/Backups)
    db.delete(server)
    db.commit()
    return {
        "message": "Server gelöscht",
        "cleanup": {
            "container_removed": container,
            "dir_removed": install_dir if dir_removed else None,
            "backups_removed": backup_dir if backups_removed else None,
        },
    }


def _missing_required_files(install_dir: str, required_files: list[str]) -> list[str]:
    """Prueft, ob alle requiredFiles als reguläre Dateien (keine Symlinks) vorhanden sind."""
    base = Path(install_dir).resolve()
    missing: list[str] = []
    for p in required_files:
        if not _is_safe_relative_path(p):
            missing.append(p)
            continue
        target = base / p
        # Path-Traversal via resolve pruefen (Symlinks werden dabei dereferenziert,
        # aber der Existenz-Check zaehlt Symlinks selbst als fehlend).
        try:
            resolved = target.resolve(strict=False)
            resolved.relative_to(base)
        except (ValueError, RuntimeError):
            missing.append(p)
            continue
        # Symlinks gelten nicht als vorhanden — Defense-in-Depth.
        if target.is_symlink() or not target.is_file():
            missing.append(p)
    return missing


@router.post("/{server_id}/start")
async def start_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.start")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")

    # Sicherheits-Vorprüfung: Server ohne explizite public_bind_ip darf nicht
    # starten — sonst würde Docker auf 0.0.0.0 binden und die UFW-Falle auslösen.
    if not server.public_bind_ip:
        raise HTTPException(
            status_code=400,
            detail=(
                "Server hat keine Bind-IP konfiguriert. Bitte im Server-Detail "
                "eine Public-IP zuweisen, bevor er gestartet wird."
            ),
        )

    # NEU: Pre-Check fuer manualUpload — VOR Firewall-Regeln.
    bp = plugin.get_blueprint()
    if bp and bp.source.type == BlueprintSourceType.MANUAL_UPLOAD:
        manual = bp.source.manual
        assert manual is not None
        missing = _missing_required_files(server.install_dir, manual.requiredFiles)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Server kann nicht gestartet werden — folgende Dateien fehlen "
                    f"im Server-Verzeichnis: {', '.join(missing)}. "
                    "Bitte über den File Manager hochladen (Archive können per "
                    "Rechtsklick → Entpacken ausgepackt werden)."
                ),
            )

    # Firewall-Regeln öffnen vor Container-Start.
    open_ports(server.name, server.game_port, server.query_port, server.rcon_port)
    iptables_accept_server(
        server.name,
        server.public_bind_ip,
        server.game_port, server.query_port, server.rcon_port,
    )

    # Plugin-Aufrufe rufen blockierende Docker-Subprozesse auf. In einer
    # async-Route blockieren sie den gesamten Uvicorn-Event-Loop — alle anderen
    # Requests hängen mit. Daher in einen Threadpool auslagern.
    result = await asyncio.to_thread(plugin.start, server)
    if "error" in result:
        # Container-Start fehlgeschlagen — Firewall-Regeln wieder schließen.
        close_ports(server.game_port, server.query_port, server.rcon_port)
        iptables_revoke_server(
            server.name,
            server.public_bind_ip,
            server.game_port, server.query_port, server.rcon_port,
        )
        raise HTTPException(status_code=500, detail=result["error"])
    server.status = "running"
    db.commit()
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_status_notification(user.email, user.username, server.name, "gestartet")
    return {"message": "Start-Befehl gesendet", "status": server.status, **result}


@router.post("/{server_id}/stop")
async def stop_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.stop")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    # Siehe start_server: docker stop ist synchron und kann bis zum
    # Graceful-Timeout dauern. Threadpool hält den Event-Loop frei.
    result = await asyncio.to_thread(plugin.stop, server)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    server.status = "stopped"
    db.commit()

    # Firewall- und iptables-Regeln nach Container-Stop schließen.
    close_ports(server.game_port, server.query_port, server.rcon_port)
    iptables_revoke_server(
        server.name,
        server.public_bind_ip or "",
        server.game_port, server.query_port, server.rcon_port,
    )

    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_status_notification(user.email, user.username, server.name, "gestoppt")
    return {"message": "Stop-Befehl gesendet", "status": server.status, **result}


@router.post("/{server_id}/restart")
async def restart_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.restart")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    stop_result = await asyncio.to_thread(plugin.stop, server)
    if "error" in stop_result:
        raise HTTPException(status_code=500, detail=stop_result["error"])
    start_result = await asyncio.to_thread(plugin.start, server)
    if "error" in start_result:
        raise HTTPException(status_code=500, detail=start_result["error"])
    server.status = "running"
    db.commit()
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_status_notification(user.email, user.username, server.name, "neugestartet")
    return {"message": "Restart-Befehl gesendet", "status": server.status, "stop": stop_result, "start": start_result}


def _disk_free_mb(path: str) -> int | None:
    """Liefert freien Speicher auf dem Filesystem von `path` in MB.

    Wir nutzen os.statvfs (Linux/Unix). Bei Fehler None — der Frontend zeigt
    dann '-' an, statt zu crashen.
    """
    try:
        if not path:
            return None
        # Falls install_dir noch nicht existiert, das Eltern-Verzeichnis nehmen
        target = path if os.path.exists(path) else os.path.dirname(path) or "/"
        st = os.statvfs(target)
        return int((st.f_bavail * st.f_frsize) // (1024 * 1024))
    except OSError:
        return None


@router.get("/{server_id}/status", response_model=ServerStatusResponse)
def server_status(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    require_server_permission(user, server_id, db, "server.view")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    disk_used = server.disk_usage_mb
    disk_free = _disk_free_mb(server.install_dir) if server.install_dir else None
    if not plugin:
        return {
            "id": server.id,
            "status": server.status,
            "status_message": server.status_message,
            "cpu_percent": None,
            "ram_mb": None,
            "disk_mb": disk_used,
            "uptime_seconds": None,
            "cpu_limit_percent": server.cpu_limit_percent,
            "ram_limit_mb": server.ram_limit_mb,
            "disk_limit_gb": server.disk_limit_gb,
            "disk_used_mb": disk_used,
            "disk_free_mb": disk_free,
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
        # Disk-MB im Status: auf den DB-Wert zurueckfallen, damit auch ohne
        # gesetztes disk_limit ein Used-Wert angezeigt wird.
        "disk_mb": plugin_status.disk_mb if plugin_status.disk_mb is not None else disk_used,
        "uptime_seconds": plugin_status.uptime_seconds,
        "cpu_limit_percent": server.cpu_limit_percent,
        "ram_limit_mb": server.ram_limit_mb,
        "disk_limit_gb": server.disk_limit_gb,
        "disk_used_mb": disk_used,
        "disk_free_mb": disk_free,
    }


@router.post("/{server_id}/install")
def install_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.install")
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
    require_server_permission(user, server_id, db, "server.console.read")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    console_log = ""
    if plugin:
        console_log = plugin.get_console_log(server, lines=lines)
    return {"logs": console_log}


@router.get("/{server_id}/console/stream")
async def server_console_stream(
    server_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Live-Stream der Container-Logs als Server-Sent Events.

    SSE statt WebSocket: unidirektional reicht (Server → Client), keine neue
    Protokoll-Schicht, EventSource im Browser ohne extra Lib. Auth via Cookie
    + ``server.console.read`` (CSRF entfaellt bei GET).

    Implementierung: ``docker logs --follow --tail 200`` als async-Subprocess,
    Zeilen werden als ``data:``-Frames durchgereicht. Bei Client-Disconnect
    wird der Subprocess sauber beendet.
    """
    require_server_permission(user, server_id, db, "server.console.read")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    container = container_name_for(server.id)

    async def event_gen():
        # Container-Logs follow. ``--tail 200`` als Verlauf, danach Live.
        # ``--timestamps`` bewusst NICHT — der User-Konsole brauchen wir das
        # Rohformat (Datumsstempel sind im MSM-Console-Log eh nicht da).
        #
        # Wichtig: Der Endpoint darf NIEMALS roh abstürzen (FileNotFoundError
        # bei fehlendem docker-Binary im PATH des systemd-Units etc.).
        # Wir yielden stattdessen ein klares Error-Event + graceful Fallback
        # auf die MSM-internen Datei-Logs.
        proc: asyncio.subprocess.Process | None = None
        try:
            docker_bin = shutil.which("docker")
            if not docker_bin:
                # Saubere, client-freundliche Meldung statt harter Exception
                # (die vorher den ganzen SSE-Stream + ASGI-Handler killte).
                log_path = _console_log_path(server.id)
                msm_hint = ""
                if os.path.exists(log_path):
                    msm_hint = " (MSM-interne Install-/Start-Meldungen stehen unter dem statischen Console-Tab)"
                yield (
                    "event: error\n"
                    f"data: [MSM] Docker CLI nicht im PATH des Backends gefunden. "
                    f"Live-Container-Logs (docker logs --follow) sind nicht verfügbar{msm_hint}.\n\n"
                )
                return

            proc = await asyncio.create_subprocess_exec(
                docker_bin, "logs", "--follow", "--tail", "200", container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            while True:
                if await request.is_disconnected():
                    break
                # ~1 s Timeout, damit wir disconnect-checks pollen koennen.
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Heartbeat-Kommentar, haelt Proxies / EventSource am Leben.
                    yield ": keepalive\n\n"
                    continue
                if not raw:
                    # Subprocess hat EOF — Stream-Ende sauber kommunizieren.
                    yield "event: end\ndata: \n\n"
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                # SSE-Frame: jedes "data:" wird zu einer eigenen Zeile. ``\n``
                # darf NICHT im Wert vorkommen — wir splitten praeventiv.
                for chunk in line.split("\n"):
                    yield f"data: {chunk}\n"
                yield "\n"
        except Exception as exc:  # alle Fälle abfangen (FileNotFound, Permission, etc.)
            # Niemals rohe Exception in den ASGI-Handler propagieren.
            yield f"event: error\ndata: [MSM] Konsole-Stream Fehler: {type(exc).__name__}\n\n"
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            # Nginx-Buffering aus, sonst sieht der Client nichts bis zum Flush.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


class ConsoleInputBody(BaseModel):
    """Eingabezeile fuer die Konsole.

    Limit von 1 KiB pro POST schuetzt vor Missbrauch (z. B. Riesen-Payloads via
    XSS). Server-Spiele schicken in der Praxis Befehlszeilen << 1 KiB.
    """
    line: str = Field(..., min_length=0, max_length=1024)


@router.post("/{server_id}/console/input")
def server_console_input(
    server_id: int,
    body: ConsoleInputBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Schreibt ``body.line`` in den stdin des Container-Prozesses.

    Auth: Cookie + CSRF + ``server.console.write``. Die Eingabe selbst wird
    NICHT geloggt — sie kann sensibel sein (OAuth-Codes, RCON-Tokens, etc.).
    """
    require_server_permission(user, server_id, db, "server.console.write")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    container = container_name_for(server.id)
    if not docker_service.is_running(container):
        raise HTTPException(status_code=409, detail="Container läuft nicht")
    # Newline erzwingen — die meisten Game-Server lesen zeilenweise.
    data = body.line if body.line.endswith("\n") else body.line + "\n"
    result = docker_service.send_stdin(container, data)
    if not result["ok"]:
        # Generische Fehlermeldung — keine Container-Internas leaken.
        raise HTTPException(status_code=500, detail="Eingabe konnte nicht zugestellt werden")
    return {"ok": True}


@router.get("/{server_id}/logs")
def server_logs(server_id: int, lines: int = 100, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    require_server_permission(user, server_id, db, "server.console.read")
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
