import asyncio
import logging
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import AsyncIterator

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
_LIFECYCLE_GUARDIAN_LEASE = timedelta(hours=4)

# Per-Server Serialisierungs-Lock. threading.Lock (nicht reentrant) damit
# ein Lifecycle-Job sich nicht selbst nochmal greifen kann. Wird sowohl
# aus Worker-Threads (Lifecycle-Worker) als auch aus async Pfaden
# (Scheduler-Auto-Restart, Backup-Stop-Hook) via ``acquire_lock_async``
# verwendet -- so gibt es pro Server NUR EIN Lock, das beide Pfade
# serialisiert.
_LIFECYCLE_LOCKS: dict[int, threading.Lock] = {}
_LIFECYCLE_LOCKS_GUARD = threading.Lock()
_ACTIVE_JOBS: set[int] = set()
_ACTIVE_JOBS_LOCK = threading.Lock()
_OPERATION_EPOCHS: dict[int, int] = {}

LifecycleOperation = str


class LifecycleOperationCancelled(RuntimeError):
    """Raised when a newer operation invalidates an in-flight lifecycle job."""


@contextmanager
def guardian_recovery_suspension_lease(db: Session, server: Server, reason: str):
    node = server.node
    if node is None or node.status != "online":
        yield
        return

    op_id = str(uuid.uuid4())
    from services.guardian_state_service import set_recovery_suspension, clear_recovery_suspension
    from services.guardian_sync_service import compile_and_sync_desired_state
    
    set_recovery_suspension(
        db,
        server,
        operation_id=op_id,
        reason=reason,
        # First installs and large Wine/Proton updates legitimately exceed
        # fifteen minutes. Use the contract's bounded maximum so Guardian
        # cannot recover or quarantine during a still-running panel action.
        suspend_until=datetime.now(timezone.utc) + _LIFECYCLE_GUARDIAN_LEASE,
    )

    try:
        sync_res = compile_and_sync_desired_state(db, server)
    except Exception:
        clear_recovery_suspension(db, server, operation_id=op_id)
        raise

    confirmed_lease = (
        sync_res.get("recovery_suspension")
        if isinstance(sync_res, dict)
        else None
    )
    if (
        not isinstance(confirmed_lease, dict)
        or confirmed_lease.get("operation_id") != op_id
    ):
        clear_recovery_suspension(db, server, operation_id=op_id)
        raise RuntimeError(f"Guardian recovery lease sync failed for server {server.id}")

    try:
        yield
    finally:
        clear_recovery_suspension(db, server, operation_id=op_id)
        try:
            clear_sync_res = compile_and_sync_desired_state(db, server)
            if (
                not isinstance(clear_sync_res, dict)
                or clear_sync_res.get("recovery_suspension") is not None
            ):
                logger.warning(
                    "Guardian recovery lease removal was not acknowledged for server %s",
                    server.id,
                )
        except Exception as exc:
            # The durable panel intent is already cleared and periodic
            # reconciliation retries delivery. A cleanup transport failure must
            # not turn an otherwise successful start/restart into `failed`.
            logger.warning(
                "Guardian recovery lease cleanup sync failed for server %s: %s",
                server.id,
                type(exc).__name__,
            )
_TRANSIENT_STATUSES = {"queued", "starting", "stopping", "restarting"}


@dataclass(frozen=True)
class LifecycleNotification:
    email: str | None = None
    username: str | None = None
    enabled: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_server_lifecycle_lock(server_id: int) -> threading.Lock:
    """Per-Server Lock fuer ALLE destruktiven Lifecycle-Operationen.

    Wird sowohl aus Worker-Threads (via ``with lock:``) als auch aus async
    Pfaden (via ``async with acquire_lock_async(lock):``) verwendet. Ein
    einzelner threading.Lock pro Server serialisiert manuelle Starts,
    Auto-Restarts und Backup-Container-Stops miteinander -- vorher gab
    es zwei Lock-Typen (asyncio.Lock + threading.Lock) was parallele
    Pfade nicht serialisierte.
    """
    with _LIFECYCLE_LOCKS_GUARD:
        lock = _LIFECYCLE_LOCKS.get(server_id)
        if lock is None:
            lock = threading.Lock()
            _LIFECYCLE_LOCKS[server_id] = lock
        return lock


@asynccontextmanager
async def acquire_lock_async(lock: threading.Lock) -> AsyncIterator[None]:
    """Async-Bruecke fuer ``threading.Lock``.

    ``async with acquire_lock_async(lock):`` blockiert den awaiter
    asynchron, ohne den Event-Loop zu blockieren, und haelt den Lock bis
    zum Verlassen des Blocks. Ersetzt ``async with lock:`` (das auf
    asyncio.Lock zugeschnitten ist und mit threading.Lock nicht
    funktioniert).
    """
    await asyncio.to_thread(lock.acquire)
    try:
        yield
    finally:
        lock.release()


def is_lifecycle_job_active(server_id: int) -> bool:
    with _ACTIVE_JOBS_LOCK:
        return server_id in _ACTIVE_JOBS


def should_preserve_lifecycle_status(server_id: int, status: str) -> bool:
    return status in _TRANSIENT_STATUSES and is_lifecycle_job_active(server_id)


