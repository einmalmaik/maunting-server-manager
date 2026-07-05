import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from database import get_db
from models import Server, User
from schemas import ServerCreate, ServerCreateResponse, ServerResponse, ServerUpdate, ServerStatusResponse
from schemas.postgres import PostgresOneTimeCredential
from dependencies import (
    get_current_user,
    get_current_user_for_ws,
    require_global,
    require_server_permission,
    verify_csrf,
)
from services import permission_service, postgres_service
from blueprints.schema import BlueprintSourceType, _is_safe_relative_path
from games import get_plugin
from games.base import container_name_for, _console_log_path, _append_console_log
from services import EmailService, docker_service
from services import exec_service
from services.docker_iptables_service import accept_server as iptables_accept_server
from services.docker_iptables_service import revoke_server as iptables_revoke_server
from services.firewall_service import close_ports, open_ports
from services.network_interfaces_service import default_bind_ip, list_host_interfaces
from services.port_allocation_service import PortConflictError, allocate_ports
from services.port_role_service import blueprint_port_requirements, normalize_port_protocol
from services.scheduler_service import sync_server_restart_schedule
from services.server_lifecycle_service import (
    LifecycleNotification,
    queue_lifecycle_operation,
    should_preserve_lifecycle_status,
)
from services.console_stream_service import connect as ws_connect
from services.install_update_lock_service import (
    INSTALL_UPDATE_ALREADY_RUNNING,
    release_install_update_lock,
    try_acquire_install_update_lock,
)

import logging
logger = logging.getLogger(__name__)


def _normalize_server_restart_mode(server: Server) -> None:
    """Stellt sicher, dass nicht beide Auto-Restart-Modi (Intervall + feste Zeiten) gleichzeitig aktiv sind.

    Intervall hat Vorrang (konsistent mit sync_server_restart_schedule).
    Verhindert „sowohl als auch“-Zustände in der DB durch direkte PATCHes, Legacy-Daten,
    fehlende Client-Normalisierung oder Migrationen.

    KISS: zentrale Normalisierung an der Persistenzstelle.
    """
    interval = getattr(server, "restart_interval_hours", None)
    times = getattr(server, "restart_times_utc", None) or getattr(server, "restart_time_utc", None)

    if interval:
        # Interval wins: clear any fixed times
        server.restart_time_utc = None
        server.restart_times_utc = None
    elif times:
        # Only fixed times: clear interval
        server.restart_interval_hours = None

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _server_restart_status_fields(server: Server) -> dict:
    return {
        "started_at": server.last_started_at,
        "last_auto_restart_attempt_at": server.last_auto_restart_attempt_at,
        "last_auto_restart_completed_at": server.last_auto_restart_completed_at,
        "last_auto_restart_status": server.last_auto_restart_status,
        "next_auto_restart_at": server.next_auto_restart_at,
    }


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


@router.post("", response_model=ServerCreateResponse, status_code=201)
async def create_server(req: ServerCreate, db: Session = Depends(get_db), user: User = Depends(require_global("servers.create")), _: None = Depends(verify_csrf)) -> ServerCreateResponse:

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
    _normalize_server_restart_mode(server)
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
    created_install_dir = False
    server_deleted = False
    postgres_credentials: list[dict] = []
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
            server_deleted = True
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
            created_install_dir = True
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

        if req.postgres_enabled:
            try:
                postgres_credentials = postgres_service.provision_server_databases(
                    db,
                    server,
                    req.postgres_database_count or 1,
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"PostgreSQL-Provisionierung fehlgeschlagen: {exc}") from exc

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
        if not install_started and not server_deleted:
            try:
                postgres_service.drop_server_resources(db, server.id)
            except Exception:
                db.rollback()
            try:
                db.delete(server)
                db.commit()
            except Exception:
                db.rollback()
            if created_install_dir and os.path.exists(server.install_dir):
                try:
                    shutil.rmtree(server.install_dir)
                except OSError:
                    logger.warning("Install-Verzeichnis konnte nach Create-Abbruch nicht entfernt werden")
        raise

    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_installed_notification(user.email, user.username, server.name)

    sync_server_restart_schedule(server)
    response = ServerCreateResponse.model_validate(server)
    response.postgres_credentials = [
        PostgresOneTimeCredential.model_validate(item)
        for item in postgres_credentials
    ]
    return response


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
    _normalize_server_restart_mode(server)
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

    # 6. Verwaltete PostgreSQL-Ressourcen loeschen. Bei Fehler bleibt der
    # Server-Datensatz erhalten, damit der Cleanup erneut versucht werden kann.
    try:
        postgres_service.drop_server_resources(db, server.id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Abbruch: PostgreSQL-Ressourcen konnten nicht gelöscht werden. Bitte später erneut versuchen: {e}",
        )

    # 7. DB-Eintrag löschen (Cascade entfernt Permissions/Mods/Backups)
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

    return queue_lifecycle_operation(
        db,
        server,
        "start",
        LifecycleNotification(user.email, user.username, user.email_notifications),
    )


