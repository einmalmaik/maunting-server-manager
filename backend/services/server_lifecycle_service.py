import asyncio
import logging
import threading
from datetime import datetime, timezone
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from games import get_plugin
from games.base import _append_console_log, container_name_for
from models import Server
from services import EmailService, docker_service

from services.docker_iptables_service import accept_server as iptables_accept_server
from services.docker_iptables_service import revoke_server as iptables_revoke_server
from services.firewall_service import close_ports, open_ports
from services.install_update_lock_service import (
    INSTALL_UPDATE_ALREADY_RUNNING,
    release_install_update_lock,
    try_acquire_install_update_lock,
)

logger = logging.getLogger(__name__)

_LIFECYCLE_LOCKS: dict[int, asyncio.Lock] = {}
_THREAD_LOCKS: dict[int, threading.Lock] = {}
_ACTIVE_JOBS: set[int] = set()
_ACTIVE_JOBS_LOCK = threading.Lock()

LifecycleOperation = str
_TRANSIENT_STATUSES = {"queued", "starting", "stopping", "restarting"}


@dataclass(frozen=True)
class LifecycleNotification:
    email: str | None = None
    username: str | None = None
    enabled: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_server_lifecycle_lock(server_id: int) -> asyncio.Lock:
    """Per-Server Lock für ALLE destruktiven Lifecycle-Operationen (start/stop/restart).

    Einheitliche Serialisierung verhindert TOCTOU-Races auf Firewall (UFW close/open)
    und iptables (revoke/accept) sowie Docker-Container-Lifecycle.
    Wird von restart_server_with_updates (manuell + Scheduler) UND start/stop in Routern genutzt.
    KISS: eine Quelle, keine Manager-Klasse, keine neuen Abstraktionen.
    """
    return _LIFECYCLE_LOCKS.setdefault(server_id, asyncio.Lock())


def get_server_lifecycle_thread_lock(server_id: int) -> threading.Lock:
    """Thread-Lock fuer Background-Lifecycle-Jobs.

    Die alten asyncio-Locks schuetzen nur Request-/Scheduler-Coroutines. Da
    manuelle Lifecycle-Aktionen jetzt bewusst ausserhalb des Request-Pfads in
    Threads laufen, braucht der Worker einen passenden Lock-Typ.
    """
    with _ACTIVE_JOBS_LOCK:
        return _THREAD_LOCKS.setdefault(server_id, threading.Lock())


def is_lifecycle_job_active(server_id: int) -> bool:
    with _ACTIVE_JOBS_LOCK:
        return server_id in _ACTIVE_JOBS


def should_preserve_lifecycle_status(server_id: int, status: str) -> bool:
    return status in _TRANSIENT_STATUSES and is_lifecycle_job_active(server_id)


def reset_lifecycle_jobs_for_tests() -> None:
    with _ACTIVE_JOBS_LOCK:
        _ACTIVE_JOBS.clear()


def _mark_job_active(server_id: int) -> bool:
    with _ACTIVE_JOBS_LOCK:
        if server_id in _ACTIVE_JOBS:
            return False
        _ACTIVE_JOBS.add(server_id)
        return True


def _mark_job_done(server_id: int) -> None:
    with _ACTIVE_JOBS_LOCK:
        _ACTIVE_JOBS.discard(server_id)


def _operation_status(operation: LifecycleOperation) -> str:
    if operation == "start":
        return "starting"
    if operation == "stop":
        return "stopping"
    if operation == "restart":
        return "restarting"
    if operation == "kill":
        return "stopping"
    return "queued"


def _operation_done_text(operation: LifecycleOperation) -> str:
    return {
        "start": "gestartet",
        "stop": "gestoppt",
        "restart": "neugestartet",
        "kill": "erzwungen beendet",
    }.get(operation, "aktualisiert")