def reconcile_orphaned_lifecycle_statuses(db: Session) -> int:
    """Nach Prozess-Neustart: DB kann noch ``starting``/``stopping`` zeigen, obwohl der
    In-Memory-Job weg ist. Status an Docker-Realität anbinden, damit das Panel nicht
    ewig „Startet…“ anzeigt und WebSockets sinnlos offen bleiben."""
    from services.node_service import probe_node_metrics

    servers = db.query(Server).filter(Server.status.in_(_TRANSIENT_STATUSES)).all()
    changed = 0
    # Ein Probe je Node, nicht je Server.
    node_reachable: dict[int, bool] = {}
    for server in servers:
        if is_lifecycle_job_active(server.id):
            continue
        node = server.node
        if node is not None:
            if node.id not in node_reachable:
                # Bewusst nicht `node.status` auswerten: dieser Lauf passiert beim
                # Start, bevor der Heartbeat-Job registriert ist. Der gespeicherte
                # Status stammt dann noch von vor dem Herunterfahren und stuende
                # gerade im gemeinsamen Neustart von Panel und Node auf "online".
                node_reachable[node.id] = (
                    probe_node_metrics(node, mark_status=False) is not None
                )
            if not node_reachable[node.id]:
                # `inspect_state` kann "Container weg" nicht von "Agent nicht
                # erreichbar" unterscheiden und liefert beides als None, woraus
                # `get_status` ein "stopped" macht. Fuer einen tatsaechlich
                # laufenden Server waere das eine falsche Tatsachenbehauptung:
                # der Auto-Restart wuerde stillschweigend uebersprungen und ein
                # Blueprint-Wechsel koennte das Verzeichnis unter einem
                # laufenden Container loeschen. Lieber den Uebergangsstatus
                # stehen lassen — der Statusabruf korrigiert ihn, sobald die
                # Node wieder antwortet.
                continue
        plugin = get_plugin(server.game_type)
        if not plugin:
            server.status = "failed"
            server.status_message = "Spiel-Typ nicht unterstützt"
            changed += 1
            continue
        plugin_status = plugin.get_status(server)
        server.status = plugin_status.status
        server.status_message = (plugin_status.message or None) if plugin_status.message else None
        if plugin_status.status == "running" and plugin_status.started_at is not None:
            server.last_started_at = plugin_status.started_at
        elif plugin_status.status != "running":
            server.last_started_at = None
        changed += 1
    if changed:
        db.commit()
    return changed


# Frisches Backup vor erneutem Start überspringen (verhindert doppelte 10GB+ tar.gz
# innerhalb kurzer Zeit und verkürzt „hängendes“ Starting bei backup_on_start).
_PRE_START_BACKUP_SKIP_MINUTES = 30


def _run_pre_start_backup_if_enabled(db: Session, server: Server, *, context: str) -> None:
    if not server.backup_on_start:
        return
    from models import Backup
    from services.backup_orchestrator import create_server_backup

    last = (
        db.query(Backup)
        .filter(Backup.server_id == server.id)
        .order_by(Backup.created_at.desc())
        .first()
    )
    if last and last.created_at is not None:
        created = last.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_min = (_utcnow() - created.astimezone(timezone.utc)).total_seconds() / 60.0
        if age_min < _PRE_START_BACKUP_SKIP_MINUTES:
            _append_console_log(
                server.id,
                f"[MSM] Backup vor {context} übersprungen: vor {int(age_min)} Min. bereits ein Backup "
                f"(Schwelle {_PRE_START_BACKUP_SKIP_MINUTES} Min.).\n",
            )
            return

    _append_console_log(
        server.id,
        f"[MSM] Backup vor {context} läuft (große Server-Verzeichnisse können mehrere Minuten "
        f"dauern, Timeout 300s). Panel bleibt erreichbar, Konsole aktualisiert sich danach.\n",
    )
    try:
        # Orchestrator uebernimmt lokales tar.gz + S3-Upload (Best-Effort, wenn konfiguriert).
        create_server_backup(server.id, db, timeout_seconds=300)
        _append_console_log(server.id, f"[MSM] Backup vor {context} abgeschlossen.\n")
    except Exception as exc:
        _append_console_log(
            server.id,
            f"[MSM] Backup vor {context} fehlgeschlagen ({_safe_error_message(exc)}); "
            f"{context} wird fortgesetzt.\n",
        )
        logger.warning(
            "Pre-Start-Backup fehlgeschlagen für Server %s (details redacted for security)",
            server.id,
        )


def reset_lifecycle_jobs_for_tests() -> None:
    with _ACTIVE_JOBS_LOCK:
        _ACTIVE_JOBS.clear()
        _OPERATION_EPOCHS.clear()


def _mark_job_active(server_id: int) -> bool:
    with _ACTIVE_JOBS_LOCK:
        if server_id in _ACTIVE_JOBS:
            return False
        _ACTIVE_JOBS.add(server_id)
        return True


def _mark_job_done(server_id: int) -> None:
    with _ACTIVE_JOBS_LOCK:
        _ACTIVE_JOBS.discard(server_id)


def _advance_operation_epoch(server_id: int) -> int:
    with _ACTIVE_JOBS_LOCK:
        epoch = _OPERATION_EPOCHS.get(server_id, 0) + 1
        _OPERATION_EPOCHS[server_id] = epoch
        return epoch


def _operation_is_current(server_id: int, operation_epoch: int) -> bool:
    with _ACTIVE_JOBS_LOCK:
        return _OPERATION_EPOCHS.get(server_id) == operation_epoch