@router.post("/{server_id}/stop")
async def stop_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.stop")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")

    return queue_lifecycle_operation(
        db,
        server,
        "stop",
        LifecycleNotification(user.email, user.username, user.email_notifications),
    )


@router.post("/{server_id}/restart")
async def restart_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.restart")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    return queue_lifecycle_operation(
        db,
        server,
        "restart",
        LifecycleNotification(user.email, user.username, user.email_notifications),
    )


@router.post("/{server_id}/auth-setup/cancel")
async def cancel_auth_setup(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Bricht einen laufenden Auth-Setup-Recovery-Vorgang ab.

    Wird aufgerufen, wenn der User den interaktiven Auth-Flow manuell abbrechen
    will (z.B. weil er das Spiel doch nicht neu authentifizieren moechte oder
    lieber die Credentials manuell austauscht).

    Setzt ``auth_required=False``, ruft ``docker_service.stop`` auf den wartenden
    Container, und loggt eine MSM-Message in die Konsole. Der eigentliche
    Recovery-Thread prueft ``auth_required`` via on_status bei seinen naechsten
    Status-Updates und beendet sich selbst.
    """
    require_server_permission(user, server_id, db, "server.start")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    if not server.auth_required:
        raise HTTPException(status_code=409, detail="Server ist nicht im Auth-Setup-Modus")

    from services import docker_service
    container_name = container_name_for(server.id)
    server.auth_required = False
    server.status_message = "Auth-Setup vom User abgebrochen"
    db.commit()
    stop_result = docker_service.stop(container_name, timeout=10)
    _append_console_log(server.id, "[MSM] Auth-Setup vom User abgebrochen.\n")
    return {
        "message": "Auth-Setup abgebrochen",
        "container_stopped": stop_result.get("ok", False),
    }


@router.post("/{server_id}/kill")
async def kill_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Erzwungenes Beenden (Docker force remove). Als Notfall auch während start/restart nutzbar (emergency override des Job-Locks).
    Permission "server.kill" (Naming analog zu server.stop, nicht server.power.* für Code-Konsistenz mit bestehenden server.* Keys).
    """
    require_server_permission(user, server_id, db, "server.kill")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    return queue_lifecycle_operation(
        db,
        server,
        "kill",
        LifecycleNotification(user.email, user.username, user.email_notifications),
    )


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


def _get_cached_update_availability(server, plugin, *, force: bool = False) -> dict:
    """Leichtgewichtige Ermittlung der Server-Datei-Update-Verfügbarkeit.

    Ruft plugin.check_for_server_file_update nur bei TTL-Miss (5min) oder force=True.
    Mod-Updates sind kein Server-Update-Badge (eigener Mod-Manager-Check).
    """
    empty = {
        "server_file_update_available": False,
        "server_file_update_reason": None,
        "mod_updates_available": [],
    }
    if not plugin or not getattr(server, "id", None):
        return empty

    sid = server.id
    now = time.time()
    if not force:
        with _UPDATE_CACHE_LOCK:
            cached = _UPDATE_CACHE.get(sid)
        if cached and (now - cached.get("ts", 0) < _UPDATE_CACHE_TTL_SECONDS):
            return cached["data"]

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
        logger.warning(
            "Passive update check failed for server %s (non-fatal): %s",
            sid, exc
        )
        fallback = dict(empty)
        with _UPDATE_CACHE_LOCK:
            _UPDATE_CACHE[sid] = {"ts": now, "data": fallback}
        return fallback


class ServerFileUpdateCheckResponse(BaseModel):
    server_file_update_available: bool = False
    server_file_update_reason: str | None = None


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
            "uptime_seconds": server.uptime_seconds,
            "cpu_limit_percent": server.cpu_limit_percent,
            "ram_limit_mb": server.ram_limit_mb,
            "disk_limit_gb": server.disk_limit_gb,
            "disk_used_mb": disk_used,
            "disk_free_mb": disk_free,
            "server_file_update_available": update_info["server_file_update_available"],
            "server_file_update_reason": update_info["server_file_update_reason"],
            "mod_updates_available": update_info["mod_updates_available"],
            **_server_restart_status_fields(server),
        }
    plugin_status = plugin.get_status(server)
    # installing/updating/error/failed nicht ueberschreiben. Laufende Lifecycle-
    # Jobs behalten ihre transienten Stati, bis der Background-Worker finalisiert.
    if server.status not in ("installing", "updating", "error", "failed") and not should_preserve_lifecycle_status(server.id, server.status):
        server.status = plugin_status.status
        server.status_message = plugin_status.message or ""
        if plugin_status.status == "running" and plugin_status.started_at is not None:
            server.last_started_at = plugin_status.started_at
        elif plugin_status.status != "running":
            server.last_started_at = None
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
        **_server_restart_status_fields(server),
    }


