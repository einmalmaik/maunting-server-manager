import os
import shutil
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from config import get_cors_origins
from database import SessionLocal
from database import get_db
from models import Server, User
from schemas import ServerCreate, ServerCreateResponse, ServerResponse, ServerUpdate, ServerStatusResponse
from schemas.postgres import PostgresOneTimeCredential
from dependencies import (
    get_current_user,
    get_current_user_for_ws,
    require_server_permission,
    verify_csrf,
)
from services import permission_service, postgres_service
from games import get_plugin
from games.base import container_name_for, _console_log_path, _append_console_log
from services import EmailService, docker_service
from services import exec_service
from services.docker_iptables_service import accept_server as iptables_accept_server
from services.docker_iptables_service import revoke_server as iptables_revoke_server
from services.firewall_service import close_ports, open_ports
from services.port_allocation_service import PortConflictError, allocate_ports
from services.port_role_service import blueprint_port_requirements, normalize_port_protocol
from services.scheduler_service import sync_server_restart_schedule, evaluate_disk_soft_limit
from services.server_lifecycle_service import (
    get_server_lifecycle_lock,
    is_lifecycle_job_active,
    should_preserve_lifecycle_status,
)
from services.console_stream_service import connect as ws_connect
from services.install_update_lock_service import (
    release_install_update_lock,
    force_release_install_update_lock,
    try_acquire_install_update_lock,
)
from services.actor_context import ActorContext
from services.server_provisioning_service import (
    assert_remote_ports_available as _assert_remote_ports_available,
    install_update_busy_error as _install_update_busy_error,
    normalize_server_restart_mode as _normalize_server_restart_mode,
    provision_server,
)
from services.server_action_service import request_lifecycle_operation

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


@router.post("", response_model=ServerCreateResponse, status_code=201)
async def create_server(
    req: ServerCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    retry_of_id: str | None = Header(None, alias="X-Task-Retry-Of"),
) -> ServerCreateResponse:
    """Dünner HTTP-Adapter; RBAC und Provisionierung liegen im gemeinsamen Service.

    `provision_server` ist durchgehend synchron und macht dabei echtes I/O:
    Socket-Binds, HTTP-Aufrufe an die Node mit zehn Sekunden Zeitlimit,
    Verzeichnisse anlegen, Datenbanken einrichten. Auf dem Event-Loop
    ausgefuehrt legt eine einzige Serveranlage das ganze Panel still — keine
    andere Anfrage, kein WebSocket-Frame. Deshalb der Threadpool. Die Route
    bleibt `async`, weil die Mailbenachrichtigung darunter awaited wird.
    """
    result = await run_in_threadpool(
        provision_server,
        db,
        req,
        ActorContext.for_user(user),
        idempotency_key=idempotency_key,
        retry_of_id=retry_of_id,
    )
    if not result.reused and EmailService.is_configured() and user.email_notifications:
        await EmailService.send_server_installed_notification(
            user.email,
            user.username,
            result.server.name,
        )

    response = _server_response(result.server)
    create_resp = ServerCreateResponse.model_validate(response.model_dump())
    create_resp.postgres_credentials = [
        PostgresOneTimeCredential.model_validate(item)
        for item in result.postgres_credentials
    ]
    create_resp.task_id = result.task.id
    return create_resp


def _is_guardian_enabled(server: Server) -> bool:
    try:
        from services.guardian_runtime_compiler import is_guardian_enabled

        plugin = get_plugin(server.game_type)
        bp = plugin.get_blueprint() if hasattr(plugin, "get_blueprint") else getattr(plugin, "blueprint", None)
        if bp is not None:
            return is_guardian_enabled(bp)
    except Exception:
        pass
    return False