def _ensure_operation_current(server_id: int, operation_epoch: int | None) -> None:
    if operation_epoch is not None and not _operation_is_current(
        server_id,
        operation_epoch,
    ):
        raise LifecycleOperationCancelled(
            f"Lifecycle operation for server {server_id} was cancelled"
        )


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
    previous_status = server.status
    server.status = status
    server.status_message = message
    db.commit()

    # Outbound-Webhook-Trigger: nur bei echten Status-Wechseln feuern
    # (ueberspringt "transient" -> "transient"-Pings, die kein Event sind).
    # Wird asyncron aus dem Main-Loop angestossen, damit der HTTP-Request
    # nicht blockiert. Fehler im Webhook-Versand duerfen Lifecycle nie
    # beeintraechtigen.
    if previous_status != status:
        from services.outbound_webhook_service import (
            EVENT_STATUS_CHANGE,
            build_status_payload,
            dispatch_event,
        )
        try:
            new_payload = build_status_payload(server)
            # Sync dispatch (Fire-and-forget) — verfuegbare Subs
            # werden in einem Background-Task rausgeschickt.
            import asyncio as _asyncio
            try:
                loop = _asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.create_task(
                    dispatch_event(
                        db, server=server,
                        event_type=EVENT_STATUS_CHANGE,
                        payload=new_payload,
                    ),
                    name=f"webhook-status-{server.id}",
                )
        except Exception as _exc:  # pragma: no cover — defensive
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "outbound-webhook: dispatch failed for server_id=%s (%s)",
                server.id, _exc,
            )


def queue_lifecycle_operation(
    db: Session,
    server: Server,
    operation: LifecycleOperation,
    notification: LifecycleNotification | None = None,
    task_id: str | None = None,
) -> dict:
    """Startet eine Lifecycle-Aktion ausserhalb des HTTP-Request-Pfads.

    Die Route prueft Auth/RBAC und harte Pre-Checks. Diese Funktion serialisiert
    dann pro Server, setzt sofort einen sichtbaren Queue-Status und startet den
    Worker mit frischer DB-Session.

    Auch Kill läuft durch denselben per-Server Lock. Ein harter Bypass waere
    zwar schneller, koennte aber parallel zu Backup, Restore, Update oder
    Guardian-Recovery den falschen Container-/Dateizustand zerstoeren.
    """
    if operation not in {"start", "stop", "restart", "kill"}:
        raise ValueError(f"Unbekannte Lifecycle-Operation: {operation}")

    if operation == "kill":
        # HARD KILL PATH: Sofort, unabhaengig von aktivem Job.
        # Das loest das User-Problem "Kill bringt den Server nicht aus der Warteschlange".
        from services.guardian_state_service import set_desired_power_state

        _advance_operation_epoch(server.id)
        set_desired_power_state(db, server, "stopped")
        sync_desired_state_to_agent(db, server)

        container = container_name_for(server.id)
        try:
            remove_result = docker_service.remove(
                container,
                force=True,
                node=server.node,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="Erzwungenes Beenden fehlgeschlagen",
            ) from exc
        if isinstance(remove_result, dict) and remove_result.get("error"):
            raise HTTPException(
                status_code=500,
                detail="Erzwungenes Beenden fehlgeschlagen",
            )

        server.status = "stopped"
        server.status_message = "Erzwungen beendet (Kill)"
        server.last_started_at = None
        db.commit()

        from services.change_timeline_service import log_change_event
        log_change_event(db, server.id, "stop", "Server erzwungen beendet.")

        ports_list = _ports(server)
        try:
            close_ports(ports_list, node=server.node, name=server.name)
            if server.node is None or server.node.is_local:
                from services.docker_iptables_service import revoke_server as iptables_revoke_server
                iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)
        except Exception:
            pass

        _append_console_log(server.id, "[MSM] Server hart via Kill beendet (auch aus Queue/Start heraus, Docker auto-restarts disabled)\n")
        return {
            "message": "Server wurde erzwungen beendet",
            "status": "stopped",
            "operation": "kill",
        }

    if not _mark_job_active(server.id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "server_lifecycle_already_running",
                "message": "errors.server_lifecycle_already_running",
            },
        )

    try:
        if operation in {"start", "stop", "restart", "kill"}:
            from services.guardian_state_service import set_desired_power_state

            desired = "running" if operation in {"start", "restart"} else "stopped"
            set_desired_power_state(db, server, desired)
            # Commit happened inside set_desired_power_state. Sync is best
            # effort; periodic reconciliation retries network failures.
            sync_desired_state_to_agent(db, server)
        _set_status(db, server, "queued", f"{operation} queued")
        operation_epoch = _advance_operation_epoch(server.id)
        _start_lifecycle_thread(
            server.id,
            operation,
            notification or LifecycleNotification(),
            operation_epoch,
            task_id,
        )
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
    operation_epoch: int,
    task_id: str | None = None,
) -> None:
    thread = threading.Thread(
        target=_run_lifecycle_job,
        args=(server_id, operation, notification, operation_epoch, task_id),
        daemon=True,
        name=f"msm-lifecycle-{server_id}-{operation}",
    )
    thread.start()


