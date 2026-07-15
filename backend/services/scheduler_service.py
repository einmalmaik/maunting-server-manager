"""
Scheduler Service for Auto-Restart and Backups

Manages scheduled tasks using APScheduler.
Simple, reliable, and extensible.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database import SessionLocal
from games import get_plugin, _append_console_log
from services import docker_service
from services.server_lifecycle_service import restart_server_with_updates, get_server_lifecycle_lock, acquire_lock_async

logger = logging.getLogger(__name__)

# Schwelle, ab der wir eine Warnung schreiben (Server läuft weiter).
DISK_WARN_THRESHOLD_PERCENT = 80
# Schwelle, ab der wir den Container hart stoppen (Soft-Limit-Enforcement).
DISK_STOP_THRESHOLD_PERCENT = 100
# Wie oft Disk-Usage geprüft wird (Minuten).
DISK_CHECK_INTERVAL_MINUTES = 15

# Intervall für passive Hintergrund-Checks auf Mod- und Server-Datei-Updates (Stunden).
# Rate-Limit-sicher (Steam-API, Workshop), KISS: nicht zu häufig, nur Erkennung.
UPDATE_CHECK_INTERVAL_HOURS = 6

# In-Memory Dedup-Sets (Prozess-Lebensdauer) für "nur einmal pro Fund" bei Update-Emails.
# Schlüssel nutzen *bestehende Metadaten* aus check_*-Rückgaben (remote_updated, current_updated,
# local_mtime, reason) → bei Apply + später neuem Update ändert sich der Key automatisch.
# Innerhalb eines Laufs: keine 6h-Wiederhol-Mails (Anti-Spam).
# Bei MSM-Prozess-Neustart: Reset (einmaliger Reminder akzeptabel, kein Dauer-Spam).
# Keine DB-Felder, keine Persistenz, KISS + defensiv (AGENTS.md).
_notified_server_update_keys: set[str] = set()
_notified_mod_update_keys: set[str] = set()

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Get or create scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=timezone.utc)
    return _scheduler


# Phase 5: Node heartbeat interval (seconds)
NODE_HEARTBEAT_INTERVAL_SECONDS = 60


def start_scheduler():
    """Start the scheduler if not running."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
    # Globalen passiven Background-Update-Check-Job sicherstellen (auch hier,
    # damit der Job nach Scheduler-Restart/Neustart aktiv ist; KISS, idempotent).
    _ensure_background_update_check_job()
    _ensure_node_heartbeat_job()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_next_restart_run_time(server_id: int) -> datetime | None:
    """Liefert den naechsten APScheduler-Run fuer einen Server-Restart.

    Defense-in-Depth: APScheduler's get_jobs()-Run-Times sind durch den expliziten
    UTC-Default in `get_scheduler()`/`schedule_server_restart()` bereits tz-aware in
    UTC. Der `tzinfo is None`-Branch behandelt legacy/mock-Pfade (z. B. zukuenftige
    Trigger ohne TZ-Override oder externe Schedulers), damit niemals ein Crash oder
    Drift durch naive Datetimes entsteht. Nicht entfernen, auch wenn der Branch
    aktuell ungenutzt wirkt.
    """
    scheduler = get_scheduler()
    next_run = None
    for job in scheduler.get_jobs():
        if job.id == f"restart_server_{server_id}" or job.id.startswith(f"restart_cron_server_{server_id}_"):
            run_time = getattr(job, "next_run_time", None)
            if run_time is None:
                continue
            # Defense-in-Depth: naive Datetimes als UTC interpretieren (siehe Docstring).
            if run_time.tzinfo is None:
                run_time = run_time.replace(tzinfo=timezone.utc)
            run_time = run_time.astimezone(timezone.utc)
            if next_run is None or run_time < next_run:
                next_run = run_time
    return next_run


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def _restart_server_task(server_id: int) -> None:
    """Top-level job task: restartet über denselben Pfad wie der manuelle Button."""
    from models import AuditLog, Server

    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            return
        # Guard gegen gleichzeitige Auto-Restarts und falsche Trigger bei nicht-laufenden Servern
        # (verhindert Fehler bei mehreren gleichzeitig aktiven Servern und falsche 4-Uhr-Neustarts
        # durch veraltete Jobs oder Scheduler-Drift).
        if server.status != "running":
            logger.info("Auto-restart für Server %s übersprungen (Status=%s)", server_id, server.status)
            return
        from services.server_lifecycle_service import is_lifecycle_job_active
        if is_lifecycle_job_active(server_id):
            logger.info("Auto-restart für Server %s übersprungen (bereits ein Lifecycle-Job aktiv)", server_id)
            return

        server.last_auto_restart_attempt_at = _utcnow()
        server.last_auto_restart_status = "running"
        db.commit()

        # Kein früher Status-Check mehr außerhalb des Locks (TOCTOU-Race mit manual stop/start).
        # Der zentrale restart_server_with_updates (mit einheitlichem Lifecycle-Lock) ist
        # autoritativ und führt stop+firewall immer sicher aus (idempotent bei nicht-laufend).
        await restart_server_with_updates(db, server)
        server.last_auto_restart_completed_at = _utcnow()
        server.last_auto_restart_status = "success"
        # Reset interval timer from *this successful completion* (fixes "set 8h but ran at 4am" and drift with multiple servers).
        # For interval-based auto-restart we re-schedule so the next run is now + interval_hours.
        try:
            if getattr(server, "auto_restart", False) and getattr(server, "restart_interval_hours", None):
                from services.scheduler_service import schedule_server_restart
                schedule_server_restart(
                    server.id,
                    interval_hours=server.restart_interval_hours,
                    job_id=f"restart_server_{server.id}",
                )
        except Exception as _e:
            logging.warning("Could not reschedule auto-restart interval after success: %s", _e)

        audit = AuditLog(
            user_id=None,
            action="auto_restart",
            target_type="server",
            target_id=server_id,
            details=f"Auto-restart triggered for server {server.name}",
        )
        db.add(audit)
        db.commit()
    except Exception as e:
        try:
            server = db.query(Server).filter(Server.id == server_id).first()
            if server:
                server.last_auto_restart_status = "failed"
                db.commit()
        except Exception:
            db.rollback()
        import logging
        logging.warning("Auto-restart failed for server %s: %s", server_id, e)
    finally:
        db.close()