def _server_response(server: Server) -> ServerResponse:
    """Serialize server including safe node label (never auth tokens)."""
    from services.node_service import effective_server_runtime_status, is_node_offline

    data = ServerResponse.model_validate(server)
    data.guardian_enabled = _is_guardian_enabled(server)
    # The persisted clear intent remains pending across offline/unknown/stale
    # observations. Reconciliation removes it only after the Agent accepts the
    # generation and reports that quarantine is gone.
    data.guardian_quarantine_clear_pending = bool(
        server.guardian_quarantine_control
    )
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

    # RAM booking guard: only when a numeric limit is being set/changed and the
    # node has a known host total (heartbeat). Unlimited (null) is not booked.
    if ram_changed and payload.get("ram_limit_mb") is not None:
        from models import Node as NodeModel
        from services.node_capacity import ensure_ram_limit_fits

        guard_node = None
        if server.node_id is not None:
            # Dieselbe Node-Zeilensperre wie in der Provisionierung. Ohne sie
            # laufen Kapazitaetspruefung und Buchung nicht gegeneinander
            # serialisiert: zwei parallele Vorgaenge lesen beide den alten
            # Stand und ueberbuchen den Node. Die Sperre haelt bis zum
            # gemeinsamen Commit am Ende dieses Requests.
            guard_node = (
                db.query(NodeModel)
                .filter(NodeModel.id == server.node_id)
                .with_for_update()
                .first()
            )
        ensure_ram_limit_fits(
            db,
            guard_node,
            new_ram_limit_mb=payload.get("ram_limit_mb"),
            exclude_server_id=server.id,
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
            if node_id is not None:
                # Portvergabe gegen die Provisionierung serialisieren. Sonst
                # koennen ein PATCH und ein gleichzeitiges Anlegen denselben
                # Port auf demselben Node vergeben — beide lesen die belegten
                # Ports, bevor einer von beiden schreibt.
                from models import Node as NodeModel

                db.query(NodeModel).filter(NodeModel.id == node_id).with_for_update().one()
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
        # Der Ablauf steht in server_network_service, weil ihn seit dem
        # KI-Vorschlag `propose_bind_ip_update` zwei Wege brauchen. Ein zweiter
        # Weg, der die Firewall *fast* genauso behandelt, waere genau die stille
        # Abweichung, die Zielpunkt 10 ausschliesst.
        from services.server_network_service import recreate_server_network

        recreate_server_network(server, old_ports, old_bind_ip)

    if guardian_network_changed:
        from services.server_lifecycle_service import sync_desired_state_to_agent

        sync_desired_state_to_agent(db, server)

    return _server_response(server)


@router.delete("/{server_id}")
def delete_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """HTTP-Rand der gemeinsamen Server-Loeschung.

    Die eigentliche Reihenfolge, Fehlerbehandlung und Rechtepruefung liegt in
    `services.server_deletion_service`, damit Panel und Hoster-Anbindung exakt
    denselben Weg verwenden (Zielpunkt 10).
    """
    from services.server_deletion_service import delete_server_completely

    return delete_server_completely(
        db, server_id=server_id, actor=ActorContext.for_user(user)
    )


@router.post("/{server_id}/start")
async def start_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    retry_of_id: str | None = Header(None, alias="X-Task-Retry-Of"),
) -> dict:
    from services.server_lifecycle_service import LifecycleNotification

    return request_lifecycle_operation(
        db,
        server_id=server_id,
        operation="start",
        actor=ActorContext.for_user(user),
        notification=LifecycleNotification(user.email, user.username, user.email_notifications),
        idempotency_key=idempotency_key,
        retry_of_id=retry_of_id,
    )


@router.post("/{server_id}/stop")
async def stop_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    retry_of_id: str | None = Header(None, alias="X-Task-Retry-Of"),
) -> dict:
    from services.server_lifecycle_service import LifecycleNotification

    return request_lifecycle_operation(
        db,
        server_id=server_id,
        operation="stop",
        actor=ActorContext.for_user(user),
        notification=LifecycleNotification(user.email, user.username, user.email_notifications),
        idempotency_key=idempotency_key,
        retry_of_id=retry_of_id,
    )


