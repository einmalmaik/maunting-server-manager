"""Gemeinsame, autorisierte Server-Provisionierung mit kompensierendem Rollback."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import shutil

from fastapi import HTTPException
from sqlalchemy.orm import Session

from config import settings
from games import get_plugin
from games.base import container_name_for
from models import Node, OperationTask, Server, User
from schemas import ServerCreate
from services import audit_service, permission_service, postgres_service
from services.actor_context import ActorContext
from services.install_update_lock_service import (
    INSTALL_UPDATE_ALREADY_RUNNING,
    release_install_update_lock,
    try_acquire_install_update_lock,
)
from services.network_interfaces_service import default_bind_ip
from services.operation_task_service import (
    TASK_SERVER_PROVISION,
    create_or_reuse_task,
    finish_server_provisioning,
    mark_failed,
    mark_running,
    set_phase,
)
from services.port_allocation_service import PortConflictError, allocate_ports
from services.port_role_service import blueprint_port_requirements
from services.scheduler_service import sync_server_restart_schedule


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProvisioningResult:
    server: Server
    task: OperationTask
    postgres_credentials: list[dict]
    reused: bool = False


def normalize_server_restart_mode(server: Server) -> None:
    """Hält Intervall- und feste Restart-Zeiten gegenseitig exklusiv."""
    interval = getattr(server, "restart_interval_hours", None)
    times = getattr(server, "restart_times_utc", None) or getattr(server, "restart_time_utc", None)
    if interval:
        server.restart_time_utc = None
        server.restart_times_utc = None
    elif times:
        server.restart_interval_hours = None


def assert_remote_ports_available(
    node: Node | None,
    ports: list[tuple[str, int, str]],
    bind_ip: str,
) -> None:
    """Prüft Ports auf dem Ziel-Node über den bestehenden authentisierten Client."""
    if node is None or node.is_local:
        return
    from services.node_client import NodeClient

    normalized = [(port, protocol, role) for role, port, protocol in ports]
    result = NodeClient.from_node(node).ports_available(normalized, bind_ip or "0.0.0.0")
    if not result.get("available", False):
        raise HTTPException(
            status_code=409,
            detail={"code": "remote_port_conflict", "message": "errors.remote_port_conflict"},
        )


def install_update_busy_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": INSTALL_UPDATE_ALREADY_RUNNING,
            "message": f"errors.{INSTALL_UPDATE_ALREADY_RUNNING}",
        },
    )


def _request_hash(req: ServerCreate) -> str:
    canonical = json.dumps(
        req.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        code = exc.detail.get("code")
        if isinstance(code, str) and code:
            return code[:64]
    if isinstance(exc, HTTPException):
        return {
            400: "provisioning_validation_failed",
            404: "provisioning_target_not_found",
            409: "provisioning_conflict",
            503: "provisioning_dependency_unavailable",
        }.get(exc.status_code, "server_provisioning_failed")
    return "server_provisioning_failed"


def _existing_result(db: Session, task: OperationTask) -> ProvisioningResult:
    if task.status == "failed":
        raise HTTPException(
            status_code=409,
            detail={
                "code": task.error_code or "task_failed",
                "message": task.error_message or "errors.task_failed",
                "task_id": task.id,
            },
        )
    if task.server_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "task_in_progress", "message": "errors.task_in_progress", "task_id": task.id},
        )
    server = db.query(Server).filter(Server.id == task.server_id).first()
    if server is None:
        raise HTTPException(
            status_code=410,
            detail={"code": "task_result_gone", "message": "errors.task_result_gone", "task_id": task.id},
        )
    # One-time Credentials werden bei Replay bewusst nicht rekonstruiert.
    return ProvisioningResult(server=server, task=task, postgres_credentials=[], reused=True)


def provision_server(
    db: Session,
    req: ServerCreate,
    actor: ActorContext,
    *,
    idempotency_key: str | None = None,
    retry_of_id: str | None = None,
) -> ProvisioningResult:
    """Provisioniert über einen einzigen, backendseitig autorisierten Pfad.

    Dateisystem-, Node- und PostgreSQL-Aktionen können nicht Teil einer echten
    SQL-Transaktion sein. Deshalb persistiert der Service zuerst die Task und
    räumt bei Fehlern alle bereits erzeugten Ressourcen in umgekehrter Reihenfolge
    auf. Das ist die kleinste ehrliche Transaktionsgrenze für diesen Datenfluss.
    """
    principal = (
        db.query(User)
        .filter(User.id == actor.user.id, User.is_active.is_(True))
        .first()
    )
    if principal is None:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    actor = ActorContext(
        user=principal,
        origin=actor.origin,
        correlation_id=actor.correlation_id,
    )
    if not permission_service.has_global_permission(db, principal, "servers.create"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    task, created = create_or_reuse_task(
        db,
        actor=actor,
        task_type=TASK_SERVER_PROVISION,
        request_hash=_request_hash(req),
        idempotency_key=idempotency_key,
        retry_of_id=retry_of_id,
    )
    if not created:
        return _existing_result(db, task)

    server: Server | None = None
    target_node: Node | None = None
    created_install_dir = False
    server_deleted = False
    install_lock_acquired = False
    install_started = False
    postgres_credentials: list[dict] = []

    try:
        mark_running(db, task, "validating")
        plugin = get_plugin(req.game_type)
        blueprint = plugin.get_blueprint() if plugin else None
        port_requirements = blueprint_port_requirements(blueprint.ports) if blueprint else [
            ("game", "udp"),
            ("query", "udp"),
            ("rcon", "tcp"),
        ]

        requested_ports = dict(req.ports or {})
        for role, value in (
            ("game", req.game_port),
            ("query", req.query_port),
            ("rcon", req.rcon_port),
        ):
            if value is not None:
                requested_ports[role] = value

        bind_ip = req.public_bind_ip or default_bind_ip()
        from services.node_service import get_local_node

        if req.node_id is not None:
            target_node = db.query(Node).filter(Node.id == req.node_id).first()
            if target_node is None:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "node_not_found", "message": "errors.node_not_found"},
                )
        else:
            target_node = get_local_node(db)
        if target_node is not None and not target_node.is_local and req.public_bind_ip is None:
            bind_ip = "0.0.0.0"

        from services.node_capacity import ensure_ram_limit_fits

        set_phase(db, task, "allocating")
        # Die Node-Zeile serialisiert Kapazitäts- und Portvergabe über mehrere
        # Backend-Prozesse. SQLite ignoriert FOR UPDATE in Tests; PostgreSQL
        # hält den Lock bis Server und Ports gemeinsam committed sind.
        if target_node is not None:
            target_node = (
                db.query(Node)
                .filter(Node.id == target_node.id)
                .with_for_update()
                .one()
            )
        ensure_ram_limit_fits(
            db,
            target_node,
            new_ram_limit_mb=req.ram_limit_mb,
            exclude_server_id=None,
        )
        try:
            allocated = allocate_ports(
                db,
                exclude_server_id=None,
                bind_ip=bind_ip or "0.0.0.0",
                port_requirements=port_requirements,
                requested_ports=requested_ports,
                node_id=target_node.id if target_node else None,
                check_host=target_node is None or target_node.is_local,
            )
        except PortConflictError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "port_conflict", "message": "errors.port_conflict"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_port_request", "message": "errors.invalid_port_request"},
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "port_check_unavailable", "message": "errors.port_check_unavailable"},
            ) from exc

        if isinstance(allocated, tuple) and len(allocated) == 3 and all(isinstance(item, int) for item in allocated):
            allocated = [
                ("game", allocated[0], "udp"),
                ("query", allocated[1], "udp"),
                ("rcon", allocated[2], "tcp"),
            ]
        assert_remote_ports_available(target_node, allocated, bind_ip or "0.0.0.0")

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
            node_id=target_node.id if target_node else None,
        )
        normalize_server_restart_mode(server)
        db.add(server)
        db.flush()

        from models.server_port import ServerPort

        for role, port_value, protocol in allocated:
            db.add(
                ServerPort(
                    server_id=server.id,
                    role=role,
                    port=port_value,
                    protocol=protocol,
                )
            )
        task.phase = "creating"
        task.server_id = server.id
        db.commit()
        db.refresh(server)
        db.refresh(task)

        if plugin:
            install_lock_acquired = try_acquire_install_update_lock(
                server.id,
                "install",
                node_id=server.node_id,
            )
            if not install_lock_acquired:
                db.delete(server)
                db.commit()
                server_deleted = True
                raise install_update_busy_error()

        is_remote_node = bool(target_node is not None and not target_node.is_local)
        install_dir = os.path.join(
            os.path.abspath(settings.servers_dir),
            str(server.id) if is_remote_node else f"{req.game_type}_{server.id}",
        )
        if not is_remote_node and os.path.exists(install_dir):
            db.delete(server)
            db.commit()
            server_deleted = True
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "install_directory_exists",
                    "message": "errors.install_directory_exists",
                },
            )

        try:
            if is_remote_node:
                from services.node_client import NodeClient
                from services.node_service import ensure_node_online

                ensure_node_online(target_node)
                NodeClient.from_node(target_node).files_ensure_server_root(server.id)
            else:
                os.makedirs(install_dir, exist_ok=False)
                # Panel-User und Gruppe dürfen arbeiten; kein world-writable
                # Host-Verzeichnis. Container-Owner werden vor Start gezielt
                # durch den bestehenden Bind-Mount-Repair normalisiert.
                os.chmod(install_dir, 0o750)
            created_install_dir = True
        except OSError as exc:
            db.delete(server)
            db.commit()
            server_deleted = True
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "install_directory_create_failed",
                    "message": "errors.install_directory_create_failed",
                },
            ) from exc

        server.install_dir = install_dir
        server.container_name = container_name_for(server.id)
        db.commit()
        db.refresh(server)

        if req.postgres_enabled:
            set_phase(db, task, "configuring")
            try:
                postgres_credentials = postgres_service.provision_server_databases(
                    db,
                    server,
                    req.postgres_database_count or 1,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "postgres_provision_failed",
                        "message": "errors.postgres_provision_failed",
                    },
                ) from exc

        audit_service.record_privileged_action(
            db,
            user_id=actor.user.id,
            action="server.provision.requested",
            target_type="server",
            target_id=server.id,
            details={"task_id": task.id, "game_type": server.game_type},
            origin=actor.origin,
            correlation_id=actor.correlation_id,
        )
        db.commit()

        if plugin:
            set_phase(db, task, "installing")
            server.status = "installing"
            server.status_message = "Installation gestartet"
            db.commit()
            try:
                install_result = plugin.install(server)
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "server_installation_start_failed",
                        "message": "errors.server_installation_start_failed",
                    },
                ) from exc
            if "error" in install_result:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "server_installation_start_failed",
                        "message": "errors.server_installation_start_failed",
                    },
                )
            install_started = True
        else:
            finish_server_provisioning(db, server.id, succeeded=True)

        sync_server_restart_schedule(server)
        db.refresh(server)
        db.refresh(task)
        return ProvisioningResult(
            server=server,
            task=task,
            postgres_credentials=postgres_credentials,
        )
    except Exception as exc:
        error_code = _failure_code(exc)
        if server is not None and install_lock_acquired and not install_started:
            release_install_update_lock(server.id)
        if server is not None and not install_started and not server_deleted:
            try:
                postgres_service.drop_server_resources(db, server.id)
            except Exception:
                db.rollback()
            try:
                db.delete(server)
                db.commit()
                server_deleted = True
            except Exception:
                db.rollback()
            if created_install_dir:
                try:
                    if target_node is not None and not target_node.is_local:
                        from services.node_client import NodeClient

                        NodeClient.from_node(target_node).files_delete_server_root(server.id)
                    elif server.install_dir and os.path.exists(server.install_dir):
                        shutil.rmtree(server.install_dir)
                except Exception:
                    logger.warning(
                        "Install-Verzeichnis konnte nach Create-Abbruch nicht entfernt werden "
                        "(server_id=%s)",
                        server.id,
                    )
        try:
            persisted_task = db.query(OperationTask).filter(OperationTask.id == task.id).first()
            if persisted_task is not None:
                already_failed = persisted_task.status == "failed"
                if not already_failed:
                    mark_failed(db, persisted_task, error_code=error_code)
                    audit_service.record_privileged_action(
                        db,
                        user_id=actor.user.id,
                        action="server.provision.failed",
                        target_type="server" if persisted_task.server_id else "task",
                        target_id=persisted_task.server_id,
                        details={"task_id": persisted_task.id, "error_code": error_code},
                        origin=actor.origin,
                        correlation_id=actor.correlation_id,
                    )
                    db.commit()
        except Exception:
            db.rollback()
            logger.warning("Provisioning-Task konnte nach Fehler nicht abgeschlossen werden")
        raise