def _run_lifecycle_job(
    server_id: int,
    operation: LifecycleOperation,
    notification: LifecycleNotification | None = None,
    operation_epoch: int | None = None,
    task_id: str | None = None,
) -> None:
    if operation_epoch is None:
        operation_epoch = _advance_operation_epoch(server_id)
    db = SessionLocal()
    lock = get_server_lifecycle_lock(server_id)
    task_succeeded = False
    task_error_code = "server_lifecycle_failed"
    try:
        with lock:
            _ensure_operation_current(server_id, operation_epoch)
            server = db.query(Server).filter(Server.id == server_id).first()
            if not server:
                task_error_code = "server_not_found"
                return
            plugin = get_plugin(server.game_type)
            if not plugin:
                _set_status(db, server, "failed", "Spiel-Typ nicht unterstützt")
                task_error_code = "unsupported_game_type"
                return

            _set_status(db, server, _operation_status(operation), None)
            with guardian_recovery_suspension_lease(db, server, f"lifecycle-{operation}"):
                try:
                    if operation == "start":
                        _run_start(
                            db,
                            server,
                            plugin,
                            operation_epoch=operation_epoch,
                        )
                    elif operation == "stop":
                        _run_stop(db, server, plugin)
                    elif operation == "restart":
                        _run_restart(
                            db,
                            server,
                            plugin,
                            operation_epoch=operation_epoch,
                        )
                    elif operation == "kill":
                        _run_kill(db, server)
                    task_succeeded = True
                    if notification and notification.enabled:
                        _send_lifecycle_notification(notification, server.name, _operation_done_text(operation))
                except LifecycleOperationCancelled:
                    db.rollback()
                    task_error_code = "lifecycle_superseded"
                    logger.info(
                        "Lifecycle-%s fuer Server %s wurde durch eine neuere Operation abgebrochen",
                        operation,
                        server_id,
                    )
                except Exception as exc:
                    db.rollback()
                    task_error_code = "server_lifecycle_failed"
                    server = db.query(Server).filter(Server.id == server_id).first()
                    if server:
                        message = _safe_error_message(getattr(exc, "detail", exc))
                        server.status = "failed"
                        server.status_message = message
                        db.commit()
                        _append_console_log(server.id, f"[MSM] Lifecycle-{operation} fehlgeschlagen: {message}\n")
                    logger.warning("Lifecycle-%s fuer Server %s fehlgeschlagen: %s", operation, server_id, exc)
    except Exception as exc:
        db.rollback()
        task_error_code = "server_lifecycle_failed"
        logger.warning(
            "Lifecycle-%s fuer Server %s ausserhalb der Operation fehlgeschlagen: %s",
            operation,
            server_id,
            type(exc).__name__,
        )
        if task_id is None:
            raise
    finally:
        from services.operation_task_service import finish_lifecycle_task

        try:
            finish_lifecycle_task(
                db,
                task_id,
                succeeded=task_succeeded,
                error_code=task_error_code,
            )
        except Exception:
            db.rollback()
            logger.warning(
                "Lifecycle-Task konnte nicht abgeschlossen werden (server_id=%s)",
                server_id,
            )
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
    # EmailService ist async (aiosmtplib/httpx). Wir laufen in einem Daemon-
    # Worker-Thread ohne Zugriff auf die FastAPI-Event-Loop, also koennen wir
    # nicht call_soon_threadsafe nutzen. Pragmatische Loesung: pro E-Mail eine
    # frische Event-Loop, sauber geschlossen (kein Leak). Overhead ist
    # akzeptabel, weil Lifecycle-E-Mails selten sind (eine pro Start/Stop).
    try:
        import asyncio as _asyncio

        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                EmailService.send_server_status_notification(
                    notification.email,
                    notification.username,
                    server_name,
                    status_text,
                )
            )
        finally:
            loop.close()
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


def _check_server_file_update(server: Server, plugin, operation: str) -> dict:
    try:
        return plugin.check_for_server_file_update(server)
    except Exception as exc:
        _append_console_log(
            server.id,
            f"[MSM] Server-Datei-Update-Check während {operation} fehlgeschlagen "
            f"(nicht kritisch): {exc}\n",
        )
        logger.warning("Server-Datei-Update-Check für Server %s fehlgeschlagen: %s", server.id, exc)
        return {"action": "none", "reason": "error"}


def _server_file_update_needed(update_check: dict | None) -> bool:
    return bool(update_check and update_check.get("action") == "update")


def _source_update_strategy(plugin):
    """Liefert die effektive Update-Strategie aus der Blueprint.

    Provider-neutral: liest ``source.updateStrategy`` (Default pro Source-Type).
    Kein Steam-only-Hardcode im Python-Core.
    """
    from blueprints.schema import BlueprintUpdateStrategy
    try:
        bp = plugin.get_blueprint()
        if bp and bp.source is not None and hasattr(bp.source, "effective_update_strategy"):
            return bp.source.effective_update_strategy()
    except Exception as exc:
        logger.debug("Blueprint-Lookup fuer Update-Strategie fehlgeschlagen: %s", exc)
    return BlueprintUpdateStrategy.CHECK_BASED


def _run_server_file_update_if_needed(
    server: Server,
    plugin,
    operation: str,
    *,
    update_check: dict | None = None,
) -> None:
    from blueprints.schema import BlueprintUpdateStrategy

    strategy = _source_update_strategy(plugin)
    if strategy == BlueprintUpdateStrategy.ALWAYS_VALIDATE:
        effective_check = {"action": "update"}
    elif strategy == BlueprintUpdateStrategy.CHECK_BASED:
        effective_check = update_check
    else:
        return

    if not _server_file_update_needed(effective_check):
        return
    _append_console_log(
        server.id,
        f"[MSM] Server-Datei-Update wird durchgeführt (Strategie: {strategy.value})...\n",
    )
    result = plugin.perform_server_file_update(server)
    if result.get("ok", False):
        _append_console_log(server.id, "[MSM] Server-Datei-Update abgeschlossen.\n")
        return
    _append_console_log(
        server.id,
        f"[MSM] Server-Datei-Update hatte Probleme während {operation}; Start wird fortgesetzt: "
        f"{result.get('error') or result}\n",
    )