def _safe_error_message(value: object) -> str:
    text = str(value or "Lifecycle-Aktion fehlgeschlagen").strip()
    return " ".join(text.split())[:500]


def _set_status(db: Session, server: Server, status: str, message: str | None = None) -> None:
    server.status = status
    server.status_message = message
    db.commit()


def queue_lifecycle_operation(
    db: Session,
    server: Server,
    operation: LifecycleOperation,
    notification: LifecycleNotification | None = None,
) -> dict:
    """Startet eine Lifecycle-Aktion ausserhalb des HTTP-Request-Pfads.

    Die Route prueft Auth/RBAC und harte Pre-Checks. Diese Funktion serialisiert
    dann pro Server, setzt sofort einen sichtbaren Queue-Status und startet den
    Worker mit frischer DB-Session.
    """
    if operation not in {"start", "stop", "restart", "kill"}:
        raise ValueError(f"Unbekannte Lifecycle-Operation: {operation}")
    if not _mark_job_active(server.id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "server_lifecycle_already_running",
                "message": "errors.server_lifecycle_already_running",
            },
        )

    _set_status(db, server, "queued", f"{operation} queued")
    try:
        _start_lifecycle_thread(server.id, operation, notification or LifecycleNotification())
    except Exception as exc:
        _mark_job_done(server.id)
        _set_status(db, server, "failed", "Lifecycle-Worker konnte nicht gestartet werden")
        logger.warning("Lifecycle-Worker fuer Server %s konnte nicht gestartet werden: %s", server.id, exc)
        raise HTTPException(status_code=500, detail="Lifecycle-Worker konnte nicht gestartet werden") from exc
    return {
        "message": "Lifecycle-Aktion wurde queued",
        "status": "queued",
        "operation": operation,
    }


def _start_lifecycle_thread(
    server_id: int,
    operation: LifecycleOperation,
    notification: LifecycleNotification,
) -> None:
    thread = threading.Thread(
        target=_run_lifecycle_job,
        args=(server_id, operation, notification),
        daemon=True,
        name=f"msm-lifecycle-{server_id}-{operation}",
    )
    thread.start()


def _run_lifecycle_job(
    server_id: int,
    operation: LifecycleOperation,
    notification: LifecycleNotification | None = None,
) -> None:
    db = SessionLocal()
    lock = get_server_lifecycle_thread_lock(server_id)
    try:
        with lock:
            server = db.query(Server).filter(Server.id == server_id).first()
            if not server:
                return
            plugin = get_plugin(server.game_type)
            if not plugin:
                _set_status(db, server, "failed", "Spiel-Typ nicht unterstützt")
                return

            _set_status(db, server, _operation_status(operation), None)
            try:
                if operation == "start":
                    _run_start(db, server, plugin)
                elif operation == "stop":
                    _run_stop(db, server, plugin)
                elif operation == "restart":
                    _run_restart(db, server, plugin)
                elif operation == "kill":
                    _run_kill(db, server)
                if notification and notification.enabled:
                    _send_lifecycle_notification(notification, server.name, _operation_done_text(operation))
            except Exception as exc:
                db.rollback()
                server = db.query(Server).filter(Server.id == server_id).first()
                if server:
                    message = _safe_error_message(getattr(exc, "detail", exc))
                    server.status = "failed"
                    server.status_message = message
                    db.commit()
                    _append_console_log(server.id, f"[MSM] Lifecycle-{operation} fehlgeschlagen: {message}\n")
                logger.warning("Lifecycle-%s fuer Server %s fehlgeschlagen: %s", operation, server_id, exc)
    finally:
        _mark_job_done(server_id)
        db.close()


def _send_lifecycle_notification(
    notification: LifecycleNotification,
    server_name: str,
    status_text: str,
) -> None:
    if not notification.email or not notification.username:
        return
    if not EmailService.is_configured():
        return
    try:
        import asyncio as _asyncio

        _asyncio.run(
            EmailService.send_server_status_notification(
                notification.email,
                notification.username,
                server_name,
                status_text,
            )
        )
    except Exception:
        logger.warning("Lifecycle-E-Mail konnte nicht gesendet werden (details redacted for security)")


