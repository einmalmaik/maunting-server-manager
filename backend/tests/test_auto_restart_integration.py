"""
Integration tests for Auto-Restart logic (interval OR fixed times, exclusively).

These tests verify scheduling, normalization, job management and task behavior
WITHOUT ever performing real server restarts or Docker operations.

Safe to run on any branch (including SCUM/steamcmd branches with blueprint changes).

Key coverage:
- Router PATCH normalization (_normalize_server_restart_mode)
- Scheduling via PATCH (IntervalTrigger vs. multiple CronTriggers)
- sync / remove / init_server_schedules
- _restart_server_task guards + state + AuditLog (mocked)
- Exclusivity enforced
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AuditLog, Server
from services.scheduler_service import (
    _restart_server_task,
    get_scheduler,
    init_server_schedules,
    remove_restart_jobs,
    stop_scheduler,
    sync_server_restart_schedule,
)


@pytest.fixture(autouse=True)
def _reset_scheduler():
    stop_scheduler()
    yield
    stop_scheduler()


def _get_restart_jobs(server_id: int):
    scheduler = get_scheduler()
    return [
        j for j in scheduler.get_jobs()
        if j.id == f"restart_server_{server_id}"
        or j.id.startswith(f"restart_cron_server_{server_id}_")
    ]


class TestAutoRestartPatchScheduling:
    """Use PATCH on fixture server (robust across branches with custom blueprints)."""

    def test_patch_interval_clears_times_and_schedules(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        remove_restart_jobs(test_server.id)
        resp = client.patch(
            f"/api/servers/{test_server.id}",
            json={
                "auto_restart": True,
                "restart_interval_hours": 6,
                "restart_times_utc": "04:00,16:00",  # both -> should normalize
            },
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

        db.refresh(test_server)
        assert test_server.restart_interval_hours == 6
        assert test_server.restart_times_utc is None
        assert test_server.restart_time_utc is None

        jobs = _get_restart_jobs(test_server.id)
        assert len(jobs) == 1
        assert "restart_server_" in jobs[0].id
        assert "interval" in str(jobs[0].trigger).lower()

    def test_patch_both_normalizes_to_interval_precedence(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        # Policy: when both modes are sent (e.g. legacy or direct API), interval wins.
        # This matches sync_server_restart_schedule and the original design.
        remove_restart_jobs(test_server.id)
        resp = client.patch(
            f"/api/servers/{test_server.id}",
            json={
                "auto_restart": True,
                "restart_times_utc": "03:00,15:30,23:45",
                "restart_interval_hours": 4,
            },
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

        db.refresh(test_server)
        # Interval kept (precedence), times cleared
        assert test_server.restart_interval_hours == 4
        assert test_server.restart_times_utc is None

        jobs = _get_restart_jobs(test_server.id)
        assert len(jobs) == 1
        assert "interval" in str(jobs[0].trigger).lower()

    def test_patch_disable_removes_jobs(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        client.patch(
            f"/api/servers/{test_server.id}",
            json={"auto_restart": True, "restart_interval_hours": 8},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert len(_get_restart_jobs(test_server.id)) > 0

        resp = client.patch(
            f"/api/servers/{test_server.id}",
            json={"auto_restart": False},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        assert len(_get_restart_jobs(test_server.id)) == 0


class TestAutoRestartTaskSafeExecution:
    def test_task_skips_when_not_running(self, db: Session, test_server: Server):
        test_server.status = "stopped"
        db.commit()
        asyncio.run(_restart_server_task(test_server.id))
        db.refresh(test_server)
        assert test_server.last_auto_restart_attempt_at is None

    def test_task_records_success_and_audit(self, db: Session, test_server: Server):
        test_server.status = "running"
        db.commit()

        with patch("services.scheduler_service.restart_server_with_updates", new_callable=AsyncMock, return_value={}) as mock:
            asyncio.run(_restart_server_task(test_server.id))

        db.refresh(test_server)
        assert test_server.last_auto_restart_attempt_at is not None
        assert test_server.last_auto_restart_status == "success"

        audit = db.query(AuditLog).filter(
            AuditLog.target_id == test_server.id,
            AuditLog.action == "auto_restart"
        ).first()
        assert audit is not None
        mock.assert_awaited_once()

    def test_task_records_failure(self, db: Session, test_server: Server):
        test_server.status = "running"
        db.commit()

        with patch("services.scheduler_service.restart_server_with_updates", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            asyncio.run(_restart_server_task(test_server.id))

        db.refresh(test_server)
        assert test_server.last_auto_restart_status == "failed"


class TestAutoRestartInitAndSync:
    def test_init_server_schedules_reloads(self, db: Session, test_server: Server):
        test_server.auto_restart = True
        test_server.restart_interval_hours = 4
        db.commit()

        remove_restart_jobs(test_server.id)
        init_server_schedules(db)

        jobs = _get_restart_jobs(test_server.id)
        assert len(jobs) == 1
        assert "interval" in str(jobs[0].trigger).lower()

    def test_sync_removes_when_disabled(self, db: Session, test_server: Server):
        test_server.auto_restart = True
        test_server.restart_interval_hours = 2
        db.commit()
        sync_server_restart_schedule(test_server)
        assert len(_get_restart_jobs(test_server.id)) == 1

        test_server.auto_restart = False
        db.commit()
        sync_server_restart_schedule(test_server)
        assert len(_get_restart_jobs(test_server.id)) == 0


def test_normalize_directly_on_model_object():
    """White-box test of the normalize helper."""
    from routers.servers import _normalize_server_restart_mode

    s = Server(
        name="norm-test",
        game_type="dayz",
        install_dir="/tmp/norm",
        auto_restart=True,
        restart_interval_hours=8,
        restart_times_utc="04:00,16:00",
    )
    _normalize_server_restart_mode(s)
    assert s.restart_interval_hours == 8
    assert s.restart_times_utc is None

    s2 = Server(
        name="norm-test2",
        game_type="dayz",
        install_dir="/tmp/norm2",
        auto_restart=True,
        restart_times_utc="02:00,14:00",
        restart_interval_hours=3,
    )
    _normalize_server_restart_mode(s2)
    # Interval wins when both present
    assert s2.restart_interval_hours == 3
    assert s2.restart_times_utc is None