def _try_start_auth_setup_recovery(
    db: Session, server: Server, plugin, start_error: str
) -> bool:
    """Erkennt Auth-Pattern im Container-Start-Error und startet Recovery.

    Returnt True wenn Recovery gestartet wurde (Caller muss dann KEIN HTTPException
    mehr werfen). Returnt False wenn kein Auth-Pattern erkannt wurde und der
    Caller normal weiter machen soll.

    Generisch: nutzt nur die ``logs`` aus docker_service und Blueprint-Plugin-API.
    """
    from services.auth_setup_service import (
        detect_auth_required,
        run_auth_setup_recovery,
    )

    # Container-Logs holen (bis zu 4KB aus docker_service.run_container).
    # Wir holen nochmal frisch vom Docker-Daemon, falls der Container
    # zwischenzeitlich weg ist (entfernt beim startup_check).
    container_name = container_name_for(server.id)
    try:
        raw_logs = docker_service.logs(container_name, lines=200, node=server.node)
    except Exception:
        raw_logs = start_error  # fallback: error-string selbst hat die URL

    log_lines = raw_logs.splitlines()
    if not detect_auth_required(log_lines):
        return False

    # Auth-Pattern erkannt. Server-Status auf "awaiting_auth" setzen
    # und Recovery-Thread starten.
    server.auth_required = True
    server.status_message = "Auth-Setup erforderlich (siehe Konsole)"
    db.commit()
    _append_console_log(
        server.id,
        "[MSM] Auth-Setup erkannt. Credentials werden zurueckgesetzt, "
        "Container startet im TTY-Modus...\n",
    )

    install_dir = server.install_dir
    port_publishes = plugin.build_port_publishes(server)
    volume_binds = plugin.build_volume_binds(server)
    uid, gid = plugin.container_uid_gid(server)
    container_user = f"{uid}:{gid}"

    def on_log(text: str) -> None:
        _append_console_log(server.id, text)

    def on_status(auth_required: bool, status_message: str | None) -> None:
        # Eigene DB-Session weil der Background-Thread lange laeuft.
        with SessionLocal() as bg_db:
            bg_server = bg_db.query(Server).filter(Server.id == server.id).first()
            if bg_server:
                bg_server.auth_required = auth_required
                bg_server.status_message = status_message
                bg_db.commit()

    def restart_callback() -> None:
        # Clean-Restart ueber queue_lifecycle_operation, damit die normalen
        # Lifecycle-Hooks (Status-Updates, Notifications) durchlaufen.
        from services.server_lifecycle_service import queue_lifecycle_operation
        from services.server_lifecycle_service import LifecycleNotification
        with SessionLocal() as bg_db:
            bg_server = bg_db.query(Server).filter(Server.id == server.id).first()
            if bg_server:
                queue_lifecycle_operation(
                    bg_db,
                    bg_server,
                    "restart",
                    LifecycleNotification(None, "auth-setup-recovery", False),
                )

    def recovery_worker() -> None:
        try:
            run_auth_setup_recovery(
                server_id=server.id,
                install_dir=install_dir,
                docker_image=plugin.docker_image,
                container_command=plugin.build_container_command(server),
                container_env=plugin.build_container_env(server),
                port_publishes=port_publishes,
                volume_binds=volume_binds,
                cpu_limit_percent=server.cpu_limit_percent,
                ram_limit_mb=server.ram_limit_mb,
                container_user=container_user,
                container_workdir=plugin.container_workdir(server),
                container_read_only_rootfs=plugin.container_read_only_rootfs,
                container_tmpfs_paths=plugin.container_tmpfs_paths(server),
                container_extra_networks=plugin.container_extra_networks(server),
                container_name=container_name,
                on_log=on_log,
                on_status=on_status,
                restart_callback=restart_callback,
                node=server.node,
            )
        except Exception as exc:
            logger.warning("Auth-Setup-Recovery fuer Server %s fehlgeschlagen: %s", server.id, exc)
            with SessionLocal() as bg_db:
                bg_server = bg_db.query(Server).filter(Server.id == server.id).first()
                if bg_server:
                    bg_server.auth_required = False
                    bg_server.status_message = f"Auth-Setup Fehler: {exc}"
                    bg_db.commit()
            _append_console_log(server.id, f"[MSM] Auth-Setup-Recovery Fehler: {exc}\n")

    threading.Thread(
        target=recovery_worker,
        daemon=True,
        name=f"auth-setup-{server.id}",
    ).start()
    return True


