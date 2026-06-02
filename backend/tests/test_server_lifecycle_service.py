"""
Unit tests for the central server_lifecycle_service.

These tests focus on the new unified restart path and lock behavior.
They are isolated: heavy external dependencies (plugin, docker, firewall, iptables)
are mocked so we test the orchestration and locking logic itself.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Server
from services.server_lifecycle_service import (
    _run_restart,
    _run_start,
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


def _steam_plugin():
    class Plugin:
        def __init__(self) -> None:
            self.validate_calls = 0

        def get_blueprint(self):
            return SimpleNamespace(source=SimpleNamespace(type=SimpleNamespace(value="steam")))

        def prepare_for_updates(self, server):
            return None

        def check_for_mod_updates(self, server):
            return []

        def perform_server_file_update(self, server):
            self.validate_calls += 1
            return {"ok": True}

        def start(self, server):
            return {"ok": True}

        def stop(self, server):
            return {"ok": True}

    return Plugin()


def _http_plugin(update_available: bool = True):
    class Plugin:
        def __init__(self) -> None:
            self.check_calls = 0
            self.update_calls = 0

        def get_blueprint(self):
            return SimpleNamespace(source=SimpleNamespace(type=SimpleNamespace(value="http")))

        def prepare_for_updates(self, server):
            return None

        def check_for_server_file_update(self, server):
            self.check_calls += 1
            return {"action": "update" if update_available else "none"}

        def check_for_mod_updates(self, server):
            return []

        def perform_server_file_update(self, server):
            self.update_calls += 1
            return {"ok": True}

        def start(self, server):
            return {"ok": True}

        def stop(self, server):
            return {"ok": True}

    return Plugin()


def test_start_always_runs_steam_validate_before_container_start():
    server = Server(id=21, name="Steam", game_type="dayz", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _steam_plugin()

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.close_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch("services.server_lifecycle_service.iptables_revoke_server"):
        _run_start(db, server, plugin)

    assert plugin.validate_calls == 1
    assert server.status == "running"


def test_start_preserves_check_based_http_server_file_update():
    server = Server(id=23, name="Http", game_type="custom", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _http_plugin(update_available=True)

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.close_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch("services.server_lifecycle_service.iptables_revoke_server"):
        _run_start(db, server, plugin)

    assert plugin.check_calls == 1
    assert plugin.update_calls == 1
    assert server.status == "running"


def test_restart_preserves_check_based_http_server_file_update():
    server = Server(id=24, name="Http", game_type="custom", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _http_plugin(update_available=True)

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.close_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch("services.server_lifecycle_service.iptables_revoke_server"):
        _run_restart(db, server, plugin)

    assert plugin.check_calls == 1
    assert plugin.update_calls == 1
    assert server.status == "running"


def test_restart_always_runs_steam_validate_even_without_passive_update_action():
    server = Server(id=22, name="Steam", game_type="dayz", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _steam_plugin()

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.close_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch("services.server_lifecycle_service.iptables_revoke_server"):
        _run_restart(db, server, plugin)

    assert plugin.validate_calls == 1
    assert server.status == "running"