@router.post(
    "/{server_id}/check-server-file-updates",
    response_model=ServerFileUpdateCheckResponse,
)
def check_server_file_updates(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Manueller Spiel-/Server-Datei-Update-Check (wie Workshop „Updates prüfen“).

    Umgeht den 5-Minuten-Status-Cache und aktualisiert die Badge-Daten.
    """
    require_server_permission(user, server_id, db, "server.view")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    info = _get_cached_update_availability(server, plugin, force=True)
    return {
        "server_file_update_available": info["server_file_update_available"],
        "server_file_update_reason": info["server_file_update_reason"],
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


# ── Erlaubte Origins fuer WebSocket-Upgrades ───────────────────────────────
# Dieselbe Logik wie die CORS-Middleware: in Dev mehr, in Prod strikt.
_WS_ALLOWED_ORIGINS: tuple[str, ...] = (
    settings.panel_url,
    "http://localhost",
    "http://localhost:5173",
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
)


def _ws_origin_allowed(origin: str | None) -> bool:
    """Prueft den Origin-Header des WS-Upgrade-Requests. SameSite-Cookie + Origin-Check
    ersetzen die fehlende CSRF-Pruefung (WS sind keine 'simple requests').
    """
    if not origin:
        return False
    return origin.rstrip("/") in {o.rstrip("/") for o in _WS_ALLOWED_ORIGINS}


@router.websocket("/{server_id}/console/ws")
async def server_console_ws(websocket: WebSocket, server_id: int) -> None:
    """Live-Stream der Server-Konsole als WebSocket.

    Auth: Cookie-Auth im WS-Handshake (genauso wie beim HTTP-Pfad), danach
    Server-Permission ``server.console.read``. Origin-Check ersetzt den CSRF-Schutz
    fuer WS-Upgrades.

    Optional ``?last_id=<n>`` Query-Param: Replay-Resume nach Reconnect — der
    Server spult nur Zeilen mit id > last_id aus dem Ring-Buffer ab und macht
    dann mit Live-Stream weiter. Ohne last_id wird der volle Backlog gesendet.

    Frame-Format: JSON ``{"id": int, "ts": iso, "source": "msm"|"docker", "text": str}``.
    Eingehende Frames: vorerst nur Heartbeat ``{"action": "ping"}`` -> ``{"action": "pong"}``.
    Stdin laeuft weiterhin ueber ``POST /api/servers/{id}/console/input``.
    """
    origin = websocket.headers.get("origin")
    if not _ws_origin_allowed(origin):
        await websocket.close(code=1008)  # 1008 = "policy violation"
        return

    db = SessionLocal()
    try:
        try:
            user = get_current_user_for_ws(websocket, db)
            server = db.query(Server).filter(Server.id == server_id).first()
            if not server:
                await websocket.close(code=1008)
                return
            require_server_permission(user, server_id, db, "server.console.read")
        except HTTPException:
            await websocket.close(code=1008)
            return
        finally:
            db.close()

        container = container_name_for(server.id)
        log_path = _console_log_path(server.id)
        last_id_raw = websocket.query_params.get("last_id")
        last_id: int | None = None
        if last_id_raw is not None:
            try:
                last_id = int(last_id_raw)
            except ValueError:
                last_id = None

        await ws_connect(
            websocket,
            server_id=server_id,
            container=container,
            log_path=log_path,
            last_id=last_id,
        )
    finally:
        if db.is_active:
            db.close()


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


# ── Exec-Tab (v1.4.7+) ─────────────────────────────────────────────────────
#
# Oneshot-Befehl im MSM-Container des Servers. Sicherheit:
# - Auth: Cookie + CSRF + neue Permission ``server.console.exec``.
# - Blueprint-Gate: ``runtime.enableExec=true`` im Server-Blueprint.
#   Wer die Permission hat, aber im Blueprint des Servers ist Exec aus,
#   bekommt 403 -- so bleibt ein "neuer Exec-User pro Server"-Workflow
#   sauber (Server-Owner aktivieren Exec pro Blueprint).
# - argv-Liste, kein Shell-String. Wir bauen NIE ``["sh", "-c", userstring]``,
#   also kann ein User mit ``server.console.exec`` keine Shell-Metazeichen
#   eskalieren. ``container.exec_run(argv)`` fuehrt die args als exec-Args
#   des Zielprozesses aus, ohne Shell dazwischen.
# - Container-Name kommt ausschliesslich aus ``container_name_for(server.id)``;
#   es gibt KEIN Feld im Request, mit dem der User den Container beeinflussen
#   koennte. Damit ist "Host-Exec" oder "Container eines anderen Servers"
#   strukturell ausgeschlossen.
# - Output gedeckelt (256 KiB) im Service, Timeout (1..600s) aus Blueprint.
# - Audit-Log (server_id, user_id, argv) im Service -- Output wird NICHT
#   geloggt (kann Secrets enthalten).
class ExecCommandBody(BaseModel):
    """Body fuer POST /api/servers/{id}/exec.

    Args als argv-Liste, nicht als String. Pydantic validiert:
    - 1..32 Elemente (sonst 422)
    - jedes Element: max 4096 Zeichen (sonst 422)
    """

    command: list[str] = Field(..., min_length=1, max_length=32)

    @field_validator("command")
    @classmethod
    def _check_each_arg(cls, v: list[str]) -> list[str]:
        for i, arg in enumerate(v):
            if not isinstance(arg, str):
                raise ValueError(f"command[{i}] muss ein String sein")
            if len(arg) > 4096:
                raise ValueError(
                    f"command[{i}] zu lang ({len(arg)} > 4096 Zeichen)"
                )
        return v


@router.post("/{server_id}/exec")
def server_exec(
    server_id: int,
    body: ExecCommandBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Fuehrt ``body.command`` (argv) im Container von ``server_id`` aus.

    Auth: Cookie + CSRF + ``server.console.exec``.
    Blueprint-Gate: ``runtime.enableExec=true``.
    Output gedeckelt; bei Fehler generische Statuscodes (500/504),
    keine internen Pfade/Stacktraces im Response.
    """
    require_server_permission(user, server_id, db, "server.console.exec")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    # Blueprint-Gate: per-Blueprint-Opt-in. Default ist False.
    blueprint = exec_service.load_blueprint_for_server(server)
    if blueprint is None or not getattr(
        blueprint.runtime, "enableExec", False
    ):
        raise HTTPException(
            status_code=403,
            detail="Exec ist im Blueprint dieses Servers deaktiviert",
        )

    timeout = int(getattr(blueprint.runtime, "execTimeoutSeconds", 60))

    result = exec_service.run_in_container(
        server_id=server_id,
        command=body.command,
        timeout=timeout,
        user_id=user.id,
    )

    if not result["ok"]:
        err = (result.get("error") or "").lower()
        if "timeout" in err:
            raise HTTPException(
                status_code=504, detail="Exec-Timeout ueberschritten"
            )
        # Generische Fehlermeldung -- keine Container-Internas leaken.
        raise HTTPException(status_code=500, detail="Exec fehlgeschlagen")

    return {
        "ok": True,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


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