def _run_start(
    db: Session,
    server: Server,
    plugin,
    *,
    operation_epoch: int | None = None,
) -> None:
    from blueprints.schema import BlueprintUpdateStrategy

    _ensure_bind_ip(server)
    mod_updates: list[dict] = []
    server_update_check: dict | None = None
    strategy = _source_update_strategy(plugin)
    try:
        plugin.prepare_for_updates(server)
        if strategy != BlueprintUpdateStrategy.NONE:
            server_update_check = _check_server_file_update(server, plugin, "start")
        mod_updates = plugin.check_for_mod_updates(server)
    except Exception as exc:
        _append_console_log(
            server.id,
            f"[MSM] prepare_for_updates / Mod-Update-Check beim Start fehlgeschlagen (nicht kritisch): {exc}\n",
        )
        mod_updates = []

    update_lock_acquired = False
    try:
        if _server_file_update_needed(server_update_check) or mod_updates:
            update_lock_acquired = try_acquire_install_update_lock(server.id, "start_update", node_id=server.node_id)
            if not update_lock_acquired:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": INSTALL_UPDATE_ALREADY_RUNNING,
                        "message": f"errors.{INSTALL_UPDATE_ALREADY_RUNNING}",
                    },
                )

        _run_server_file_update_if_needed(server, plugin, "start", update_check=server_update_check)

        if mod_updates:
            _append_console_log(
                server.id,
                f"[MSM] {len(mod_updates)} Workshop-Mod(s) beim Start erkannt - "
                "führe gebündelten Workshop-Download aus...\n",
            )
            mod_res = plugin.perform_workshop_mod_updates(server, only_auto_update=False)
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
    _ensure_operation_current(server.id, operation_epoch)
    _append_console_log(
        server.id,
        "[MSM] Start-Vorbereitung: Datei-/Mod-Updates abgeschlossen, optional Backup, dann Container.\n",
    )
    _run_pre_start_backup_if_enabled(db, server, context="Start")
    _ensure_operation_current(server.id, operation_epoch)

    ports_list = _ports(server)
    open_ports(server.name, ports_list, node=server.node, db=db, server_id=server.id, reason="Server gestartet")
    if server.node is None or server.node.is_local:
        iptables_accept_server(server.name, server.public_bind_ip or "", ports_list)
    _append_console_log(server.id, "[MSM] Server-Start gestartet. Bei großen Wine/Proton-Images (z.B. SCUM) oder erstem Start kann der Image-Pull + Steam-Validierung 5-15 Minuten dauern. Die Konsole zeigt Pull-Fortschritt sobald der Container läuft.\n")
    _append_console_log(server.id, "[MSM] Server-Restart: gleiche Wartezeit wie Start möglich (Image-Pull/Steam-Update).\n")
    _append_console_log(server.id, "[MSM] Starte den eigentlichen Game-Container (kann bei großen Images wie Wine/Proton oder erstem Start lange dauern wegen Pull/Setup)...\n")
    try:
        result = plugin.start(server)
    except Exception:
        close_ports(ports_list, node=server.node, name=server.name, db=db, server_id=server.id, reason="Start-Fehler Cleanup")
        if server.node is None or server.node.is_local:
            iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)
        raise
    if operation_epoch is not None and not _operation_is_current(
        server.id,
        operation_epoch,
    ):
        docker_service.remove(
            container_name_for(server.id),
            force=True,
            node=server.node,
        )
        raise LifecycleOperationCancelled(
            f"Lifecycle start for server {server.id} was cancelled"
        )
    if "error" in result:
        close_ports(ports_list, node=server.node, name=server.name, db=db, server_id=server.id, reason="Start-Fehler Cleanup")
        if server.node is None or server.node.is_local:
            iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)
        # Auth-Setup-Recovery: wenn der Container-Output auf einen interaktiven
        # Auth-Flow hinweist (z.B. Hytale OAuth-Refresh expired), starten wir
        # den Container im TTY-Modus neu und warten auf den User.
        # Blueprint-agnostisch: Erkennung laeuft ueber Log-Pattern, nicht game_type.
        if _try_start_auth_setup_recovery(db, server, plugin, result.get("error", "")):
            return  # Recovery-Thread laeuft im Hintergrund
        raise HTTPException(status_code=500, detail=result["error"])
    server.status = "running"
    server.status_message = None
    server.last_started_at = _utcnow()
    db.commit()
    from services.change_timeline_service import log_change_event
    log_change_event(db, server.id, "start", "Server gestartet.")


def _run_stop(db: Session, server: Server, plugin) -> None:
    result = plugin.stop(server)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    server.status = "stopped"
    server.status_message = None
    server.last_started_at = None
    db.commit()
    from services.change_timeline_service import log_change_event
    log_change_event(db, server.id, "stop", "Server gestoppt.")
    ports_list = _ports(server)
    close_ports(ports_list, node=server.node, name=server.name, db=db, server_id=server.id, reason="Server gestoppt")
    if server.node is None or server.node.is_local:
        iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)


def _run_kill(db: Session, server: Server) -> None:
    container = container_name_for(server.id)
    result = docker_service.remove(container, force=True, node=server.node)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail="Erzwungenes Beenden fehlgeschlagen")
    server.status = "stopped"
    server.status_message = "Erzwungen beendet"
    server.last_started_at = None
    db.commit()
    from services.change_timeline_service import log_change_event
    log_change_event(db, server.id, "stop", "Server erzwungen beendet.")
    ports_list = _ports(server)
    close_ports(ports_list, node=server.node, name=server.name, db=db, server_id=server.id, reason="Erzwungen beendet (Kill)")
    if server.node is None or server.node.is_local:
        iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)