def _ensure_bind_ip(server: Server) -> None:
    if not server.public_bind_ip:
        raise HTTPException(
            status_code=400,
            detail=(
                "Server hat keine Bind-IP konfiguriert. Bitte im Server-Detail "
                "eine Public-IP zuweisen, bevor er gestartet wird."
            ),
        )


def _ports(server: Server) -> list[tuple[int, str, str]]:
    return [(p.port, p.protocol, p.role) for p in server.ports]


def _run_start(db: Session, server: Server, plugin) -> None:
    _ensure_bind_ip(server)
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
    try:
        if mod_updates:
            update_lock_acquired = try_acquire_install_update_lock(server.id, "start_update")
            if not update_lock_acquired:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": INSTALL_UPDATE_ALREADY_RUNNING,
                        "message": f"errors.{INSTALL_UPDATE_ALREADY_RUNNING}",
                    },
                )

        ports_list = _ports(server)
        open_ports(server.name, ports_list)
        iptables_accept_server(server.name, server.public_bind_ip or "", ports_list)

        if mod_updates:
            _append_console_log(
                server.id,
                f"[MSM] {len(mod_updates)} Workshop-Mod(s) beim Start erkannt - "
                "fuehre Download via install_mod/run_steamcmd_workshop_download aus...\n",
            )
            mod_res = plugin.perform_workshop_mod_updates(server, only_auto_update=True)
            if not mod_res.get("ok", False):
                _append_console_log(
                    server.id,
                    f"[MSM] Workshop-Mod-Update beim Start hatte Probleme (nicht kritisch): "
                    f"{mod_res.get('error') or mod_res}\n",
                )
    finally:
        if update_lock_acquired:
            release_install_update_lock(server.id)

    db.refresh(server)
    if server.backup_on_start:
        from services.backup_service import run_backup

        try:
            run_backup(server.id, db, timeout_seconds=300)
        except Exception:
            logger.warning("Pre-Start-Backup fehlgeschlagen fuer Server %s (details redacted for security)", server.id)

    result = plugin.start(server)
    if "error" in result:
        ports_list = _ports(server)
        close_ports(ports_list)
        iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)
        raise HTTPException(status_code=500, detail=result["error"])
    server.status = "running"
    server.status_message = None
    server.last_started_at = _utcnow()
    db.commit()


def _run_stop(db: Session, server: Server, plugin) -> None:
    result = plugin.stop(server)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    server.status = "stopped"
    server.status_message = None
    server.last_started_at = None
    db.commit()
    ports_list = _ports(server)
    close_ports(ports_list)
    iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)


def _run_kill(db: Session, server: Server) -> None:
    container = container_name_for(server.id)
    result = docker_service.remove(container, force=True)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail="Erzwungenes Beenden fehlgeschlagen")
    server.status = "stopped"
    server.status_message = "Erzwungen beendet"
    server.last_started_at = None
    db.commit()
    ports_list = _ports(server)
    close_ports(ports_list)
    iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)


