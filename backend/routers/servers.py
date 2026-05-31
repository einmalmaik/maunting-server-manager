import asyncio
import os
import shutil
import threading
import time
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
from games.base import container_name_for, _console_log_path, _append_console_log
from services import EmailService, docker_service
from services.docker_iptables_service import accept_server as iptables_accept_server
from services.docker_iptables_service import revoke_server as iptables_revoke_server
from services.firewall_service import close_ports, open_ports
from services.network_interfaces_service import default_bind_ip, list_host_interfaces
from services.port_allocation_service import PortConflictError, allocate_ports
from services.port_role_service import blueprint_port_requirements, normalize_port_protocol
from services.scheduler_service import sync_server_restart_schedule
from services.server_lifecycle_service import restart_server_with_updates, get_server_lifecycle_lock
from services.install_update_lock_service import (
    INSTALL_UPDATE_ALREADY_RUNNING,
    release_install_update_lock,
    try_acquire_install_update_lock,
)

import logging
logger = logging.getLogger(__name__)

# ── Leichtergewichtiger, passiver Cache für Update-Checks im Status-Endpoint ──
# Zweck: Frontend-Badge (Update-Verfügbarkeit) ohne teure Calls (Workshop/Steam)
# bei jedem Poll. KISS + defensiv: TTL-basiert, nie status kaputt machen.
# TTL 5min reicht für Badge (Updates sind nicht sekündlich).
_UPDATE_CACHE: dict[int, dict] = {}
_UPDATE_CACHE_LOCK = threading.Lock()
_UPDATE_CACHE_TTL_SECONDS = 300

# _SERVER_OPERATION_LOCKS entfernt: alle destruktiven Lifecycle-Ops (start/stop/restart)
# verwenden nun EINHEITLICH get_server_lifecycle_lock aus server_lifecycle_service.
# Verhindert TOCTOU auf Firewall/iptables (Security-Finding). KISS + zentrale Serialisierung.


router = APIRouter(prefix="/api/servers", tags=["servers"])


def _port_requirements_for_server(server: Server, protocol_overrides: dict[str, str] | None = None) -> list[tuple[str, str]]:
    plugin = get_plugin(server.game_type)
    bp = plugin.get_blueprint() if plugin else None
    if bp:
        requirements = blueprint_port_requirements(bp.ports)
    else:
        requirements = [
            ("game", "udp"),
            ("query", "udp"),
            ("rcon", "tcp"),
        ]

    current_protocols = {
        p.role: normalize_port_protocol(p.protocol)
        for p in getattr(server, "ports", []) or []
    }
    overrides = {
        role: normalize_port_protocol(protocol)
        for role, protocol in (protocol_overrides or {}).items()
    }

    return [
        (role, overrides.get(role, current_protocols.get(role, proto)))
        for role, proto in requirements
    ]


def _install_update_busy_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": INSTALL_UPDATE_ALREADY_RUNNING,
            "message": f"errors.{INSTALL_UPDATE_ALREADY_RUNNING}",
        },
    )





@router.get("", response_model=list[ServerResponse])
def list_servers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Server]:
    return permission_service.list_visible_servers(db, user)