async def _backup_server_task(server_id: int) -> None:
    """Top-level job task: delegates to backup_orchestrator (lokal + S3 Best-Effort).

    Verwendet bewusst den Orchestrator (nicht mehr direkt backup_service.run_backup),
    damit geplante Backups automatisch verschluesselt in S3 hochgeladen werden, sobald
    S3 konfiguriert und ein Backup-Passwort gesetzt ist. S3-Fehler blockieren weder
    das lokale Backup noch den Scheduler (Best-Effort, Warning-Log ohne Secrets).
    """
    from models import Server  # only for existence check (service does the rest)

    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            return

        from services.backup_orchestrator import create_server_backup
        # Scheduler path: kuerzerer Timeout (300s), damit der Scheduler-Loop nicht zu lange blockiert.
        # Orchestrator uebernimmt tar + DB-Record + Retention-Cleanup + S3-Upload (Best-Effort).
        create_server_backup(server_id, db, timeout_seconds=300)
    except Exception:
        import logging
        logging.warning("Auto-backup failed for server %s (details redacted for security)", server_id)
    finally:
        db.close()


def schedule_server_restart(
    server_id: int,
    interval_hours: Optional[int] = None,
    cron_time: Optional[str] = None,  # "HH:MM" format
    job_id: Optional[str] = None
) -> str:
    """Schedule automatic server restart.

    Args:
        server_id: Server ID
        interval_hours: Restart every N hours (interval mode)
        cron_time: Restart at specific time "HH:MM" (cron mode)
        job_id: Optional custom job ID

    Returns:
        Job ID
    """
    scheduler = get_scheduler()
    job_id = job_id or f"restart_server_{server_id}"

    remove_job(job_id)

    if interval_hours:
        # WICHTIG: start_date in Zukunft setzen, damit IntervalTrigger NICHT sofort
        # beim Hinzufügen ausgeführt wird (verhindert unerwartete Neustarts direkt
        # nach Config-Änderung oder Server-Start; löst Timing-Probleme bei
        # 8h-Intervall etc. und Race bei mehreren Servern).
        start_date = _utcnow() + timedelta(hours=interval_hours)
        trigger = IntervalTrigger(hours=interval_hours, start_date=start_date)
    elif cron_time:
        hour, minute = map(int, cron_time.split(":"))
        trigger = CronTrigger(hour=hour, minute=minute, timezone=timezone.utc)
    else:
        raise ValueError("Either interval_hours or cron_time required")

    job = scheduler.add_job(
        func=_restart_server_task,
        args=[server_id],
        trigger=trigger,
        id=job_id,
        name=f"Auto-Restart Server {server_id}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,  # Skip missed runs, only run once when back
    )

    return job.id