def _run_restart(db: Session, server: Server, plugin) -> None:
    _ensure_bind_ip(server)
    server_update: dict = {}
    mod_updates: list[dict] = []
    try:
        plugin.prepare_for_updates(server)
        server_update = plugin.check_for_server_file_update(server)
        mod_updates = plugin.check_for_mod_updates(server)
    except Exception as exc:
        _append_console_log(
            server.id,
            f"[MSM] Updater-Check waehrend Restart fehlgeschlagen (nicht kritisch): {exc}\n",
        )
        logger.warning("Updater-Check beim Restart von Server %s fehlgeschlagen: %s", server.id, exc)

    update_lock_acquired = False
    try:
        needs_update_job = server_update.get("action") == "update" or bool(mod_updates)
        if needs_update_job:
            update_lock_acquired = try_acquire_install_update_lock(server.id, "restart_update")
            if not update_lock_acquired:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": INSTALL_UPDATE_ALREADY_RUNNING,
                        "message": f"errors.{INSTALL_UPDATE_ALREADY_RUNNING}",
                    },
                )

        ports_list = _ports(server)
        close_ports(ports_list)
        iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)

        stop_result = plugin.stop(server)
        if "error" in stop_result:
            raise HTTPException(status_code=500, detail=stop_result["error"])

        if server_update.get("action") == "update":
            _append_console_log(
                server.id,
                f"[MSM] Server-Datei-Update erkannt ({server_update.get('reason')}). "
                "Update wird vor dem Container-Start ausgefuehrt.\n",
            )
            update_res = plugin.perform_server_file_update(server)
            if not update_res.get("ok", False):
                _append_console_log(
                    server.id,
                    f"[MSM] Server-Datei-Update fehlgeschlagen (Restart wird fortgesetzt): "
                    f"{update_res.get('error') or update_res}\n",
                )

        if mod_updates:
            _append_console_log(
                server.id,
                f"[MSM] {len(mod_updates)} Workshop-Mod(s) benoetigen Update/Installation. "
                "Download laeuft vor dem Container-Start.\n",
            )
            mod_res = plugin.perform_workshop_mod_updates(server, only_auto_update=True)
            if not mod_res.get("ok", False):
                _append_console_log(
                    server.id,
                    f"[MSM] Workshop-Mod-Update fehlgeschlagen (Restart wird fortgesetzt): "
                    f"{mod_res.get('error') or mod_res}\n",
                )
    finally:
        if update_lock_acquired:
            release_install_update_lock(server.id)

    db.refresh(server)
    if server.backup_on_start:
        from services.backup_service import run_backup

        try:
            run_backup(server.id, db, timeout_seconds=300)
        except Exception:
            logger.warning("Pre-Start-Backup fehlgeschlagen fuer Server %s (details redacted for security)", server.id)

    start_result = plugin.start(server)
    if "error" in start_result:
        raise HTTPException(status_code=500, detail=start_result["error"])

    ports_list = _ports(server)
    open_ports(server.name, ports_list)
    iptables_accept_server(server.name, server.public_bind_ip or "", ports_list)

    server.status = "running"
    server.status_message = None
    server.last_started_at = _utcnow()
    db.commit()


async def restart_server_with_updates(db: Session, server: Server) -> dict:
    """Restartet einen Server über den zentralen Lifecycle-Pfad.

    Der Pfad ist absichtlich klein und wird von manuellem Restart und
    Auto-Restart genutzt, damit Server-Datei-Updates, Mod-Updates, Firewall und
    iptables nicht auseinanderlaufen.

    Auto-Restart bleibt async aufrufbar, nutzt aber denselben Thread-Lock und
    dieselbe Restart-Implementierung wie manuelle Background-Jobs.
    """
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")

    if not _mark_job_active(server.id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "server_lifecycle_already_running",
                "message": "errors.server_lifecycle_already_running",
            },
        )

    lock = get_server_lifecycle_thread_lock(server.id)
    try:
        with lock:
            db.refresh(server)
            _set_status(db, server, "restarting", None)
            try:
                _run_restart(db, server, plugin)
            except Exception as exc:
                db.rollback()
                db.refresh(server)
                message = _safe_error_message(getattr(exc, "detail", exc))
                server.status = "failed"
                server.status_message = message
                db.commit()
                _append_console_log(server.id, f"[MSM] Lifecycle-restart fehlgeschlagen: {message}\n")
                raise

        db.refresh(server)
        return {
            "message": "Restart-Befehl gesendet",
            "status": server.status,
        }
    finally:
        _mark_job_done(server.id)
