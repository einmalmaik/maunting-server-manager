"""
Scheduler Service for Auto-Restart and Backups

Manages scheduled tasks using APScheduler.
Simple, reliable, and extensible.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database import SessionLocal
from games import get_plugin, _append_console_log
from services import docker_service
from services.server_lifecycle_service import restart_server_with_updates, get_server_lifecycle_lock

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
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_scheduler():
    """Start the scheduler if not running."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
    # Globalen passiven Background-Update-Check-Job sicherstellen (auch hier,
    # damit der Job nach Scheduler-Restart/Neustart aktiv ist; KISS, idempotent).
    _ensure_background_update_check_job()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_next_restart_run_time(server_id: int) -> datetime | None:
    """Liefert den naechsten APScheduler-Run fuer einen Server-Restart."""
    scheduler = get_scheduler()
    next_run = None
    for job in scheduler.get_jobs():
        if job.id == f"restart_server_{server_id}" or job.id.startswith(f"restart_cron_server_{server_id}_"):
            run_time = getattr(job, "next_run_time", None)
            if run_time is None:
                continue
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
        server.last_auto_restart_attempt_at = _utcnow()
        server.last_auto_restart_status = "running"
        db.commit()

        # Kein früher Status-Check mehr außerhalb des Locks (TOCTOU-Race mit manual stop/start).
        # Der zentrale restart_server_with_updates (mit einheitlichem Lifecycle-Lock) ist
        # autoritativ und führt stop+firewall immer sicher aus (idempotent bei nicht-laufend).
        await restart_server_with_updates(db, server)
        server.last_auto_restart_completed_at = _utcnow()
        server.last_auto_restart_status = "success"

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
    """Top-level job task: delegates to central backup_service (no duplicated tar logic)."""
    from models import Server  # only for existence check (service does the rest)

    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            return

        from services.backup_service import run_backup
        # Scheduler path: kürzerer Timeout (300s), damit der Scheduler-Loop nicht zu lange blockiert.
        # Service übernimmt tar + DB-Record + Retention-Cleanup.
        run_backup(server_id, db, timeout_seconds=300)
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
        trigger = IntervalTrigger(hours=interval_hours)
    elif cron_time:
        hour, minute = map(int, cron_time.split(":"))
        trigger = CronTrigger(hour=hour, minute=minute)
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


def sync_server_restart_schedule(server) -> None:
    """Synchronisiert DB-Settings eines Servers in APScheduler.

    DB bleibt die Quelle der Wahrheit. Intervall hat Vorrang vor festen Zeiten,
    weil die UI immer genau einen Modus speichert.
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

async def _disk_soft_limit_task() -> None:
    """Globaler periodischer Job: aktualisiert `disk_usage_mb` für ALLE Server,
    und prüft zusätzlich das Soft-Limit (falls gesetzt).

    - Usage-Tracking: immer (auch ohne Limit) — Frontend zeigt sonst keinen
      Belegt-Wert für Server ohne Limit.
    - Warnung bei >= 80 % belegt: `status_message` enthält Warntext.
    - Auto-Stop bei >= 100 % belegt: Container gestoppt, `status='error'`.
    """
    from models import AuditLog, Server

    db = SessionLocal()
    try:
        servers = db.query(Server).all()
        for server in servers:
            usage_mb = docker_service.disk_usage_mb(server.install_dir)
            if usage_mb is None:
                continue
            server.disk_usage_mb = usage_mb
            limit_mb = (server.disk_limit_gb or 0) * 1024
            if limit_mb <= 0:
                continue
            percent = (usage_mb * 100) // limit_mb

            if percent >= DISK_STOP_THRESHOLD_PERCENT:
                plugin = get_plugin(server.game_type)
                if plugin and server.status == "running":
                    try:
                        plugin.stop(server)
                    except Exception as e:
                        logger.warning("disk-limit stop failed for %s: %s", server.id, e)
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
            elif percent >= DISK_WARN_THRESHOLD_PERCENT:
                server.status_message = (
                    f"Warnung: Disk-Verbrauch bei {percent} % von {limit_mb} MB."
                )
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
                        User.email.isnot(None),
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
                    async with get_server_lifecycle_lock(server.id):
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


def init_server_schedules(db):
    """Initialize schedules for all servers on startup."""
    from models import Server

    _ensure_disk_check_job()
    # Passiver Hintergrund-Update-Check-Job (global, für alle Server):
    # Ruft check_for_mod_updates + check_for_server_file_update passiv auf.
    # Integriert hier + in start_scheduler (genau nach Plan).
    _ensure_background_update_check_job()

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