@router.post("", response_model=ServerResponse, status_code=201)
async def create_server(req: ServerCreate, db: Session = Depends(get_db), user: User = Depends(require_global("servers.create")), _: None = Depends(verify_csrf)) -> Server:

    base_dir = os.path.abspath(settings.servers_dir)

    plugin = get_plugin(req.game_type)
    bp = plugin.get_blueprint() if plugin else None

    # Map blueprint ports to stable, unique requirements [(role, protocol)].
    port_requirements = blueprint_port_requirements(bp.ports) if bp else [
        ("game", "udp"),
        ("query", "udp"),
        ("rcon", "tcp"),
    ]

    # Overrides mapping
    requested_ports = dict(req.ports or {})
    if req.game_port is not None:
        requested_ports["game"] = req.game_port
    if req.query_port is not None:
        requested_ports["query"] = req.query_port
    if req.rcon_port is not None:
        requested_ports["rcon"] = req.rcon_port

    bind_ip = req.public_bind_ip or default_bind_ip()
    try:
        allocated = allocate_ports(
            db,
            exclude_server_id=None,
            bind_ip=bind_ip or "0.0.0.0",
            port_requirements=port_requirements,
            requested_ports=requested_ports,
        )
    except PortConflictError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if isinstance(allocated, tuple) and len(allocated) == 3 and all(isinstance(x, int) for x in allocated):
        allocated = [
            ("game", allocated[0], "udp"),
            ("query", allocated[1], "udp"),
            ("rcon", allocated[2], "tcp"),
        ]

    # Placeholder-Row zuerst einfügen, um stabile PK (server.id) zu erhalten.
    # Danach install_dir = f".../{game_type}_{id}" - kollisionsfrei über alle Zeit,
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
        restart_times_utc=req.restart_times_utc,
        cpu_limit_percent=req.cpu_limit_percent,
        ram_limit_mb=req.ram_limit_mb,
        disk_limit_gb=req.disk_limit_gb,
        public_bind_ip=bind_ip,
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    install_lock_acquired = False
    if plugin:
        install_lock_acquired = try_acquire_install_update_lock(
            server.id, "install"
        )
        if not install_lock_acquired:
            db.delete(server)
            db.commit()
            raise _install_update_busy_error()

    install_started = False
    try:
        from models.server_port import ServerPort
        for role, port_val, proto in allocated:
            db.add(ServerPort(server_id=server.id, role=role, port=port_val, protocol=proto))
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

        # Verzeichnis anlegen - wird vom Panel-User (`msm`) angelegt. Vor jedem
        # Container-Start normalisiert docker_service.repair_bind_mount_permissions()
        # Owner/Rechte im Container-Kontext, damit Runtime (z. B. Wine) und Panel
        # konsistent auf dieselben Dateien zugreifen können.
        # exist_ok=False ist jetzt sicher (Guard oben).
        try:
            os.makedirs(install_dir, exist_ok=False)
            os.chmod(install_dir, 0o777)
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
            try:
                result = plugin.install(server)
            except Exception:
                raise HTTPException(status_code=500, detail="Installation konnte nicht gestartet werden")
            if "error" in result:
                raise HTTPException(status_code=500, detail=result["error"])
            install_started = True
    except Exception:
        if install_lock_acquired and not install_started:
            release_install_update_lock(server.id)
        raise

    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_installed_notification(user.email, user.username, server.name)

    sync_server_restart_schedule(server)
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

    old_ports = [(p.port, p.protocol, p.role) for p in server.ports]
    old_bind_ip = server.public_bind_ip

    payload = req.model_dump(exclude_unset=True)
    port_fields = {"game_port", "query_port", "rcon_port", "ports", "port_protocols"}
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
        port_requirements = _port_requirements_for_server(
            server,
            protocol_overrides=req.port_protocols,
        )

        current_ports = {p.role: p.port for p in server.ports}
        requested_ports = dict(req.ports or {})
        
        if req.game_port is not None:
            requested_ports["game"] = req.game_port
        if req.query_port is not None:
            requested_ports["query"] = req.query_port
        if req.rcon_port is not None:
            requested_ports["rcon"] = req.rcon_port

        for role, _ in port_requirements:
            if role not in requested_ports:
                requested_ports[role] = current_ports.get(role)

        bind_ip_for_check = payload.get("public_bind_ip", old_bind_ip) or "0.0.0.0"
        try:
            allocated = allocate_ports(
                db,
                exclude_server_id=server.id,
                bind_ip=bind_ip_for_check,
                port_requirements=port_requirements,
                requested_ports=requested_ports,
            )
        except PortConflictError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))

        if isinstance(allocated, tuple) and len(allocated) == 3 and all(isinstance(x, int) for x in allocated):
            allocated = [
                ("game", allocated[0], "udp"),
                ("query", allocated[1], "udp"),
                ("rcon", allocated[2], "tcp"),
            ]

        from models.server_port import ServerPort
        db.query(ServerPort).filter(ServerPort.server_id == server.id).delete()
        for role, port_val, proto in allocated:
            db.add(ServerPort(server_id=server.id, role=role, port=port_val, protocol=proto))
        db.commit()

    # Standard-Update
    for key, val in payload.items():
        if key not in ("game_port", "query_port", "rcon_port", "ports", "port_protocols"):
            setattr(server, key, val)
    db.commit()
    db.refresh(server)

    if {"auto_restart", "restart_interval_hours", "restart_time_utc", "restart_times_utc"} & set(payload.keys()):
        sync_server_restart_schedule(server)

    if network_change:
        # Alte Firewall- und iptables-Regeln entfernen, neue anlegen - ABER
        # nur, wenn der Server gerade laeuft. Fuer gestoppte Server bleiben die
        # Regeln zu (Lifecycle-Kopplung).
        plugin = get_plugin(server.game_type)
        was_running = plugin is not None and docker_service.is_running(container_name_for(server.id))

        if was_running:
            close_ports(old_ports)
            iptables_revoke_server(
                server.name,
                old_bind_ip or "",
                old_ports,
            )
            # Container stoppen - Plugin.start() legt ihn mit den neuen Ports/
            # Bind-Werten frisch an.
            plugin.stop(server)
            new_ports = [(p.port, p.protocol, p.role) for p in server.ports]
            open_ports(server.name, new_ports)
            iptables_accept_server(
                server.name,
                server.public_bind_ip or "",
                new_ports,
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
    4. Backup-Verzeichnis (alle TAR-Archive) vom Host entfernen - DB-Cascade
       räumt die Backup-Records selbst.
    5. MSM-Console-Log-Verzeichnis entfernen.
    6. DB-Eintrag löschen (Cascade entfernt Permissions/Mods/Backups).
    """
    if not permission_service.has_global_permission(db, user, "servers.delete"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    # 1. Container stoppen + entfernen (idempotent - force killt running)
    container = container_name_for(server.id)
    docker_service.remove(container, force=True)

    # 2. Firewall- und iptables-Regeln schließen
    ports_list = [(p.port, p.protocol, p.role) for p in server.ports]
    close_ports(ports_list)
    iptables_revoke_server(
        server.name,
        server.public_bind_ip or "",
        ports_list,
    )

    # 3. Install-Verzeichnis physisch löschen
    install_dir = server.install_dir
    dir_removed = False
    if install_dir and os.path.exists(install_dir):
        repair = docker_service.repair_bind_mount_permissions(install_dir)
        if not repair.get("ok"):
            logger.warning(
                "Install-Verzeichnis-Rechte konnten vor Delete nicht normalisiert werden: %s",
                repair.get("error") or "unbekannter Fehler",
            )
        try:
            shutil.rmtree(install_dir)
            dir_removed = True
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Abbruch (Atomar): Install-Verzeichnis konnte nicht gelöscht werden. Bitte behebe die Berechtigungen (z. B. chown/chmod) oder lösche den Ordner manuell, bevor du den Server im Panel entfernst: {e}"
            )

    # 4. Backup-Verzeichnis (Files) löschen - DB-Cascade räumt Records
    backup_dir = f"/opt/msm/backups/{server.id}"
    backups_removed = False
    if os.path.exists(backup_dir):
        try:
            shutil.rmtree(backup_dir)
            backups_removed = True
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Abbruch (Atomar): Backup-Verzeichnis konnte nicht gelöscht werden. Bitte lösche den Ordner manuell: {e}"
            )

    # 5. MSM-Console-Log-Verzeichnis räumen
    console_log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs",
        str(server.id),
    )
    if os.path.exists(console_log_dir):
        try:
            shutil.rmtree(console_log_dir)
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Abbruch (Atomar): Console-Log-Verzeichnis konnte nicht gelöscht werden. Bitte lösche den Ordner manuell: {e}"
            )

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
        # Symlinks gelten nicht als vorhanden - Defense-in-Depth.
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
    # starten - sonst würde Docker auf 0.0.0.0 binden und die UFW-Falle auslösen.
    if not server.public_bind_ip:
        raise HTTPException(
            status_code=400,
            detail=(
                "Server hat keine Bind-IP konfiguriert. Bitte im Server-Detail "
                "eine Public-IP zuweisen, bevor er gestartet wird."
            ),
        )

    # NEU: Pre-Check fuer manualUpload - VOR Firewall-Regeln.
    bp = plugin.get_blueprint()
    if bp and bp.source.type == BlueprintSourceType.MANUAL_UPLOAD:
        manual = bp.source.manual
        assert manual is not None
        missing = _missing_required_files(server.install_dir, manual.requiredFiles)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Server kann nicht gestartet werden - folgende Dateien fehlen "
                    f"im Server-Verzeichnis: {', '.join(missing)}. "
                    "Bitte über den File Manager hochladen (Archive können per "
                    "Rechtsklick → Entpacken ausgepackt werden)."
                ),
            )

    lock = get_server_lifecycle_lock(server.id)
    async with lock:
        mod_updates: list[dict] = []
        try:
            plugin.prepare_for_updates(server)
            mod_updates = plugin.check_for_mod_updates(server)
        except Exception as exc:
            _append_console_log(
                server.id,
                f"[MSM] prepare_for_updates / Mod-Update-Check beim Start fehlgeschlagen (nicht kritisch): {exc}\n",
            )
            mod_updates = []

        update_lock_acquired = False
        if mod_updates:
            update_lock_acquired = try_acquire_install_update_lock(
                server.id, "start_update"
            )
            if not update_lock_acquired:
                raise _install_update_busy_error()

        ports_list = [(p.port, p.protocol, p.role) for p in server.ports]
        try:
            # Firewall-Regeln öffnen vor Container-Start.
            open_ports(server.name, ports_list)
            iptables_accept_server(
                server.name,
                server.public_bind_ip,
                ports_list,
            )

            # Plugin-Aufrufe rufen blockierende Docker-Subprozesse auf. In einer
            # async-Route blockieren sie den gesamten Uvicorn-Event-Loop - alle anderen
            # Requests hängen mit. Daher in einen Threadpool auslagern.

            # Updater-Hook vor jedem Start (vor allem für Workshop-Mod-Checks nach Neustart)
            # Optional: Auch im normalen Start-Pfad Workshop-Mod-Updates ausführen (KISS,
            # falls ein Server lange stand und in der Zwischenzeit Mod-Updates auf Steam
            # erschienen sind). Primär aber der Restart-Pfad (wie Server-Datei-Updates).
            try:
                # Optionale Execution im direkten Start (neben dem Pflicht-Restart-Pfad)
                if mod_updates:
                    _append_console_log(
                        server.id,
                        f"[MSM] {len(mod_updates)} Workshop-Mod(s) beim Start erkannt – "
                        "führe Download via install_mod/run_steamcmd_workshop_download aus...\n"
                    )
                    mod_res = await asyncio.to_thread(
                        plugin.perform_workshop_mod_updates, server
                    )
                    if not mod_res.get("ok", False):
                        _append_console_log(
                            server.id,
                            f"[MSM] Workshop-Mod-Update beim Start hatte Probleme (nicht kritisch): {mod_res.get('error') or mod_res}\n"
                        )
            except Exception as exc:
                _append_console_log(
                    server.id,
                    f"[MSM] prepare_for_updates / Mod-Update beim Start fehlgeschlagen (nicht kritisch): {exc}\n",
                )
        finally:
            if update_lock_acquired:
                release_install_update_lock(server.id)

        db.refresh(server)

        # Pre-Start-Backup (best-effort, nach Permission + Lock, vor docker run)
        if server.backup_on_start:
            from services.backup_service import run_backup
            try:
                run_backup(server.id, db, timeout_seconds=300)
            except Exception:
                logger.warning("Pre-Start-Backup fehlgeschlagen für Server %s (details redacted for security)", server.id)
                # NO Hard-Fail: Server startet trotzdem (best-effort)

        # AUFGABE 4A: transient status VOR Docker-Operation
        server.status = "starting"
        db.commit()

        result = await asyncio.to_thread(plugin.start, server)
        if "error" in result:
            # Container-Start fehlgeschlagen - Firewall-Regeln wieder schließen.
            close_ports(ports_list)
            iptables_revoke_server(
                server.name,
                server.public_bind_ip,
                ports_list,
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

    # WICHTIG: Lock mit start/restart teilen (via get_server_lifecycle_lock).
    # Verhindert Race auf stop + close/revoke vs. concurrent start/restart/firewall.
    # Früher fehlte hier jeder Lock → TOCTOU mit Restart-Pfad.
    lock = get_server_lifecycle_lock(server.id)
    async with lock:
        # AUFGABE 4A: transient status VOR Docker-Operation (für Echtzeit-Feedback)
        server.status = "stopping"
        db.commit()
        # Siehe start_server: docker stop ist synchron und kann bis zum
        # Graceful-Timeout dauern. Threadpool hält den Event-Loop frei.
        result = await asyncio.to_thread(plugin.stop, server)
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        server.status = "stopped"
        db.commit()

        # Firewall- und iptables-Regeln nach Container-Stop schließen.
        ports_list = [(p.port, p.protocol, p.role) for p in server.ports]
        close_ports(ports_list)
        iptables_revoke_server(
            server.name,
            server.public_bind_ip or "",
            ports_list,
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
    # AUFGABE 4A: transient now set only inside locked service (before first docker stop) for consistency with stop/start + to avoid duplicate commit / small TOCTOU (review Issue 5/10)
    result = await restart_server_with_updates(db, server)
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_status_notification(user.email, user.username, server.name, "neugestartet")
    return result


@router.post("/{server_id}/kill")
async def kill_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Erzwungenes Beenden (Docker force remove). Nur für running/stopping/restarting sichtbar im UI.
    Permission "server.kill" (Naming analog zu server.stop, nicht server.power.* für Code-Konsistenz mit bestehenden server.* Keys).
    """
    require_server_permission(user, server_id, db, "server.kill")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    lock = get_server_lifecycle_lock(server.id)
    async with lock:
        from games.base import container_name_for
        from services import docker_service
        container = container_name_for(server.id)
        # AUFGABE fix (review Issue 1/2 + Security Finding 1): transient "stopping" (reuses existing amber/disable UI logic for in-flight kill; KISS no new i18n/statusClasses) + commit BEFORE docker remove (prevents stale "running" on hang, matches stop/start/restart invariant). Result checked; error before any final DB mutation (no false success).
        server.status = "stopping"
        db.commit()
        result = docker_service.remove(container, force=True)
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=500, detail="Erzwungenes Beenden fehlgeschlagen")
        server.status = "stopped"
        server.status_message = "Erzwungen beendet"
        db.commit()

    return {"message": "Server wurde erzwungen beendet"}


