"""
Scheduler Service for Auto-Restart and Backups

Manages scheduled tasks using APScheduler.
Simple, reliable, and extensible.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timezone
from typing import Optional, Callable
import asyncio

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
    
    # Remove existing job
    remove_job(job_id)
    
    if interval_hours:
        trigger = IntervalTrigger(hours=interval_hours)
    elif cron_time:
        hour, minute = map(int, cron_time.split(':'))
        trigger = CronTrigger(hour=hour, minute=minute)
    else:
        raise ValueError("Either interval_hours or cron_time required")
    
    from routers.servers import start_server, stop_server
    from database import SessionLocal
    from models import Server
    
    async def restart_task():
        db = SessionLocal()
        try:
            server = db.query(Server).filter(Server.id == server_id).first()
            if not server or server.status != "running":
                return
            
            # Stop then start
            stop_result = stop_server(server_id, db, None)  # Needs proper user context
            await asyncio.sleep(5)
            start_result = start_server(server_id, db, None)
            
            # Log the restart
            from models import AuditLog
            audit = AuditLog(
                user_id=None,
                action="auto_restart",
                target_type="server",
                target_id=server_id,
                details=f"Auto-restart triggered for server {server.name}"
            )
            db.add(audit)
            db.commit()
            
        except Exception as e:
            print(f"Auto-restart failed for server {server_id}: {e}")
        finally:
            db.close()
    
    job = scheduler.add_job(
        func=restart_task,
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
    
    from routers.backups import create_backup
    from database import SessionLocal
    from models import Server
    
    async def backup_task():
        db = SessionLocal()
        try:
            server = db.query(Server).filter(Server.id == server_id).first()
            if not server:
                return
            
            # Create backup
            create_backup(server_id, db, None)  # Needs proper user context
            
            # Cleanup old backups (keep last 10)
            cleanup_old_backups(server_id, db)
            
        except Exception as e:
            print(f"Auto-backup failed for server {server_id}: {e}")
        finally:
            db.close()
    
    job = scheduler.add_job(
        func=backup_task,
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
    """Keep only the last 10 backups for a server."""
    from models import Backup
    
    backups = db.query(Backup).filter(
        Backup.server_id == server_id
    ).order_by(Backup.created_at.desc()).all()
    
    if len(backups) > 10:
        for old_backup in backups[10:]:
            # Delete file if exists
            import os
            if old_backup.path and os.path.exists(old_backup.path):
                try:
                    os.remove(old_backup.path)
                except Exception:
                    pass
            db.delete(old_backup)
        
        db.commit()


def init_server_schedules(db):
    """Initialize schedules for all servers on startup."""
    from models import Server
    
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
                print(f"Failed to schedule restart for server {server.id}: {e}")
        
        if server.auto_restart and server.restart_time_utc:
            try:
                schedule_server_restart(
                    server.id,
                    cron_time=server.restart_time_utc,
                    job_id=f"restart_cron_server_{server.id}"
                )
            except Exception as e:
                print(f"Failed to schedule cron restart for server {server.id}: {e}")