def _run_restart(
    db: Session,
    server: Server,
    plugin,
    *,
    operation_epoch: int | None = None,
) -> None:
    from blueprints.schema import BlueprintUpdateStrategy

    _ensure_bind_ip(server)
    mod_updates: list[dict] = []
    server_update_check: dict | None = None
    strategy = _source_update_strategy(plugin)
    try:
        plugin.prepare_for_updates(server)
        if strategy != BlueprintUpdateStrategy.NONE:
            server_update_check = _check_server_file_update(server, plugin, "restart")
        mod_updates = plugin.check_for_mod_updates(server)
    except Exception as exc:
        _append_console_log(
            server.id,
            f"[MSM] Updater-Check während Restart fehlgeschlagen (nicht kritisch): {exc}\n",
        )
        logger.warning("Updater-Check beim Restart von Server %s fehlgeschlagen: %s", server.id, exc)

    update_lock_acquired = False
    try:
        if _server_file_update_needed(server_update_check) or bool(mod_updates):
            update_lock_acquired = try_acquire_install_update_lock(server.id, "restart_update", node_id=server.node_id)
            if not update_lock_acquired:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": INSTALL_UPDATE_ALREADY_RUNNING,
                        "message": f"errors.{INSTALL_UPDATE_ALREADY_RUNNING}",
                    },
                )

        ports_list = _ports(server)
        close_ports(ports_list, node=server.node, name=server.name)
        if server.node is None or server.node.is_local:
            iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)

        stop_result = plugin.stop(server)
        if "error" in stop_result:
            raise HTTPException(status_code=500, detail=stop_result["error"])

        _run_server_file_update_if_needed(server, plugin, "restart", update_check=server_update_check)

        if mod_updates:
            _append_console_log(
                server.id,
                f"[MSM] {len(mod_updates)} Workshop-Mod(s) benötigen Update/Installation. "
                "Download läuft vor dem Container-Start.\n",
            )
            mod_res = plugin.perform_workshop_mod_updates(server, only_auto_update=False)
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
    _ensure_operation_current(server.id, operation_epoch)
    _run_pre_start_backup_if_enabled(db, server, context="Restart")
    _ensure_operation_current(server.id, operation_epoch)

    ports_list = _ports(server)
    open_ports(server.name, ports_list, node=server.node, db=db, server_id=server.id, reason="Server neugestartet")
    if server.node is None or server.node.is_local:
        iptables_accept_server(server.name, server.public_bind_ip or "", ports_list)
    _append_console_log(server.id, "[MSM] Server-Start gestartet. Bei großen Wine/Proton-Images (z.B. SCUM) oder erstem Start kann der Image-Pull + Steam-Validierung 5-15 Minuten dauern. Die Konsole zeigt Pull-Fortschritt sobald der Container läuft.\n")
    _append_console_log(server.id, "[MSM] Server-Restart: gleiche Wartezeit wie Start möglich (Image-Pull/Steam-Update).\n")
    _append_console_log(server.id, "[MSM] Starte den eigentlichen Game-Container (kann bei großen Images wie Wine/Proton oder erstem Start lange dauern wegen Pull/Setup)...\n")
    try:
        start_result = plugin.start(server)
    except Exception:
        close_ports(ports_list, node=server.node, name=server.name, db=db, server_id=server.id, reason="Restart-Fehler Cleanup")
        if server.node is None or server.node.is_local:
            iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)
        raise
    if operation_epoch is not None and not _operation_is_current(
        server.id,
        operation_epoch,
    ):
        docker_service.remove(
            container_name_for(server.id),
            force=True,
            node=server.node,
        )
        raise LifecycleOperationCancelled(
            f"Lifecycle restart for server {server.id} was cancelled"
        )
    if "error" in start_result:
        close_ports(ports_list, node=server.node, name=server.name, db=db, server_id=server.id, reason="Restart-Fehler Cleanup")
        if server.node is None or server.node.is_local:
            iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)
        raise HTTPException(status_code=500, detail=start_result["error"])

    server.status = "running"
    server.status_message = None
    server.last_started_at = _utcnow()
    db.commit()
    from services.change_timeline_service import log_change_event
    log_change_event(db, server.id, "restart", "Server neugestartet.")


def _restart_server_sync(server_id: int) -> dict:
    """Synchroner Restart-Pfad für Auto-Restart (läuft in separatem Thread)."""
    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="Server nicht gefunden")

        plugin = get_plugin(server.game_type)
        if not plugin:
            raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")

        lock = get_server_lifecycle_lock(server_id)
        with lock:
            _set_status(db, server, "restarting", None)
            with guardian_recovery_suspension_lease(db, server, "lifecycle-restart"):
                try:
                    _run_restart(db, server, plugin)
                except Exception as exc:
                    db.rollback()
                    message = _safe_error_message(getattr(exc, "detail", exc))
                    server.status = "failed"
                    server.status_message = message
                    db.commit()
                    _append_console_log(server_id, f"[MSM] Lifecycle-restart fehlgeschlagen: {message}\n")
                    raise

        return {
            "message": "Restart-Befehl gesendet",
            "status": server.status,
        }
    finally:
        db.close()


async def restart_server_with_updates(db: Session, server: Server) -> dict:
    """Restartet einen Server über den zentralen Lifecycle-Pfad.

    Der Pfad ist absichtlich klein und wird von manuellem Restart und
    Auto-Restart genutzt, damit Server-Datei-Updates, Mod-Updates, Firewall und
    iptables nicht auseinanderlaufen.

    Auto-Restart nutzt asyncio.to_thread() um den Event Loop nicht zu blockieren.
    """
    if not _mark_job_active(server.id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "server_lifecycle_already_running",
                "message": "errors.server_lifecycle_already_running",
            },
        )

    try:
        return await asyncio.to_thread(_restart_server_sync, server.id)
    finally:
        _mark_job_done(server.id)


