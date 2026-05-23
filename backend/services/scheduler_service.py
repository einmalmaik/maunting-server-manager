"""
Scheduler Service for Auto-Restart and Backups

Manages scheduled tasks using APScheduler.
Simple, reliable, and extensible.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database import SessionLocal
from games import get_plugin
from services import docker_service

logger = logging.getLogger(__name__)

# Schwelle, ab der wir eine Warnung schreiben (Server läuft weiter).
DISK_WARN_THRESHOLD_PERCENT = 80
# Schwelle, ab der wir den Container hart stoppen (Soft-Limit-Enforcement).
DISK_STOP_THRESHOLD_PERCENT = 100
# Wie oft Disk-Usage geprüft wird (Minuten).
DISK_CHECK_INTERVAL_MINUTES = 15

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


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def _restart_server_task(server_id: int) -> None:
    """Top-level job task: stops and starts a server directly via its plugin."""
    from models import AuditLog, Server

    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server or server.status != "running":
            return

        plugin = get_plugin(server.game_type)
        if not plugin:
            return

        stop_result = plugin.stop(server)
        if "error" in stop_result:
            raise RuntimeError(f"stop failed: {stop_result['error']}")

        start_result = plugin.start(server)
        if "error" in start_result:
            raise RuntimeError(f"start failed: {start_result['error']}")

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
        import logging
        logging.warning("Auto-restart failed for server %s: %s", server_id, e)
    finally:
        db.close()


async def _backup_server_task(server_id: int) -> None:
    """Top-level job task: creates a backup directly without auth checks."""
    from models import Backup, Server

    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            return

        backup_dir = f"/opt/msm/backups/{server_id}"
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{server.name}_{timestamp}.tar.gz"
        filepath = os.path.join(backup_dir, filename)

        subprocess.run(
            ["tar", "-czf", filepath, "-C", server.install_dir, "."],
            check=True, capture_output=True, timeout=300,
            env={**os.environ, "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        )
        size_mb = os.path.getsize(filepath) // (1024 * 1024)

        backup = Backup(server_id=server_id, filename=filepath, size_mb=size_mb)
        db.add(backup)
        db.commit()

        cleanup_old_backups(server_id, db)
    except Exception as e:
        import logging
        logging.warning("Auto-backup failed for server %s: %s", server_id, e)
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


def cleanup_old_backups(server_id: int, db):
    """Keep only the configured number of backups per server."""
    from models import Backup, Server

    server = db.query(Server).filter(Server.id == server_id).first()
    keep = server.backup_retention_count if server else 5

    backups = db.query(Backup).filter(
        Backup.server_id == server_id
    ).order_by(Backup.created_at.desc()).all()

    if len(backups) > keep:
        for old_backup in backups[keep:]:
            if os.path.exists(old_backup.filename):
                try:
                    os.remove(old_backup.filename)
                except Exception:
                    pass
            db.delete(old_backup)
        db.commit()


async def _disk_soft_limit_task() -> None:
    """Globaler periodischer Job: prüft Disk-Usage aller Server gegen ihr
    Soft-Limit. Bei >= 80 % schreibt eine Warnung in status_message. Bei
    >= 100 % stoppt der Job den Container und setzt status='error'.

    Dies ist KEIN hartes Quota — nur ein KISS Monitoring + Auto-Stop.
    """
    from models import AuditLog, Server

    db = SessionLocal()
    try:
        servers = db.query(Server).filter(Server.disk_limit_gb.is_not(None)).all()
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


def init_server_schedules(db):
    """Initialize schedules for all servers on startup."""
    from models import Server

    _ensure_disk_check_job()

    servers = db.query(Server).all()
    for server in servers:
        if server.auto_restart and server.restart_interval_hours:
            try:
                schedule_server_restart(
                    server.id,
                    interval_hours=server.restart_interval_hours,
                    job_id=f"restart_server_{server.id}"
                )
            except Exception as e:
                import logging
                logging.warning("Failed to schedule restart for server %s: %s", server.id, e)

        if server.auto_restart and server.restart_time_utc:
            try:
                schedule_server_restart(
                    server.id,
                    cron_time=server.restart_time_utc,
                    job_id=f"restart_cron_server_{server.id}"
                )
            except Exception as e:
                import logging
                logging.warning("Failed to schedule cron restart for server %s: %s", server.id, e)

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