def _disk_free_mb(path: str) -> int | None:
    """Liefert freien Speicher auf dem Filesystem von `path` in MB.

    Wir nutzen os.statvfs (Linux/Unix). Bei Fehler None - der Frontend zeigt
    dann '-' an, statt zu crashen.
    """
    try:
        if not path:
            return None
        if not hasattr(os, "statvfs"):
            return None
        # Falls install_dir noch nicht existiert, das Eltern-Verzeichnis nehmen
        target = path if os.path.exists(path) else os.path.dirname(path) or "/"
        st = os.statvfs(target)
        return int((st.f_bavail * st.f_frsize) // (1024 * 1024))
    except (AttributeError, OSError):
        return None


def _get_cached_update_availability(server, plugin) -> dict:
    """Leichtgewichtige, cached/passive Ermittlung der Update-Verfügbarkeit.

    Ruft plugin.check_for_* NUR bei TTL-Miss (5min). Status-Endpoint bleibt schnell.
    Defensiv + KISS: fängt alles ab, liefert Defaults, keine Seiteneffekte.
    Mod-Updates werden autonom vorbereitet und sind kein Server-Update-Badge.
    """
    if not plugin or not getattr(server, "id", None):
        return {
            "server_file_update_available": False,
            "server_file_update_reason": None,
            "mod_updates_available": [],
        }

    sid = server.id
    now = time.time()
    with _UPDATE_CACHE_LOCK:
        cached = _UPDATE_CACHE.get(sid)
    if cached and (now - cached.get("ts", 0) < _UPDATE_CACHE_TTL_SECONDS):
        return cached["data"]

    # Cache-Miss → echte (aber seltene) Checks
    try:
        check_server = getattr(plugin, "check_for_server_file_update", None)
        server_update = check_server(server) if check_server else {}
        if not isinstance(server_update, dict):
            server_update = {}
        server_file_available = server_update.get("action") == "update"
        server_file_reason = server_update.get("reason") if server_file_available else None

        data = {
            "server_file_update_available": bool(server_file_available),
            "server_file_update_reason": server_file_reason,
            "mod_updates_available": [],
        }
        with _UPDATE_CACHE_LOCK:
            _UPDATE_CACHE[sid] = {"ts": now, "data": data}
        return data
    except Exception as exc:
        # Niemals Status-Endpoint durch Update-Checks zum Absturz bringen.
        # Badge zeigt einfach "kein Update" – sicher + wartbar.
        logger.warning(
            "Passive update check failed for server %s (non-fatal): %s",
            sid, exc
        )
        fallback = {
            "server_file_update_available": False,
            "server_file_update_reason": None,
            "mod_updates_available": [],
        }
        with _UPDATE_CACHE_LOCK:
            _UPDATE_CACHE[sid] = {"ts": now, "data": fallback}
        return fallback


@router.get("/{server_id}/status", response_model=ServerStatusResponse)
def server_status(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    require_server_permission(user, server_id, db, "server.view")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    disk_used = server.disk_usage_mb
    disk_free = _disk_free_mb(server.install_dir) if server.install_dir else None

    # Update-Info leichtgewichtig + cached (nicht bei jedem Status-Call teuer).
    # Ergebnisse von check_for_server_file_update + check_for_mod_updates.
    update_info = _get_cached_update_availability(server, plugin)

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
            "server_file_update_available": update_info["server_file_update_available"],
            "server_file_update_reason": update_info["server_file_update_reason"],
            "mod_updates_available": update_info["mod_updates_available"],
        }
    plugin_status = plugin.get_status(server)
    # installing/updating/error nicht ueberschreiben - Background-Thread oder
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
        "server_file_update_available": update_info["server_file_update_available"],
        "server_file_update_reason": update_info["server_file_update_reason"],
        "mod_updates_available": update_info["mod_updates_available"],
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
    if not try_acquire_install_update_lock(server.id, "install"):
        raise _install_update_busy_error()
    try:
        server.status = "installing"
        server.status_message = "Installation gestartet"
        db.commit()
        result = plugin.install(server)
    except Exception:
        release_install_update_lock(server.id)
        raise HTTPException(status_code=500, detail="Installation konnte nicht gestartet werden")
    if "error" in result:
        release_install_update_lock(server.id)
        raise HTTPException(status_code=500, detail=result["error"])
    return {"message": "Installation gestartet", **result}


@router.get("/{server_id}/console/stream")
async def server_console_stream(
    server_id: int,
    request: Request,
    after: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Live-Stream der Console als Server-Sent Events.

    Single Source of Truth (KISS): die MSM-Console-Logdatei
    ``backend/logs/<server_id>/console.log``. Sie sammelt:

    - Install-/Update-Output (SteamCMD, HTTP-Source, manuelle Hinweise)
    - Lifecycle-Events (``[MSM] Container gestartet/gestoppt``, Fehler)
    - Live-Container-Stdout/Stderr während der Server läuft (siehe unten)

    Damit auch Live-Container-Output in dieselbe Datei landet, koppelt der
    Endpoint den Rootless-Docker-Logstream aus ``docker_service``: dessen
    Output wird parallel an den Stream geyielded. Die Datei bleibt der primäre
    Backlog, Docker liefert nur die laufenden neuen Zeilen.

    SSE statt WebSocket: unidirektional reicht, EventSource im Browser ohne
    extra Lib. Auth via Cookie + ``server.console.read`` (CSRF entfällt bei
    GET). Bei Client-Disconnect werden Subprozess + Hintergrund-Tasks sauber
    beendet.
    """
    require_server_permission(user, server_id, db, "server.console.read")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    container = container_name_for(server.id)
    log_path = _console_log_path(server.id)

    return StreamingResponse(
        _console_event_stream(request, container, log_path, after_bytes=after),
        media_type="text/event-stream",
        headers={
            # Nginx/Caddy-Buffering aus, sonst sieht der Client nichts bis zum Flush.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


def _sse_data(line: str, event_id: int | None = None) -> str:
    """Eine Logzeile als SSE ``data:``-Frame kodieren.

    Mehrzeilige Werte werden zeilenweise als mehrere ``data:``-Felder
    geschickt - das ist die SSE-Spezifikation für Newlines im Payload.
    """
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return prefix + "".join(f"data: {part}\n" for part in line.split("\n")) + "\n"


async def _console_event_stream(
    request: Request,
    container: str,
    log_path: str,
    *,
    after_bytes: int | None = None,
):
    """Generator für den Console-SSE-Stream.

    DESIGN-RATIONALE (explizite KISS-Ausnahme, dokumentiert per AGENTS §1.5 + general-3 review):
    - Ziel: "Direkt volle History beim Tab-Öffnen + autom. Re-Buffer bei Container-Start" OHNE Tab-Switch.
    - MSM-Logdatei = Single Source für alle [MSM] Lifecycle/Install-Events (zentral, persistent über Restarts).
    - Docker --follow --tail 200 (dann 0) = komplementärer Container-Stdout für Game-Logs beim Start (keine Garantie auf Dupe-Freiheit, aber praktisch ok).
    - Dual-Task + Queue + inner reconnect/terminate-Handling + Keepalive nötig, weil:
      a) File-Poll für Events die NICHT im Container-Log landen.
      b) Docker-Logs bei Start frischen Tail braucht, später live.
      c) Rotation, disconnect, subprocess races müssen sauber sein (kein Leak bei vielen Consoles).
    - Einfachere Alternative (ein Tail, oder nur docker, oder tail -f via shell) würde Feature oder Robustheit verlieren.
    - Keine Pipeline/Orchestrator (verboten per examples.md); expliziter Generator mit 2 Tasks.
    - Tests (test_console_endpoints) + runtime decken Grundpfad; volle E2E mit realem Game-Container ist env-limitiert.
    Siehe auch ServerConsolePanel.tsx (EventSource Mount) und AGENTS KISS: "Kann ich simpler ohne Feature-Verlust?" - hier dokumentiert nein.
    """

    # 1. Initial-Backlog senden (Install-/Lifecycle-Historie).
    initial_bytes = max(after_bytes or 0, 0)
    if os.path.exists(log_path):
        try:
            with open(log_path, "rb") as f:
                f.seek(initial_bytes)
                content_bytes = f.read()
            pos = initial_bytes
            for raw_line in content_bytes.splitlines(keepends=True):
                pos += len(raw_line)
                line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                yield _sse_data(line, pos)
            initial_bytes = pos
        except OSError:
            # Datei verschwand zwischen exists() und open() - kein Problem,
            # Live-Tail fängt neue Schreibvorgänge.
            initial_bytes = max(after_bytes or 0, 0)

    # 2. Live-Quellen einrichten.
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def _tail_file():
        """Poll die MSM-Logdatei alle 250 ms auf neue Bytes (Lifecycle/Install)."""
        pos = initial_bytes
        while True:
            await asyncio.sleep(0.25)
            try:
                size = os.path.getsize(log_path)
            except OSError:
                continue
            if size < pos:
                # Datei wurde rotiert/geleert → von vorn beginnen.
                pos = 0
            if size <= pos:
                continue
            try:
                with open(log_path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
                pos = size
            except OSError:
                continue
            read_pos = pos - len(chunk)
            for raw_line in chunk.splitlines(keepends=True):
                read_pos += len(raw_line)
                line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                await queue.put(_sse_data(line, read_pos))

    async def _tail_docker():
        """Streame Live-Container-Stdout/Stderr (oder melde, dass Docker fehlt).

        `tail=200` als historischer Backlog des Container-Outputs -
        komplementaer zum MSM-Logdatei-Backlog (Install/Lifecycle), keine
        Duplikate. Fehlende Container/Rootless-Docker-Verbindung sind kein
        Fehler: dann bleibt der Stream einfach still und liefert nur
        File-Lifecycle-Events.
        """
        if not docker_service.is_available():
            await queue.put(
                _sse_data(
                    "[MSM] Rootless Docker Daemon not running for user msm - Live-Container-Logs deaktiviert."
                )
            )
            return
        tail = 0 if after_bytes is not None else 200
        while True:
            saw_line = False
            async for line in docker_service.stream_logs(container, tail=tail):
                saw_line = True
                await queue.put(_sse_data(line))
            if saw_line:
                tail = 0
            await asyncio.sleep(1.0)

    tasks = [
        asyncio.create_task(_tail_file()),
        asyncio.create_task(_tail_docker()),
    ]

    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # SSE-Keepalive - verhindert dass Proxies die Verbindung kappen.
                yield ": keepalive\n\n"
                continue
            yield frame
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


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
    NICHT geloggt - sie kann sensibel sein (OAuth-Codes, RCON-Tokens, etc.).
    """
    require_server_permission(user, server_id, db, "server.console.write")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    container = container_name_for(server.id)
    if not docker_service.is_running(container):
        raise HTTPException(status_code=409, detail="Container läuft nicht")
    # Newline erzwingen - die meisten Game-Server lesen zeilenweise.
    data = body.line if body.line.endswith("\n") else body.line + "\n"
    result = docker_service.send_stdin(container, data)
    if not result["ok"]:
        # Generische Fehlermeldung - keine Container-Internas leaken.
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