def sync_desired_state_to_agent(db: Session, server: Server) -> bool:
    """Compile, capability-check and immediately sync durable desired state."""
    node = server.node
    if not node or node.status != "online":
        return False
    try:
        from services.guardian_sync_service import compile_and_sync_desired_state

        compile_and_sync_desired_state(db, server)
        return True
    except Exception as exc:
        db.rollback()
        logger.warning(
            "Guardian desired-state sync failed for server_id=%s code=%s",
            server.id,
            getattr(exc, "code", type(exc).__name__),
        )
        return False


def switch_server_blueprint(db: Session, server: Server, new_blueprint_id: str, user_id: int | None = None) -> dict:
    """Wechselt das Spiel / den Blueprint eines gestoppten Servers.

    Sicherheits- & Backup-Invarianten:
    1. Server MUSS gestoppt sein (status == 'stopped').
    2. Neuer Blueprint MUSS existieren.
    3. MANDATORY CENTRAL BACKUP: Erstellt IMMER ein Backup ueber backup_orchestrator.create_server_backup().
       Schlaegt das Backup fehl, wird abgebrochen.
    4. Alte Spieldateien in install_dir bereinigen (Clean Wipe).
    5. Blueprint / game_type in DB aktualisieren & Ports neu zuweisen.
    6. Re-Installation des neuen Blueprints ausloesen.
    """
    if server.status != "stopped":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "server_must_be_stopped",
                "message": "Der Server muss gestoppt sein, um das Spiel oder den Blueprint zu wechseln.",
            },
        )

    from blueprints import get_registry
    registry = get_registry()
    entry = registry.get(new_blueprint_id)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "blueprint_not_found",
                "message": f"Blueprint '{new_blueprint_id}' wurde nicht gefunden.",
            },
        )

    old_game_type = server.game_type

    # 1. Ausnahmsloses Pflicht-Backup ueber zentrales Backup-System (backup_orchestrator)
    from services.backup_orchestrator import create_server_backup
    try:
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_name = f"pre-switch-{new_blueprint_id}-{timestamp_str}"
        logger.info(
            "Erstelle Pflicht-Pre-Switch-Backup fuer Server ID=%s (Alt: '%s', Neu: '%s')...",
            server.id,
            old_game_type,
            new_blueprint_id,
        )
        backup_record = create_server_backup(server.id, db, name=backup_name, timeout_seconds=600)
        if not backup_record or getattr(backup_record, "status", None) == "failed":
            raise RuntimeError("Backup-Status meldet 'failed'.")
    except Exception as exc:
        logger.error("Pre-Switch Backup fuer Server ID=%s fehlgeschlagen: %s", server.id, exc)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "pre_switch_backup_failed",
                "message": f"Pre-Switch-Backup ueber das zentrale Backup-System fehlgeschlagen: {exc}. Wechsel abgebrochen, keine Spieldateien geloescht.",
            },
        )

    # 2. Alte Spieldateien in install_dir reinigen
    try:
        target_node = server.node
        if target_node is not None and not target_node.is_local:
            from services.node_client import NodeClient
            NodeClient.from_node(target_node).files_delete_server_root(server.id)
        elif server.install_dir and os.path.exists(server.install_dir):
            for item in os.listdir(server.install_dir):
                item_path = os.path.join(server.install_dir, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path, ignore_errors=True)
                else:
                    try:
                        os.remove(item_path)
                    except Exception:
                        pass
    except Exception as exc:
        logger.warning("Datei-Reinigung vor Blueprint-Wechsel fuer Server ID=%s: %s", server.id, exc)

    # 3. Game Type / Blueprint ID im Server Model aktualisieren
    server.game_type = new_blueprint_id
    db.commit()
    db.refresh(server)

    # 4. Ports fuer den neuen Blueprint neu abstimmen
    try:
        from services.port_allocation_service import allocate_ports
        from models.server_port import ServerPort

        blueprint_obj = entry.blueprint
        port_reqs = [
            (p.name, p.protocol) for p in getattr(blueprint_obj, "ports", [])
        ] if getattr(blueprint_obj, "ports", None) else [("game", "udp"), ("query", "udp"), ("rcon", "tcp")]

        check_host = server.node.is_local if server.node else True
        allocated = allocate_ports(
            db,
            exclude_server_id=server.id,
            port_requirements=port_reqs,
            node_id=server.node_id,
            check_host=check_host,
        )

        db.query(ServerPort).filter(ServerPort.server_id == server.id).delete()
        if isinstance(allocated, list):
            for role, port_val, proto in allocated:
                db.add(ServerPort(server_id=server.id, role=role, port=port_val, protocol=proto))
        db.commit()
    except Exception as exc:
        logger.warning("Port-Synchronisierung nach Blueprint-Wechsel fuer Server ID=%s: %s", server.id, exc)

    # 5. Neu-Installation triggern
    from games import get_plugin
    plugin = get_plugin(new_blueprint_id)
    if plugin:
        _set_status(db, server, "installing", f"Wechsel zu '{new_blueprint_id}' gestartet")
        try:
            plugin.install(server)
        except Exception as exc:
            logger.error("Installation nach Blueprint-Wechsel fehlgeschlagen: %s", exc)

    # 6. Desired State an Guardian Agent syncen
    sync_desired_state_to_agent(db, server)

    return {
        "message": f"Blueprint erfolgreich zu '{new_blueprint_id}' gewechselt.",
        "server_id": server.id,
        "old_blueprint": old_game_type,
        "new_blueprint": new_blueprint_id,
        "backup_id": getattr(backup_record, "id", None),
        "status": server.status,
    }