def remove_restart_jobs(server_id: int) -> None:
    """Entfernt alle Auto-Restart-Jobs eines Servers."""
    scheduler = get_scheduler()
    for job in list(scheduler.get_jobs()):
        if job.id == f"restart_server_{server_id}" or job.id.startswith(f"restart_cron_server_{server_id}_"):
            remove_job(job.id)


# Job-ID fuer den geplanten Panel-Backup-Job (eindeutig, global).
PANEL_BACKUP_JOB_ID = "panel_backup_job"


def sync_server_restart_schedule(server) -> None:
    """Synchronisiert DB-Settings eines Servers in APScheduler.

    DB bleibt die Quelle der Wahrheit. 
    Intervall hat Vorrang vor festen Zeiten (siehe _normalize_server_restart_mode im Router).
    Die Normalisierung im Router stellt sicher, dass in der DB nie beide Modi gleichzeitig
    gesetzt sind (sowohl als auch ist jetzt ausgeschlossen).
    """
    remove_restart_jobs(server.id)
    if not getattr(server, "auto_restart", False):
        return

    interval_hours = getattr(server, "restart_interval_hours", None)
    if interval_hours:
        schedule_server_restart(
            server.id,
            interval_hours=interval_hours,
            job_id=f"restart_server_{server.id}",
        )
        return

    times_raw = getattr(server, "restart_times_utc", None) or getattr(server, "restart_time_utc", None) or ""
    for time_value in [part.strip() for part in times_raw.split(",") if part.strip()]:
        safe_id = time_value.replace(":", "")
        schedule_server_restart(
            server.id,
            cron_time=time_value,
            job_id=f"restart_cron_server_{server.id}_{safe_id}",
        )


