"""
Unit tests for the central server_lifecycle_service.

These tests focus on the new unified restart path and lock behavior.
They are isolated: heavy external dependencies (plugin, docker, firewall, iptables)
are mocked so we test the orchestration and locking logic itself.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Server
from services.server_lifecycle_service import (
    get_server_lifecycle_lock,
    queue_lifecycle_operation,
    reset_lifecycle_jobs_for_tests,
    restart_server_with_updates,
)


def test_get_server_lifecycle_lock_returns_same_instance_for_same_id():
    """Basic contract: same server_id always yields the exact same lock instance."""
    lock1 = get_server_lifecycle_lock(42)
    lock2 = get_server_lifecycle_lock(42)
    assert lock1 is lock2
    assert isinstance(lock1, asyncio.Lock)


def test_get_server_lifecycle_lock_different_ids_are_different():
    """Different servers must not share a lock (would serialize unrelated operations)."""
    lock_a = get_server_lifecycle_lock(1)
    lock_b = get_server_lifecycle_lock(2)
    assert lock_a is not lock_b


def test_restart_server_with_updates_raises_on_unsupported_game_type():
    """Early validation: unknown game_type must fail fast with clear error."""
    from fastapi import HTTPException

    fake_server = Server(id=1, game_type="nonexistent_game_xyz")
    fake_db = MagicMock(spec=Session)

    with pytest.raises(HTTPException) as exc:
        # We can call it directly; it fails before any async work
        import asyncio
        asyncio.run(restart_server_with_updates(fake_db, fake_server))

    assert exc.value.status_code == 400
    assert "nicht unterstützt" in str(exc.value.detail)


def test_queue_lifecycle_operation_returns_before_worker_runs():
    fake_server = Server(id=10, game_type="dayz", status="running")
    fake_db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service._start_lifecycle_thread") as start_thread:
        result = queue_lifecycle_operation(fake_db, fake_server, "stop")

    assert result == {
        "message": "Lifecycle-Aktion wurde queued",
        "status": "queued",
        "operation": "stop",
    }
    assert fake_server.status == "queued"
    fake_db.commit.assert_called_once()
    start_thread.assert_called_once()
    reset_lifecycle_jobs_for_tests()


def test_queue_lifecycle_operation_blocks_parallel_action_for_same_server():
    fake_server = Server(id=11, game_type="dayz", status="running")
    fake_db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service._start_lifecycle_thread"):
        queue_lifecycle_operation(fake_db, fake_server, "stop")
        with pytest.raises(HTTPException) as exc:
            queue_lifecycle_operation(fake_db, fake_server, "kill")

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "server_lifecycle_already_running"
    reset_lifecycle_jobs_for_tests()


def test_restart_server_with_updates_blocks_when_lifecycle_job_is_active():
    fake_server = Server(id=12, game_type="dayz", status="running")
    fake_db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service._start_lifecycle_thread"):
        queue_lifecycle_operation(fake_db, fake_server, "stop")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(restart_server_with_updates(fake_db, fake_server))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "server_lifecycle_already_running"
    reset_lifecycle_jobs_for_tests()
