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
from services.scheduler_service import sync_server_restart_schedule, evaluate_disk_soft_limit
from services.server_lifecycle_service import (
    LifecycleNotification,
    get_server_lifecycle_lock,
    is_lifecycle_job_active,
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


def _assert_remote_ports_available(node, ports: list[tuple[str, int, str]], bind_ip: str) -> None:
    if node is None or node.is_local:
        return
    from services.node_client import NodeClient

    normalized = [(port, protocol, role) for role, port, protocol in ports]
    result = NodeClient.from_node(node).ports_available(normalized, bind_ip or "0.0.0.0")
    if not result.get("available", False):
        conflicts = ", ".join(
            f"{item.get('port')}/{item.get('protocol')}" for item in result.get("conflicts", [])
        )
        raise HTTPException(status_code=409, detail=f"Port auf dem Ziel-Node belegt: {conflicts}")


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
    # Phase 2/3: node from request or default local; ports scoped per node
    from models import Node
    from services.node_service import get_local_node

    target_node = None
    if req.node_id is not None:
        target_node = db.query(Node).filter(Node.id == req.node_id).first()
        if not target_node:
            raise HTTPException(status_code=400, detail="Node nicht gefunden")
    else:
        target_node = get_local_node(db)
    if target_node is not None and not target_node.is_local and req.public_bind_ip is None:
        bind_ip = "0.0.0.0"
    target_node_id = target_node.id if target_node else None
    check_host = True if (target_node is None or target_node.is_local) else False
    try:
        allocated = allocate_ports(
            db,
            exclude_server_id=None,
            bind_ip=bind_ip or "0.0.0.0",
            port_requirements=port_requirements,
            requested_ports=requested_ports,
            node_id=target_node_id,
            check_host=check_host,
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
    _assert_remote_ports_available(target_node, allocated, bind_ip or "0.0.0.0")

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
        node_id=target_node_id,
    )
    _normalize_server_restart_mode(server)
    db.add(server)
    db.commit()
    db.refresh(server)

    install_lock_acquired = False
    if plugin:
        install_lock_acquired = try_acquire_install_update_lock(
            server.id, "install", node_id=server.node_id
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

        is_remote_node = bool(target_node is not None and not target_node.is_local)
        install_dir = os.path.join(base_dir, str(server.id) if is_remote_node else f"{req.game_type}_{server.id}")

        # Vorheriges Verzeichnis auf Host prüfen (verwaist von abgebrochenem Install,
        # manuellem Eingriff oder root-owned SteamCMD-Artifact). Saubere 409 statt
        # mysteriösem EPERM auf chmod.
        if not is_remote_node and os.path.exists(install_dir):
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
            if is_remote_node:
                from services.node_client import NodeClient
                from services.node_service import ensure_node_online

                ensure_node_online(target_node)
                NodeClient.from_node(target_node).files_ensure_server_root(server.id)
            else:
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
            if created_install_dir:
                try:
                    if target_node is not None and not target_node.is_local:
                        from services.node_client import NodeClient

                        NodeClient.from_node(target_node).files_delete_server_root(server.id)
                    elif os.path.exists(server.install_dir):
                        shutil.rmtree(server.install_dir)
                except Exception:
                    logger.warning("Install-Verzeichnis konnte nach Create-Abbruch nicht entfernt werden")
        raise

    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_installed_notification(user.email, user.username, server.name)

    sync_server_restart_schedule(server)
    response = _server_response(server)
    create_resp = ServerCreateResponse.model_validate(response.model_dump())
    create_resp.postgres_credentials = [
        PostgresOneTimeCredential.model_validate(item)
        for item in postgres_credentials
    ]
    return create_resp


def _is_guardian_enabled(server: Server) -> bool:
    if getattr(server, "guardian_config_hash", None):
        return True
    try:
        plugin = get_plugin(server.game_type)
        bp = plugin.get_blueprint() if hasattr(plugin, "get_blueprint") else getattr(plugin, "blueprint", None)
        if bp is not None:
            recovery = getattr(bp, "recovery", None)
            if recovery is not None:
                policies = getattr(recovery, "policies", None)
                if policies and len(policies) > 0:
                    return True
            health = getattr(bp, "health", None)
            if health is not None:
                return True
    except Exception:
        pass
    return False


def _server_response(server: Server) -> ServerResponse:
    """Serialize server including safe node label (never auth tokens)."""
    from services.node_service import effective_server_runtime_status, is_node_offline

    data = ServerResponse.model_validate(server)
    data.guardian_enabled = _is_guardian_enabled(server)
    node = getattr(server, "node", None)
    if node is not None:
        data.node_id = node.id
        data.node_name = node.name
        # Graceful degradation: keep server visible; surface node_unreachable
        if is_node_offline(node):
            data.status = effective_server_runtime_status(server, node)
            data.status_message = "Node offline — Aktionen deaktiviert"
    else:
        data.node_id = getattr(server, "node_id", None)
        data.node_name = None
    return data


def _reject_if_node_offline(server: Server) -> None:
    """Block start/stop/file-ops when heartbeat marked the node offline."""
    from services.node_service import NODE_OFFLINE_MSG, is_node_offline

    node = getattr(server, "node", None)
    if is_node_offline(node):
        raise HTTPException(status_code=503, detail=NODE_OFFLINE_MSG)


@router.get("", response_model=list[ServerResponse])
def list_servers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ServerResponse]:
    servers = permission_service.list_visible_servers(db, user)
    return [_server_response(s) for s in servers]


@router.get("/{server_id}", response_model=ServerResponse)
def get_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ServerResponse:
    require_server_permission(user, server_id, db, "server.view")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return _server_response(server)


@router.patch("/{server_id}", response_model=ServerResponse)
def update_server(server_id: int, req: ServerUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> Server:
    # ── Zugriffsgate: Server muss sichtbar sein (least-privilege Basis). ──
    # Frueher war hier pauschal ``server.config.write`` erforderlich, was
    # reine Ressourcen-PATCHes unnoetig blockiert hat (VAL-API-011). Die
    # konkreten Schreibrechte werden unten pro Feldgruppe geprueft.
    require_server_permission(user, server_id, db, "server.view")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    old_ports = [(p.port, p.protocol, p.role) for p in server.ports]
    old_bind_ip = server.public_bind_ip

    payload = req.model_dump(exclude_unset=True)
    port_fields = {"game_port", "query_port", "rcon_port", "ports", "port_protocols"}
    resource_fields = {"cpu_limit_percent", "ram_limit_mb", "disk_limit_gb"}
    config_fields = {"name", "auto_restart", "restart_interval_hours", "restart_time_utc", "restart_times_utc"}
    changed_ports = port_fields & set(payload.keys())
    bind_ip_present = "public_bind_ip" in payload
    bind_ip_changed = bind_ip_present and payload["public_bind_ip"] != old_bind_ip
    # Network-field PRESENCE (scrutiny round 3 fix): determines permission
    # checks and mixed-payload 409 rejection. Must be presence-based so a
    # resource plus public_bind_ip with the CURRENT value still requires
    # server.network.manage and triggers the mixed 409 before mutation.
    # Port fields (changed_ports) are already presence-based.
    network_field_present = bool(changed_ports) or bind_ip_present
    # Network VALUE CHANGE: determines post-commit network recreation
    # (firewall, iptables, plugin stop/start). Value-based so same-value
    # fields are no-ops and don't trigger unnecessary recreation.
    network_change = bool(changed_ports) or bind_ip_changed
    guardian_network_changed = bind_ip_changed
    has_resource = bool(resource_fields & set(payload.keys()))
    has_config = bool(config_fields & set(payload.keys()))

    # ── Tatsaechliche CPU/RAM-Wertänderungen (kein No-Op). ──
    # Wird vor den Attribut-Mutationen berechnet, damit der alte Wert
    # noch als Referenz vorliegt. No-Op-PATCHes loesen kein Docker-Update
    # aus (VAL-API-012). Nur CPU/RAM brauchen Live-Update; Disk ist ein
    # Soft-Limit ohne Docker-Hard-Quota.
    old_cpu = server.cpu_limit_percent
    old_ram = server.ram_limit_mb
    cpu_changed = "cpu_limit_percent" in payload and payload["cpu_limit_percent"] != old_cpu
    ram_changed = "ram_limit_mb" in payload and payload["ram_limit_mb"] != old_ram
    old_disk = server.disk_limit_gb
    disk_changed = "disk_limit_gb" in payload and payload["disk_limit_gb"] != old_disk

    # ── Least-privilege: jede Feldgruppe braucht nur ihre eigene Permission. ──
    # Ressourcen-PATCHes kommen mit ``server.resources.manage`` allein aus,
    # ohne ``server.config.write`` oder ``server.network.manage`` (VAL-API-011).
    # Bei gemischten Payloads werden ALLE relevanten Permissions verlangt
    # (VAL-API-013). Die Pruefungen finden vor jeder Mutation statt.
    if has_resource:
        require_server_permission(user, server_id, db, "server.resources.manage")
    if network_field_present:
        require_server_permission(user, server_id, db, "server.network.manage")
    if has_config:
        require_server_permission(user, server_id, db, "server.config.write")

    # ── Mixed resource/disk + network rejection (scrutiny round 2 fix). ──
    # Resource-Felder (cpu_limit_percent, ram_limit_mb, disk_limit_gb) und
    # Network-Felder (ports, bind_ip, port_protocols) loesen unterschiedliche
    # Seiteneffekt-Gruppen aus (Docker-Live-Update / Disk-Soft-Limit vs.
    # Firewall / iptables / Plugin-Stop-Start). Die Network-Seiteneffekte
    # laufen NACH dem DB-Commit, sodass ein Post-Commit-Fehler die bereits
    # committeten Resource-Aenderungen nicht zurueckrollen kann. KISS-safe:
    # diese unsupported mixed side-effect group VOR jeder Mutation ablehnen
    # (VAL-CROSS-010, VAL-CROSS-014). Permission-Pruefungen laufen zuerst
    # (403 vor 409). Resource-only, disk-only, network-only und
    # config/scheduler Paths bleiben unberuehrt.
    if has_resource and network_field_present:
        raise HTTPException(
            status_code=409,
            detail="Ressourcen- und Netzwerk-Aenderungen koennen nicht in einem gemeinsamen PATCH durchgefuehrt werden",
        )

    # ── DB-Atomaritaet: alle Mutationen in einer Transaktion, ein Commit. ──
    # Schlägt ein Schritt (z. B. Port-Allokation) fehl, wird die Session
    # zurückgerollt, sodass Ressourcen-, Netzwerk- und Konfig-Felder nicht
    # partial driften (VAL-API-013). Unerwartete Fehler werden sanitisiert
    # (VAL-API-010): kein Stacktrace, kein Host-Pfad, kein Socket-Pfad im
    # Response oder Log.
    try:
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
            node = getattr(server, "node", None)
            node_id = getattr(server, "node_id", None)
            check_host = True if (node is None or getattr(node, "is_local", True)) else False
            try:
                allocated = allocate_ports(
                    db,
                    exclude_server_id=server.id,
                    bind_ip=bind_ip_for_check,
                    port_requirements=port_requirements,
                    requested_ports=requested_ports,
                    node_id=node_id,
                    check_host=check_host,
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
            _assert_remote_ports_available(node, allocated, bind_ip_for_check)

            normalized_old_ports = sorted(
                (int(port), str(protocol).lower(), str(role))
                for port, protocol, role in old_ports
            )
            normalized_new_ports = sorted(
                (int(port_val), str(proto).lower(), str(role))
                for role, port_val, proto in allocated
            )
            guardian_network_changed = (
                guardian_network_changed
                or normalized_new_ports != normalized_old_ports
            )

            from models.server_port import ServerPort
            db.query(ServerPort).filter(ServerPort.server_id == server.id).delete()
            for role, port_val, proto in allocated:
                db.add(ServerPort(server_id=server.id, role=role, port=port_val, protocol=proto))

        # Standard-Update (alle nicht-Port-Felder)
        for key, val in payload.items():
            if key not in ("game_port", "query_port", "rcon_port", "ports", "port_protocols"):
                setattr(server, key, val)
        _normalize_server_restart_mode(server)

        # ── Live CPU/RAM-Update und/oder Disk-Soft-Limit-Re-evaluation ──
        # Mixed resource/disk + network payloads wurden VOR diesem Punkt
        # mit 409 abgelehnt (scrutiny round 2 fix). Hier sind nur noch
        # resource-only, disk-only, resource+disk (ohne network), oder
        # network/config-only Payloads moeglich.
        # CPU/RAM-Live-Update nur wenn kein Network-Change im selben PATCH
        # ist (der Network-Recreate-Pfad sammelt die neuen Werte beim
        # naechsten Start ein).
        # Disk-Soft-Limit-Re-evaluation bei JEDER disk_limit_gb-Aenderung
        # (VAL-DISK-001). Die Re-evaluation findet vor dem Commit statt,
        # sodass bei Fehlschlag alle Aenderungen zurueckgerollt werden.
        # Bei gestoppten Servern werden die Werte nur persistiert (VAL-API-008).
        # Bei Docker-Fehlschlag wird die DB zurueckgerollt (VAL-API-009).
        # Lifecycle-Serialisierung verhindert Race-Conditions mit Start/Stop
        # (VAL-API-014). Stale-Runtime-Check verhindert DB/Docker-Drift
        # (VAL-API-015). Keine Network- oder Firewall-Mutation (VAL-DOCKER-006).
        # Disk ist ein Soft-Limit: sofortige Re-evaluation ohne Docker-Hard-Quota
        # (VAL-DISK-001, VAL-DISK-004, VAL-DOCKER-010).
        resource_live_change = (cpu_changed or ram_changed) and not network_change
        disk_eval_needed = disk_changed
        # Lock wird benoetigt fuer CPU/RAM-Live-Update (Docker-Mutation) und
        # fuer Disk-Re-evaluation bei laufendem Server (potenzieller Stop
        # via plugin.stop, VAL-DISK-007).
        needs_lock = resource_live_change or (disk_eval_needed and server.status == "running")
        if needs_lock:
            container_name = container_name_for(server.id)
            # Lifecycle-Job aktiv -> sicherer Konflikt (VAL-API-014).
            if is_lifecycle_job_active(server_id):
                raise HTTPException(
                    status_code=409,
                    detail="Server Lifecycle-Aktion laeuft, Ressourcen-Update nicht moeglich",
                )
            lock = get_server_lifecycle_lock(server_id)
            if not lock.acquire(timeout=5):
                raise HTTPException(
                    status_code=409,
                    detail="Server Lifecycle-Aktion laeuft, Ressourcen-Update nicht moeglich",
                )
            try:
                # Re-Check nach Lock-Acquire (Race-Schutz: Job koennte zwischen
                # der ersten Pruefung und dem Lock-Acquire gestartet worden sein).
                if is_lifecycle_job_active(server_id):
                    raise HTTPException(
                        status_code=409,
                        detail="Server Lifecycle-Aktion laeuft, Ressourcen-Update nicht moeglich",
                    )
                # ── CPU/RAM Live-Update fuer laufende Container (ohne Restart). ──
                # Stale-Runtime-Check (VAL-API-015): DB-Status und Docker-
                # Container-Status muessen uebereinstimmen. Wenn DB "running"
                # sagt, aber Docker gestoppt ist, wird sicher abgebrochen.
                # Wenn DB "stopped" sagt, aber Docker tatsaechlich laeuft,
                # wird ebenfalls sicher abgebrochen (kein Drift: niemals Werte
                # persistieren, die ein Live-Update behaupten, ohne dass der
                # Container aktualisiert wurde).
                if resource_live_change:
                    docker_running = docker_service.is_running(container_name, node=server.node)
                    if server.status == "running" and not docker_running:
                        raise HTTPException(
                            status_code=409,
                            detail="Server-Status nicht konsistent, Ressourcen-Update abgebrochen",
                        )
                    if server.status != "running" and docker_running:
                        raise HTTPException(
                            status_code=409,
                            detail="Server-Status nicht konsistent, Ressourcen-Update abgebrochen",
                        )
                    if docker_running:
                        # Docker Live-Update nur mit geaenderten Feldern
                        # (VAL-DOCKER-002).
                        docker_updates: dict[str, int | None] = {}
                        if cpu_changed:
                            docker_updates["cpu_limit_percent"] = server.cpu_limit_percent
                        if ram_changed:
                            docker_updates["ram_limit_mb"] = server.ram_limit_mb
                        result = docker_service.update_container_resources(
                            container_name, docker_updates, node=server.node,
                        )
                        if not result.get("ok"):
                            # Generische, sanitisierte Meldung (VAL-API-010):
                            # der spezifische Fehler wird im Docker-Service geloggt.
                            # Bei drift=True (Restore-Verifikation fehlgeschlagen)
                            # wird eine blocker-safe Meldung zurueckgegeben, die
                            # den Operator auf moeglichen Docker-Drift hinweist
                            # (scrutiny round 2 fix).
                            if result.get("drift"):
                                raise HTTPException(
                                    status_code=503,
                                    detail=(
                                        "Ressourcen-Update fehlgeschlagen, "
                                        "manuelle Pruefung erforderlich"
                                    ),
                                )
                            raise HTTPException(
                                status_code=503,
                                detail="Ressourcen-Update konnte nicht angewendet werden",
                            )
                # Bei DB=stopped + Docker=stopped: nur persistieren, kein
                # Docker-Aufruf, kein Start (VAL-API-008).
                # ── Disk Soft-Limit sofort neu bewerten (VAL-DISK-001). ──
                # Misst Nutzung und wendet bestehende Warn-/Stop-Policy an.
                # Stop erfolgt via plugin.stop unter Lifecycle-Lock (VAL-DISK-007).
                # Bei Fehlschlag: 503 + Rollback, kein Drift (VAL-DISK-005).
                if disk_eval_needed:
                    disk_result = evaluate_disk_soft_limit(db, server)
                    if not disk_result.get("ok"):
                        raise HTTPException(
                            status_code=503,
                            detail="Disk-Limit konnte nicht neu bewertet werden",
                        )
            finally:
                lock.release()
        elif disk_eval_needed:
            # Server nicht running -> Disk-Re-evaluation ohne Lock (kein Stop
            # moeglich, keine Docker-Mutation). Misst nur Nutzung und ggf.
            # Loeschen verstaendlicher Disk-Warn-Status (VAL-DISK-006).
            disk_result = evaluate_disk_soft_limit(db, server)
            if not disk_result.get("ok"):
                raise HTTPException(
                    status_code=503,
                    detail="Disk-Limit konnte nicht neu bewertet werden",
                )

        # ── Scheduler-Sync vor Commit (VAL-API-013 scrutiny fix): ──
        # Bei Fehlschlag wird die DB zurueckgerollt, damit DB- und
        # Scheduler-Status nicht driften. Scheduler-Sync ist eine
        # Seiteneffekt-Gruppe, die mit Resource- oder Config-Aenderungen
        # im selben PATCH atomic sein muss.
        if {"auto_restart", "restart_interval_hours", "restart_time_utc", "restart_times_utc"} & set(payload.keys()):
            sync_server_restart_schedule(server)

        if guardian_network_changed:
            from services.guardian_state_service import mark_guardian_configuration_changed

            mark_guardian_configuration_changed(server)

        db.commit()
        db.refresh(server)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.warning(
            "Server-Aktualisierung fehlgeschlagen (server_id=%s): %s",
            server_id, type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Server-Aktualisierung fehlgeschlagen")

    if network_change:
        # Alte Firewall- und iptables-Regeln entfernen, neue anlegen - ABER
        # nur, wenn der Server gerade laeuft. Fuer gestoppte Server bleiben die
        # Regeln zu (Lifecycle-Kopplung).
        plugin = get_plugin(server.game_type)
        was_running = plugin is not None and docker_service.is_running(
            container_name_for(server.id), node=server.node
        )

        if was_running:
            close_ports(old_ports, node=server.node, name=server.name)
            if server.node is None or server.node.is_local:
                iptables_revoke_server(server.name, old_bind_ip or "", old_ports)
            # Container stoppen - Plugin.start() legt ihn mit den neuen Ports/
            # Bind-Werten frisch an.
            plugin.stop(server)
            new_ports = [(p.port, p.protocol, p.role) for p in server.ports]
            open_ports(server.name, new_ports, node=server.node)
            if server.node is None or server.node.is_local:
                iptables_accept_server(server.name, server.public_bind_ip or "", new_ports)
            plugin.start(server)

    if guardian_network_changed:
        from services.server_lifecycle_service import sync_desired_state_to_agent

        sync_desired_state_to_agent(db, server)

    return _server_response(server)


@router.delete("/{server_id}")
def delete_server(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    """Löscht einen Server vollständig:

    1. Docker-Container stoppen + entfernen (idempotent, force=True killt auch
       laufende Container).
    2. UFW-Regeln für Ports schließen.
    3. Install-Verzeichnis (Bind-Mount-Quelle) vom Host entfernen.
    4. Backup-Verzeichnis (alle TAR-Archive) vom Host entfernen + S3-Objekte
       der Backups best-effort löschen (vor Cascade, sonst verwaist).
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
    node = getattr(server, "node", None)
    remove_result = docker_service.remove(container, force=True, node=node)
    if not remove_result.get("ok"):
        raise HTTPException(status_code=503, detail="Container konnte auf dem Node nicht entfernt werden")

    # 2. Firewall- und iptables-Regeln schließen
    ports_list = [(p.port, p.protocol, p.role) for p in server.ports]
    close_ports(ports_list, node=node, name=server.name)
    if node is None or node.is_local:
        iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)

    # 3. Install-Verzeichnis physisch löschen
    install_dir = server.install_dir
    dir_removed = False
    if node is not None and not node.is_local:
        try:
            from services.node_client import NodeClient

            NodeClient.from_node(node).files_delete_server_root(server.id)
            dir_removed = True
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail="Abbruch: Server-Verzeichnis konnte auf dem Node nicht gelöscht werden",
            ) from e
    elif install_dir and os.path.exists(install_dir):
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
    #    Nur fuer lokale Nodes, da Remote-Backups auf dem Node liegen.
    backup_dir = f"/opt/msm/backups/{server.id}"
    backups_removed = False
    if node is None or node.is_local:
        if os.path.exists(backup_dir):
            try:
                shutil.rmtree(backup_dir)
                backups_removed = True
            except OSError as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Abbruch (Atomar): Backup-Verzeichnis konnte nicht gelöscht werden. Bitte lösche den Ordner manuell: {e}"
                )

    # 5. MSM-Console-Log-Verzeichnis räumen (nur lokal vorhanden)
    if node is None or node.is_local:
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

    # 7. S3-Objekte der Backups best-effort löschen - VOR db.delete, da der
    #    Cascade-Delete die Backup-Records (und damit s3_key) entfernt und
    #    die S3-Objekte sonst permanent verwaisten. Local-Import + Warning-Log
    #    wie in cleanup_old_backups / delete_backup (keine Secrets im Log).
    for backup in server.backups:
        if backup.s3_key:
            try:
                from services.s3_service import S3Service
                S3Service.delete_object(backup.s3_key, bucket=backup.s3_bucket)
            except Exception as e:
                logger.warning(
                    "S3-Delete fehlgeschlagen (Backup %s): %s",
                    backup.id, type(e).__name__,
                )

    # 8. DB-Eintrag löschen (Cascade entfernt Permissions/Mods/Backups)
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
    _reject_if_node_offline(server)
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
    _reject_if_node_offline(server)
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
    _reject_if_node_offline(server)
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
    stop_result = docker_service.stop(container_name, timeout=10, node=server.node)
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
    if server.node is not None and not server.node.is_local:
        try:
            from services.node_client import NodeClient

            disk_data = NodeClient.from_node(server.node).files_disk_info(server.id)
            disk_free = int(disk_data["free_bytes"]) // (1024 * 1024)
        except Exception:
            disk_free = None
    else:
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
    if not try_acquire_install_update_lock(server.id, "install", node_id=server.node_id):
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
# Dieselbe Allowlist wie CORS (panel_url + MSM_CORS_ALLOWED_ORIGINS + Dev).
from config import get_cors_origins


def _ws_origin_allowed(origin: str | None) -> bool:
    """Prueft den Origin-Header des WS-Upgrade-Requests. SameSite-Cookie + Origin-Check
    ersetzen die fehlende CSRF-Pruefung (WS sind keine 'simple requests').
    """
    if not origin:
        return False
    allowed = {o.rstrip("/") for o in get_cors_origins()}
    return origin.rstrip("/") in allowed


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
    node = None
    try:
        try:
            user = get_current_user_for_ws(websocket, db)
            server = db.query(Server).filter(Server.id == server_id).first()
            if not server:
                await websocket.close(code=1008)
                return
            require_server_permission(user, server_id, db, "server.console.read")
            # Eager-load node before session closes (avoid DetachedInstanceError)
            node = server.node
            if node is not None:
                # Touch attributes while session is open
                _ = (node.id, node.host, node.is_local, node.auth_token_enc)
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
            node=node,
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
    if not docker_service.is_running(container, node=server.node):
        raise HTTPException(status_code=409, detail="Container läuft nicht")
    # Newline erzwingen - die meisten Game-Server lesen zeilenweise.
    data = body.line if body.line.endswith("\n") else body.line + "\n"
    result = docker_service.send_stdin(container, data, node=server.node)
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
        node=server.node,
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