@router.post("/{server_id}/restart")
async def restart_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    retry_of_id: str | None = Header(None, alias="X-Task-Retry-Of"),
) -> dict:
    from services.server_lifecycle_service import LifecycleNotification

    return request_lifecycle_operation(
        db,
        server_id=server_id,
        operation="restart",
        actor=ActorContext.for_user(user),
        notification=LifecycleNotification(user.email, user.username, user.email_notifications),
        idempotency_key=idempotency_key,
        retry_of_id=retry_of_id,
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
def kill_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    retry_of_id: str | None = Header(None, alias="X-Task-Retry-Of"),
) -> dict:
    """Erzwungenes Beenden (Docker force remove). Als Notfall auch während start/restart nutzbar (emergency override des Job-Locks).
    Permission "server.kill" (Naming analog zu server.stop, nicht server.power.* für Code-Konsistenz mit bestehenden server.* Keys).

    Bewusst `def` statt `async def`: die Route wartet auf `docker_service.remove`
    an der Node (30 s Zeitlimit). Auf dem Event-Loop haette ein Kill gegen eine
    nicht erreichbare Node das ganze Panel bis zum Zeitlimit eingefroren. Der
    Kill-Zweig setzt `server.status` direkt statt ueber `_set_status` und haengt
    an keinem laufenden Loop — der Wechsel in den Threadpool aendert also
    nichts am Verhalten. Fuer start/stop/restart gilt das **nicht**: dort
    verschickt `_set_status` den Webhook nur bei laufendem Loop.
    """
    return request_lifecycle_operation(
        db,
        server_id=server_id,
        operation="kill",
        actor=ActorContext.for_user(user),
        idempotency_key=idempotency_key,
        retry_of_id=retry_of_id,
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
    # Der Status vor der Installation wird gemerkt, damit ein Fehlstart ihn
    # wiederherstellen kann. Ohne das blieb der Server dauerhaft auf
    # "installing": weder die Statusaktualisierung noch die
    # Lifecycle-Reconciliation korrigieren diesen Zustand, der Server war im
    # Panel also nicht mehr bedienbar.
    previous_status = server.status
    previous_status_message = server.status_message

    def _restore_status() -> None:
        try:
            db.rollback()
            server.status = previous_status
            server.status_message = previous_status_message
            db.commit()
        except Exception:
            db.rollback()
            logger.warning(
                "Serverstatus konnte nach fehlgeschlagenem Installationsstart nicht "
                "zurueckgesetzt werden (server_id=%s)",
                server_id,
            )

    try:
        server.status = "installing"
        server.status_message = "Installation gestartet"
        db.commit()
        result = plugin.install(server)
    except Exception:
        release_install_update_lock(server.id)
        _restore_status()
        raise HTTPException(status_code=500, detail="Installation konnte nicht gestartet werden")
    if "error" in result:
        release_install_update_lock(server.id)
        # plugin.install() hat den Server bereits selbst auf "error" gesetzt,
        # wenn es die Installation gar nicht erst starten konnte. In dem Fall
        # bleibt dieser Status stehen; nur ein noch auf "installing" stehender
        # Server wird zurueckgesetzt.
        db.refresh(server)
        if server.status == "installing":
            _restore_status()
        raise HTTPException(status_code=500, detail=result["error"])
    return {"message": "Installation gestartet", **result}


@router.post("/{server_id}/unlock")
def unlock_server(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Manuelle Freigabe hängender/blockierter Installation-Locks für einen Server."""
    require_server_permission(user, server_id, db, "server.install")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    released = force_release_install_update_lock(server.id)

    if server.status == "installing":
        server.status = "stopped"
        server.status_message = "Manuell entsperrt"
        db.commit()

    return {
        "message": "Installation-Lock freigegeben",
        "released": released,
    }


# ── Erlaubte Origins fuer WebSocket-Upgrades ───────────────────────────────
# Dieselbe Allowlist wie CORS (panel_url + MSM_CORS_ALLOWED_ORIGINS + Dev).


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


class SwitchBlueprintRequest(BaseModel):
    new_blueprint_id: str


@router.post("/{server_id}/switch-blueprint")
def switch_server_blueprint_endpoint(
    server_id: int,
    body: SwitchBlueprintRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Wechselt das Spiel / den Blueprint eines gestoppten Servers.
    Erzeugt AUSNAHMSLOS ein Pflicht-Pre-Switch-Backup ueber das zentrale Backup-System.
    """
    require_server_permission(user, server_id, db, "server.config.write")

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    from services.server_lifecycle_service import switch_server_blueprint
    return switch_server_blueprint(db, server, body.new_blueprint_id, user_id=user.id)