def schedule_backup(
    server_id: int,
    interval_hours: int = 24,
    job_id: Optional[str] = None
) -> str:
    """Schedule automatic backup.

    Args:
        server_id: Server ID
        interval_hours: Backup every N hours
        job_id: Optional custom job ID

    Returns:
        Job ID
    """
    scheduler = get_scheduler()
    job_id = job_id or f"backup_server_{server_id}"

    remove_job(job_id)

    job = scheduler.add_job(
        func=_backup_server_task,
        args=[server_id],
        trigger=IntervalTrigger(hours=interval_hours),
        id=job_id,
        name=f"Auto-Backup Server {server_id}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    return job.id


async def _panel_backup_task() -> None:
    """Top-level job task: geplantes Panel-Backup (DB-Dump + Configs + S3 + Retention).

    Ruft panel_backup_service.create_panel_backup auf. Fehler werden abgefangen
    (Best-Effort), damit der Scheduler nicht crashed (VAL-PANEL-SCHED-005).
    S3-Upload und Retention passieren innerhalb create_panel_backup.
    """
    db = SessionLocal()
    try:
        from services.panel_backup_service import create_panel_backup
        create_panel_backup(db)
    except Exception:
        # Generische Warning — keine Secrets, kein Crash (VAL-PANEL-SCHED-005).
        logger.warning(
            "Scheduled panel backup failed (details redacted for security)"
        )
    finally:
        db.close()


def sync_panel_backup_schedule() -> None:
    """Synchronisiert panel_settings (backup.panel_*) in APScheduler.

    - enabled=False -> Job entfernen (VAL-PANEL-SCHED-002)
    - enabled=True -> IntervalTrigger mit interval_hours (VAL-PANEL-SCHED-001)
    - Aenderungen werden live rescheduled (VAL-PANEL-SCHED-004)

    Wird aufgerufen: beim Panel-Start (init_server_schedules) und nach
    PATCH /api/panel-backups/settings.
    """
    from services.panel_backup_service import get_panel_backup_settings

    settings = get_panel_backup_settings()
    if not settings["enabled"]:
        remove_job(PANEL_BACKUP_JOB_ID)
        return

    interval = settings["interval_hours"]
    if interval <= 0:
        remove_job(PANEL_BACKUP_JOB_ID)
        return

    scheduler = get_scheduler()
    # start_date in Zukunft, damit IntervalTrigger nicht sofort feuert
    # (analog schedule_server_restart — verhindert unerwartetes Backup direkt
    # nach Config-Aenderung oder Panel-Start).
    start_date = _utcnow() + timedelta(hours=interval)
    scheduler.add_job(
        func=_panel_backup_task,
        trigger=IntervalTrigger(hours=interval, start_date=start_date),
        id=PANEL_BACKUP_JOB_ID,
        name="Panel Auto-Backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def remove_job(job_id: str) -> bool:
    """Remove a scheduled job."""
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(job_id)
        return True
    except Exception:
        return False


def get_jobs(server_id: Optional[int] = None) -> list:
    """Get scheduled jobs, optionally filtered by server."""
    scheduler = get_scheduler()
    jobs = scheduler.get_jobs()

    if server_id:
        prefix = f"_server_{server_id}"
        jobs = [j for j in jobs if prefix in j.id]

    return [
        {
            "id": j.id,
            "name": j.name,
            "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
            "trigger": str(j.trigger),
        }
        for j in jobs
    ]


# cleanup_old_backups wurde entfernt (war Duplikat). Zentrale Implementierung
# jetzt in services/backup_service.py (wird von _backup_server_task und Router genutzt).

# ── Disk-Soft-Limit: zentrale Evaluierungs-Funktion (DRY) ──────────────
# Wird sowohl vom Scheduler (periodisch, _disk_soft_limit_task) als auch vom
# PATCH-Handler (sofort nach disk_limit_gb-Aenderung) genutzt.
# Keine Datei- oder DB-Zeilenloeschung. Kein Docker-Hard-Quota.
# Startet niemals einen gestoppten Server (VAL-DISK-006).

DISK_WARN_MSG_PREFIX = "Warnung: Disk-Verbrauch bei"
DISK_STOP_MSG_PREFIX = "Disk-Soft-Limit erreicht"


def _is_disk_status_message(msg: str | None) -> bool:
    """True wenn status_message ein Disk-Soft-Limit-Status ist (Warnung oder Stop)."""
    if not msg:
        return False
    return msg.startswith(DISK_WARN_MSG_PREFIX) or msg.startswith(DISK_STOP_MSG_PREFIX)


def _clear_stale_disk_state(server) -> None:
    """Loescht verstaendliche Disk-Warn-/Fehler-Zustaende (VAL-DISK-006).

    Setzt status von 'error' auf 'stopped' zurueck, wenn der Fehler durch
    Disk-Soft-Limit verursacht wurde. Startet niemals einen Server.
    """
    if not _is_disk_status_message(server.status_message):
        return
    server.status_message = None
    if server.status == "error":
        server.status = "stopped"


def evaluate_disk_soft_limit(db, server) -> dict:
    """Misst die aktuelle Disk-Nutzung und wendet die bestehende Soft-Limit-
    Warn-/Stop-Policy fuer einen einzelnen Server an.

    Wird sowohl vom Scheduler (periodisch) als auch vom PATCH-Handler
    (sofort nach Aenderung) genutzt (DRY, VAL-DISK-001).

    - Keine Datei- oder DB-Zeilenloeschung (VAL-DISK-002, VAL-DISK-003).
    - Kein Docker-Hard-Quota (VAL-DISK-004, VAL-DOCKER-010).
    - Stop erfolgt ueber plugin.stop (Lifecycle/Plugin-Boundary, VAL-DISK-007).
    - Startet niemals einen gestoppten Server (VAL-DISK-006).
    - Bei Mess- oder Enforcement-Fehler: ``{"ok": False}`` damit Aufrufer
      rollbacken kann (VAL-DISK-005, kein Drift).

    Returns:
        ``{"ok": True, "action": "none"|"warning"|"stop"|"cleared"}``
        ``{"ok": False, "error": "..."}`` bei Mess- oder Enforcement-Fehler
    """
    from models import AuditLog

    usage_mb = docker_service.disk_usage_mb(
        server.install_dir,
        node=getattr(server, "node", None),
        server_id=server.id,
    )
    if usage_mb is None:
        return {"ok": False, "error": "Disk-Nutzung konnte nicht ermittelt werden"}

    server.disk_usage_mb = usage_mb
    limit_mb = (server.disk_limit_gb or 0) * 1024

    if limit_mb <= 0:
        # Kein Limit gesetzt -> ggf. verstaendliche Disk-Warnung loeschen
        # (VAL-DISK-006). Status NICHT auf 'running' setzen (kein Auto-Start).
        _clear_stale_disk_state(server)
        return {"ok": True, "action": "cleared"}

    percent = (usage_mb * 100) // limit_mb

    if percent >= DISK_STOP_THRESHOLD_PERCENT:
        plugin = get_plugin(server.game_type)
        if plugin and server.status == "running":
            try:
                plugin.stop(server)
            except Exception:
                logger.warning("disk-limit stop failed for %s", server.id)
                return {"ok": False, "error": "Disk-Limit-Stop fehlgeschlagen"}
        server.status = "error"
        server.status_message = (
            f"Disk-Soft-Limit erreicht ({usage_mb} MB / {limit_mb} MB). Container gestoppt."
        )
        db.add(AuditLog(
            user_id=None,
            action="disk_limit_stop",
            target_type="server",
            target_id=server.id,
            details=f"Disk usage {usage_mb} MB hit limit {limit_mb} MB",
        ))
        return {"ok": True, "action": "stop"}
    elif percent >= DISK_WARN_THRESHOLD_PERCENT:
        server.status_message = (
            f"Warnung: Disk-Verbrauch bei {percent} % von {limit_mb} MB."
        )
        return {"ok": True, "action": "warning"}
    else:
        # Usage innerhalb des Limits -> verstaendliche Disk-Warnung loeschen
        # (VAL-DISK-006). Kein Auto-Start.
        _clear_stale_disk_state(server)
        return {"ok": True, "action": "none"}


async def _disk_soft_limit_task() -> None:
    """Globaler periodischer Job: aktualisiert `disk_usage_mb` für ALLE Server,
    und prüft zusätzlich das Soft-Limit (falls gesetzt).

    - Usage-Tracking: immer (auch ohne Limit) — Frontend zeigt sonst keinen
      Belegt-Wert für Server ohne Limit.
    - Warnung bei >= 80 % belegt: `status_message` enthält Warntext.
    - Auto-Stop bei >= 100 % belegt: Container gestoppt, `status='error'`.
    """
    from models import Server

    db = SessionLocal()
    try:
        servers = db.query(Server).all()
        for server in servers:
            result = evaluate_disk_soft_limit(db, server)
            if not result.get("ok"):
                continue
        db.commit()
    except Exception as e:
        logger.warning("disk soft-limit task crashed: %s", e)
    finally:
        db.close()


def _ensure_disk_check_job() -> None:
    scheduler = get_scheduler()
    job_id = "global_disk_soft_limit_check"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    scheduler.add_job(
        func=_disk_soft_limit_task,
        trigger=IntervalTrigger(minutes=DISK_CHECK_INTERVAL_MINUTES),
        id=job_id,
        name="Disk Soft-Limit Check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


async def _background_update_check_task() -> None:
    """Globaler periodischer Scheduler-Job für passive Hintergrund-Checks.

    Für ALLE Server:
    - Ruft über das Plugin `check_for_mod_updates` und `check_for_server_file_update` auf.
    - Bei Fund (Mod-Updates oder Server-Datei-Update verfügbar):
      * Schreibt Eintrag in die MSM-Console-Log (sichtbar im UI-Console).
      * Setzt `server.status_message` mit Hinweis (passiv, rein informativ).
      * Markiert betroffene Mods als pending, damit der Fallback-Button sichtbar ist.
    - Workshop-Mods mit auto_update=True werden autonom geladen, wenn der Server
      nicht läuft; bei laufendem Server bleibt der Pending-Status bis zum nächsten
      Start/Restart oder manuellen Update.
    - Server-Datei-Updates bleiben passiv: KEIN Container-Stop/Start.
    - Rate-Limit-sicher: alle 6 Stunden (UPDATE_CHECK_INTERVAL_HOURS).
    - Email-Benachrichtigungen (verdrahtet):
      * Nur wenn EmailService.is_configured() True.
      * Nur an User mit user.email_notifications=True (Glocke im Topbar).
      * Nur an User mit server.view-Recht auf dem betroffenen Server
        (Owner via is_owner, Rolle oder explizite ServerPermission; has_server_permission).
      * Nur 1x pro Fund: Dedup-Key aus *bestehenden Metadaten* der Check-Rückgabe
        (remote_updated, current_updated, local_mtime, reason, workshop_id).
        In-Memory (Prozess-Laufzeit) — bei Apply + neuem remote Update: neuer Key,
        Mail geht raus. Kein 6h-Spam bei pending Updates.
    - Keine AuditLogs (minimaler Scope, KISS).
    - Defensiv: pro-Server try/except + pro-Notify try/except, frische DB-Session,
      Snapshot der Kandidaten am Task-Start, keine Secrets, keine neuen DB-Writes.

    Entspricht exakt _disk_soft_limit_task-Pattern (AGENTS.md + Architektur).
    Deutsche Kommentare, keine neue Komplexität, keine destruktiven Aktionen.
    Verdrahtung referenziert send_server_update_available_notification und
    send_mod_update_available_notification aus email_service.py (Aufrufbedingung
    exakt wie in deren Docstring beschrieben).
    """
    from models import Server, User
    from services.email_service import EmailService
    from services.permission_service import has_server_permission

    db = SessionLocal()
    try:
        # Email-Kandidaten einmalig am Task-Start snapshotten (KISS).
        # Nur wenn konfiguriert: sonst kein Query, keine Mail-Logik.
        email_configured = False
        candidate_users: list[User] = []
        try:
            email_configured = EmailService.is_configured()
            if email_configured:
                candidate_users = (
                    db.query(User)
                    .filter(
                        User.is_active == True,
                        User.email_notifications == True,
                        User.email_encrypted.isnot(None),
                    )
                    .all()
                )
        except Exception:  # pragma: no cover - defensiv
            email_configured = False
            candidate_users = []

        servers = db.query(Server).all()
        for server in servers:
            try:
                plugin = get_plugin(server.game_type)
                if not plugin:
                    continue

                # 1. Workshop-Mod-Updates werden autonom vorbereitet.
                mod_updates = plugin.check_for_mod_updates(server)
                if mod_updates:
                    from services.mod_install_status_service import mark_mod_pending

                    for mod_update in mod_updates:
                        mark_mod_pending(
                            server.id,
                            mod_update.get("workshop_id", ""),
                            mod_update.get("action", "update"),
                        )
                    count = len(mod_updates)
                    _append_console_log(
                        server.id,
                        f"[MSM] Background-Check: {count} Workshop-Mod(s) benötigen Update/Installation "
                        f"für Server '{server.name}'. Pending-Status wurde gesetzt; "
                        "auto_update-Mods werden bei gestopptem Server oder beim nächsten Start/Restart geladen.\n"
                    )
                    logger.info(
                        "Background-Check: %d Mod-Update(s) für Server %s ('%s') gefunden.",
                        count, server.id, server.name
                    )
                    async with acquire_lock_async(get_server_lifecycle_lock(server.id)):
                        db.refresh(server)
                        if server.status not in ("running", "starting", "stopping"):
                            from services.install_update_lock_service import (
                                try_acquire_install_update_lock,
                                release_install_update_lock,
                            )
                            lock_acquired = try_acquire_install_update_lock(
                                server.id, "scheduler_mod_update"
                            )
                            if not lock_acquired:
                                _append_console_log(
                                    server.id,
                                    "[MSM] Autonomes Workshop-Mod-Update übersprungen: "
                                    "laufende Lifecycle-Operation.\n",
                                )
                            else:
                                try:
                                    mod_res = await asyncio.to_thread(
                                        plugin.perform_workshop_mod_updates,
                                        server,
                                        only_auto_update=True,
                                    )
                                    if not mod_res.get("ok", False):
                                        _append_console_log(
                                            server.id,
                                            f"[MSM] Autonomes Workshop-Mod-Update fehlgeschlagen: "
                                            f"{mod_res.get('error') or mod_res}\n",
                                        )
                                finally:
                                    release_install_update_lock(server.id)

                # 2. Server-Datei-Update (Game-Binaries, passiv)
                server_update = plugin.check_for_server_file_update(server)
                if server_update.get("action") == "update":
                    reason = server_update.get("reason", "unbekannt")
                    _append_console_log(
                        server.id,
                        f"[MSM] Background-Check: Server-Datei-Update erkannt für '{server.name}' "
                        f"({reason}) — passiv, kein Auto-Update (auch nicht bei laufendem Server).\n"
                    )
                    server.status_message = (
                        f"Hintergrund-Check: Server-Datei-Update verfügbar ({reason}). "
                        "Update wird vor manuellem Neustart empfohlen."
                    )
                    logger.info(
                        "Background-Check: Server-Datei-Update für Server %s ('%s') gefunden (%s, passiv).",
                        server.id, server.name, reason
                    )

                    # E-Mail-Benachrichtigung (nur 1x pro neuem Fund via Metadaten-Dedup)
                    if email_configured and candidate_users:
                        remote = server_update.get("remote_updated") or ""
                        local = server_update.get("local_mtime") or ""
                        dedup_key = f"server:{server.id}:{reason}:{remote}:{local}"
                        if dedup_key not in _notified_server_update_keys:
                            _notified_server_update_keys.add(dedup_key)
                            for u in candidate_users:
                                try:
                                    if not has_server_permission(db, u, server.id, "server.view"):
                                        continue
                                    if EmailService.is_configured() and getattr(u, "email_notifications", False):
                                        await EmailService.send_server_update_available_notification(
                                            u.email, u.username, server.name
                                        )
                                except Exception as notify_err:  # pragma: no cover - defensiv
                                    logger.warning(
                                        "Server-Update-Email fehlgeschlagen für User %s, Server %s: %s",
                                        getattr(u, "id", "?"), server.id, notify_err
                                    )

                db.commit()
            except Exception as e:  # pragma: no cover - defensiv pro Server
                db.rollback()
                logger.warning(
                    "Background-Update-Check fehlgeschlagen für Server %s: %s", server.id, e
                )
    except Exception as e:
        db.rollback()
        logger.warning("background update check task crashed: %s", e)
    finally:
        db.close()


def _ensure_background_update_check_job() -> None:
    """Stellt den globalen passiven Update-Check-Job sicher (idempotent, wie _ensure_disk)."""
    scheduler = get_scheduler()
    job_id = "global_background_update_checks"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    scheduler.add_job(
        func=_background_update_check_task,
        trigger=IntervalTrigger(hours=UPDATE_CHECK_INTERVAL_HOURS),
        id=job_id,
        name="Passive Background Update Checks (Mods + Server Files)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def _node_heartbeat_task() -> None:
    """Probe every registered node /health; set online/offline + last_heartbeat.

    Never logs tokens. Fingerprint pinning enforced inside NodeClient.
    """
    from models import Node
    from services.node_client import NodeClient, NodeClientError

    db = SessionLocal()
    try:
        nodes = db.query(Node).order_by(Node.id.asc()).all()
        for node in nodes:
            try:
                client = NodeClient.from_node(node)
                client.health()
                node.status = "online"
                node.last_heartbeat = _utcnow()
            except NodeClientError:
                node.status = "offline"
            except Exception:
                logger.warning("node heartbeat unexpected error node_id=%s", getattr(node, "id", "?"))
                node.status = "offline"
        db.commit()
    except Exception as exc:
        logger.warning("node heartbeat task failed: %s", exc)
        db.rollback()
    finally:
        db.close()


def _ensure_node_heartbeat_job() -> None:
    """Register periodic node health probes (Phase 5)."""
    scheduler = get_scheduler()
    job_id = "global_node_heartbeat"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    scheduler.add_job(
        func=_node_heartbeat_task,
        trigger=IntervalTrigger(seconds=NODE_HEARTBEAT_INTERVAL_SECONDS),
        id=job_id,
        name="Node Agent Heartbeat",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def init_server_schedules(db):
    """Initialize schedules for all servers on startup."""
    from models import Server

    _ensure_disk_check_job()
    # Passiver Hintergrund-Update-Check-Job (global, für alle Server):
    # Ruft check_for_mod_updates + check_for_server_file_update passiv auf.
    # Integriert hier + in start_scheduler (genau nach Plan).
    _ensure_background_update_check_job()
    _ensure_node_heartbeat_job()

    servers = db.query(Server).all()
    for server in servers:
        if server.auto_restart:
            try:
                sync_server_restart_schedule(server)
            except Exception as e:
                import logging
                logging.warning("Failed to schedule restart for server %s: %s", server.id, e)

        if server.backup_interval_hours and server.backup_interval_hours > 0:
            try:
                schedule_backup(
                    server.id,
                    interval_hours=server.backup_interval_hours,
                    job_id=f"backup_server_{server.id}"
                )
            except Exception as e:
                import logging
                logging.warning("Failed to schedule backup for server %s: %s", server.id, e)

    # Panel-Backup-Scheduler aus panel_settings wiederherstellen
    # (VAL-CROSS-008: Panel-Neustart -> Scheduler nimmt Jobs wieder auf).
    try:
        sync_panel_backup_schedule()
    except Exception as e:
        import logging
        logging.warning("Failed to sync panel backup schedule: %s", e)

    # Hinweis zum restart_time_utc / restart_times_utc Pattern (wie in models/server.py):
    # Backup nutzt aktuell ausschließlich Interval (backup_interval_hours).
    # Die Struktur ist bewusst so vorbereitet, dass später ein backup_times_utc
    # (Cron-ähnlich, analog zu restart_times_utc) ergänzt werden kann,
    # ohne die bestehende Interval-Logik oder die init_server_schedules zu zerstören.
    # Zeitzonen-Handling: Beide Systeme speichern/interpretieren Zeiten als
    # UTC-intendierte HH:MM-Strings. Globales time_format (PanelSettings) ist
    # reine UI-Darstellung und wird nicht für Scheduling-Entscheidungen benötigt.
    # (Konsistenz mit Restart-System gewährleistet.)
    # Backup verwendet aktuell NUR backup_interval_hours (IntervalTrigger).
    # Ein zukünftiges backup_times_utc (analog) würde kleine Erweiterungen in schedule_backup +
    # init erfordern (ähnlich der Restart-Logik), ist aber strukturell vorbereitet.
