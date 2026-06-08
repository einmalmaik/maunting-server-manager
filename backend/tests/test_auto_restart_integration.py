"""
Integration tests for Auto-Restart logic (interval OR fixed times, exclusively).

These tests verify scheduling, normalization, job management and task behavior
WITHOUT ever performing real server restarts or Docker operations.

Key coverage:
- Router-level normalization (_normalize_server_restart_mode) via API
- Scheduling via create + PATCH (IntervalTrigger vs. multiple CronTriggers)
- sync / remove behavior
- init_server_schedules on "startup"
- _restart_server_task guards + state updates + AuditLog (fully mocked restart path)
- Exclusivity: never both restart_interval_hours and restart_*_utc at the same time in DB

All tests use the existing test fixtures (in-memory SQLite, client, auth, test_server).
Real lifecycle is patched so no containers are touched.
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
    """Ensure clean APScheduler state between tests (like in test_scheduler_service.py)."""
    stop_scheduler()
    yield
    stop_scheduler()


def _get_restart_jobs(server_id: int):
    """Helper: return current restart jobs for a server id."""
    scheduler = get_scheduler()
    return [
        j for j in scheduler.get_jobs()
        if j.id == f"restart_server_{server_id}"
        or j.id.startswith(f"restart_cron_server_{server_id}_")
    ]


class TestAutoRestartCreateAndScheduling:
    """Tests that cover create + immediate scheduling (via router)."""

    def test_create_with_interval_schedules_interval_job_and_clears_times(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session
    ):
        payload = {
            "name": "autorestart-interval",
            "game_type": "dayz",
            "auto_restart": True,
            "restart_interval_hours": 6,
            # deliberately send both to test normalization
            "restart_times_utc": "04:00,16:00",
        }
        resp = client.post(
            "/api/servers",
            json=payload,
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 201
        server_id = resp.json()["id"]

        db.refresh(db.query(Server).get(server_id))  # type: ignore[arg-type]
        server = db.query(Server).get(server_id)

        # Normalization must have won: interval kept, times cleared
        assert server.auto_restart is True
        assert server.restart_interval_hours == 6
        assert server.restart_time_utc is None
        assert server.restart_times_utc is None

        # Scheduler must have exactly one interval job
        jobs = _get_restart_jobs(server_id)
        assert len(jobs) == 1
        assert "restart_server_" in jobs[0].id
        assert "IntervalTrigger" in str(jobs[0].trigger)

    def test_create_with_fixed_times_schedules_multiple_cron_jobs(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session
    ):
        payload = {
            "name": "autorestart-fixed",
            "game_type": "dayz",
            "auto_restart": True,
            "restart_times_utc": "03:00,15:30,23:45",
            # send interval too to prove it gets cleared
            "restart_interval_hours": 4,
        }
        resp = client.post(
            "/api/servers",
            json=payload,
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 201
        server_id = resp.json()["id"]

        server = db.query(Server).get(server_id)
        assert server.restart_interval_hours is None
        assert server.restart_times_utc == "03:00,15:30,23:45"

        jobs = _get_restart_jobs(server_id)
        assert len(jobs) == 3  # one per time
        assert all("restart_cron_server_" in j.id for j in jobs)
        assert all("CronTrigger" in str(j.trigger) for j in jobs)


class TestAutoRestartPatchNormalization:
    """PATCH must also normalize and re-sync."""

    def test_patch_both_modes_normalizes_to_interval(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        # Start clean
        remove_restart_jobs(test_server.id)

        # Send mixed payload
        resp = client.patch(
            f"/api/servers/{test_server.id}",
            json={
                "auto_restart": True,
                "restart_interval_hours": 12,
                "restart_times_utc": "04:00,20:00",
            },
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

        db.refresh(test_server)
        assert test_server.restart_interval_hours == 12
        assert test_server.restart_times_utc is None
        assert test_server.restart_time_utc is None

        jobs = _get_restart_jobs(test_server.id)
        assert len(jobs) == 1
        assert "IntervalTrigger" in str(jobs[0].trigger)

    def test_patch_to_fixed_times_clears_interval(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        remove_restart_jobs(test_server.id)

        resp = client.patch(
            f"/api/servers/{test_server.id}",
            json={
                "auto_restart": True,
                "restart_times_utc": "05:00,17:00",
            },
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

        db.refresh(test_server)
        assert test_server.restart_interval_hours is None
        assert test_server.restart_times_utc == "05:00,17:00"

        jobs = _get_restart_jobs(test_server.id)
        assert len(jobs) == 2

    def test_patch_disable_removes_all_jobs(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        # First enable with interval
        client.patch(
            f"/api/servers/{test_server.id}",
            json={"auto_restart": True, "restart_interval_hours": 8},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert len(_get_restart_jobs(test_server.id)) == 1

        # Now disable
        resp = client.patch(
            f"/api/servers/{test_server.id}",
            json={"auto_restart": False},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

        db.refresh(test_server)
        assert test_server.auto_restart is False
        assert len(_get_restart_jobs(test_server.id)) == 0


class TestAutoRestartTaskSafeExecution:
    """Test _restart_server_task in isolation (no real restart)."""

    def test_task_skips_when_server_not_running(self, db: Session, test_server: Server):
        test_server.status = "stopped"
        db.commit()

        # Should return quickly without touching lifecycle
        asyncio.run(_restart_server_task(test_server.id))

        db.refresh(test_server)
        # No attempt recorded when skipped
        assert test_server.last_auto_restart_attempt_at is None

    def test_task_records_attempt_success_and_audit_when_running(
        self, db: Session, test_server: Server
    ):
        test_server.status = "running"
        db.commit()

        with patch(
            "services.scheduler_service.restart_server_with_updates",
            new_callable=AsyncMock,
            return_value={"message": "restarted"},
        ) as mock_restart:
            asyncio.run(_restart_server_task(test_server.id))

        db.refresh(test_server)

        assert test_server.last_auto_restart_attempt_at is not None
        assert test_server.last_auto_restart_completed_at is not None
        assert test_server.last_auto_restart_status == "success"

        # AuditLog must have been written (user_id=None for auto)
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.target_id == test_server.id, AuditLog.action == "auto_restart")
            .first()
        )
        assert audit is not None
        assert "Auto-restart triggered" in (audit.details or "")

        mock_restart.assert_awaited_once()

    def test_task_records_failure_status_on_exception(
        self, db: Session, test_server: Server
    ):
        test_server.status = "running"
        db.commit()

        with patch(
            "services.scheduler_service.restart_server_with_updates",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            asyncio.run(_restart_server_task(test_server.id))

        db.refresh(test_server)
        assert test_server.last_auto_restart_status == "failed"
        # attempt should still be recorded
        assert test_server.last_auto_restart_attempt_at is not None


class TestAutoRestartInitAndSync:
    """Covers init_server_schedules (called on panel start) and manual sync."""

    def test_init_server_schedules_reloads_existing_auto_restart(
        self, db: Session, test_server: Server
    ):
        # Prepare a server that should be scheduled on "startup"
        test_server.auto_restart = True
        test_server.restart_interval_hours = 4
        test_server.restart_times_utc = None
        db.commit()

        # Make sure nothing is scheduled yet
        remove_restart_jobs(test_server.id)
        assert len(_get_restart_jobs(test_server.id)) == 0

        # Simulate panel startup
        init_server_schedules(db)

        jobs = _get_restart_jobs(test_server.id)
        assert len(jobs) == 1
        assert "IntervalTrigger" in str(jobs[0].trigger)

    def test_sync_removes_jobs_when_auto_restart_disabled(
        self, db: Session, test_server: Server
    ):
        test_server.auto_restart = True
        test_server.restart_interval_hours = 2
        db.commit()

        sync_server_restart_schedule(test_server)
        assert len(_get_restart_jobs(test_server.id)) == 1

        # Simulate user turning it off directly in DB (or via another path)
        test_server.auto_restart = False
        db.commit()

        sync_server_restart_schedule(test_server)
        assert len(_get_restart_jobs(test_server.id)) == 0


def test_normalize_directly_on_model_object(db: Session):
    """White-box test of the normalize helper (called by router on every write)."""
    # We import the private helper for testability of the new logic.
    # This is acceptable for integration/feature coverage.
    from routers.servers import _normalize_server_restart_mode

    s = Server(
        name="norm-test",
        game_type="dayz",
        install_dir="/tmp/norm",
        auto_restart=True,
        restart_interval_hours=8,
        restart_times_utc="04:00,16:00",
        restart_time_utc="05:00",
    )

    _normalize_server_restart_mode(s)

    assert s.restart_interval_hours == 8
    assert s.restart_times_utc is None
    assert s.restart_time_utc is None

    # Reverse direction
    s2 = Server(
        name="norm-test2",
        game_type="dayz",
        install_dir="/tmp/norm2",
        auto_restart=True,
        restart_times_utc="02:00,14:00",
        restart_interval_hours=3,
    )
    _normalize_server_restart_mode(s2)

    assert s2.restart_interval_hours is None
    assert s2.restart_times_utc == "02:00,14:00"
