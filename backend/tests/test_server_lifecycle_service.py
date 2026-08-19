"""
Unit tests for the central server_lifecycle_service.

These tests focus on the new unified restart path and lock behavior.
They are isolated: heavy external dependencies (plugin, docker, firewall, iptables)
are mocked so we test the orchestration and locking logic itself.
"""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
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
    assert isinstance(lock1, type(threading.Lock()))


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
    fake_db.query.return_value.filter.return_value.first.return_value = fake_server

    with patch("services.server_lifecycle_service.SessionLocal", return_value=fake_db):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(restart_server_with_updates(fake_db, fake_server))

    assert exc.value.status_code == 400
    assert "nicht unterstützt" in str(exc.value.detail)


def test_queue_lifecycle_operation_returns_before_worker_runs():
    fake_server = Server(id=10, game_type="dayz", status="running")
    fake_db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service._start_lifecycle_thread") as start_thread, patch(
        "services.guardian_state_service.set_desired_power_state"
    ) as set_desired, patch(
        "services.server_lifecycle_service.sync_desired_state_to_agent",
        return_value=True,
    ) as sync_desired:
        result = queue_lifecycle_operation(fake_db, fake_server, "stop")

    assert result == {
        "message": "Lifecycle-Aktion wurde queued",
        "status": "queued",
        "operation": "stop",
    }
    assert fake_server.status == "queued"
    fake_db.commit.assert_called_once()
    set_desired.assert_called_once_with(fake_db, fake_server, "stopped")
    sync_desired.assert_called_once_with(fake_db, fake_server)
    start_thread.assert_called_once()
    reset_lifecycle_jobs_for_tests()


def test_queue_restart_sets_desired_power_state_to_running():
    fake_server = Server(id=1010, game_type="dayz", status="running")
    fake_db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service._start_lifecycle_thread"), patch(
        "services.guardian_state_service.set_desired_power_state"
    ) as set_desired, patch(
        "services.server_lifecycle_service.sync_desired_state_to_agent",
        return_value=True,
    ):
        queue_lifecycle_operation(fake_db, fake_server, "restart")

    set_desired.assert_called_once_with(fake_db, fake_server, "running")
    reset_lifecycle_jobs_for_tests()


def test_queue_lifecycle_operation_kill_overrides_active_job_as_emergency():
    """Kill als Notfall-Button soll auch bei laufendem Start/Restart/Stop funktionieren (override)."""
    fake_server = Server(id=11, game_type="dayz", status="running")
    fake_db = MagicMock(spec=Session)

    def fake_reconcile(_db, _server):
        if _server.guardian_recovery_suspension:
            _server.guardian_agent_recovery_suspension_json = _server.guardian_recovery_suspension
        else:
            _server.guardian_agent_recovery_suspension_json = None
        return {
            "payload_hash": "sha256:dummy",
            "recovery_suspension": (
                json.loads(_server.guardian_recovery_suspension)
                if _server.guardian_recovery_suspension
                else None
            ),
        }

    with patch("services.server_lifecycle_service._start_lifecycle_thread"), \
         patch("services.guardian_sync_service.reconcile_guardian_server", side_effect=fake_reconcile):
        queue_lifecycle_operation(fake_db, fake_server, "stop")
        # kill must NOT raise 409, it forces the lock
        result = queue_lifecycle_operation(fake_db, fake_server, "kill")
        assert "queued" in str(result).lower() or result.get("operation") == "kill"

    reset_lifecycle_jobs_for_tests()


def test_kill_reports_docker_remove_failure() -> None:
    fake_server = Server(
        id=111,
        name="KillFailure",
        game_type="dayz",
        status="running",
        desired_power_state="running",
    )
    fake_db = MagicMock(spec=Session)

    with patch(
        "services.guardian_state_service.set_desired_power_state",
    ) as set_desired, patch(
        "services.server_lifecycle_service.sync_desired_state_to_agent",
        return_value=False,
    ), patch(
        "services.server_lifecycle_service.docker_service.remove",
        side_effect=RuntimeError("synthetic docker failure"),
    ):
        with pytest.raises(HTTPException) as exc:
            queue_lifecycle_operation(fake_db, fake_server, "kill")

    assert exc.value.status_code == 500
    assert fake_server.status != "stopped"
    set_desired.assert_called_once_with(fake_db, fake_server, "stopped")


def test_kill_cancels_inflight_start_before_container_creation(db: Session) -> None:
    import services.server_lifecycle_service as lifecycle_service

    server = Server(
        name="KillDuringStart",
        game_type="minecraft",
        install_dir="/tmp/kill-during-start",
        status="stopped",
        desired_power_state="stopped",
        public_bind_ip="127.0.0.1",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    preparation_started = threading.Event()
    release_preparation = threading.Event()
    worker_finished = threading.Event()
    plugin = MagicMock()
    plugin.get_blueprint.return_value = None
    plugin.check_for_mod_updates.return_value = []
    plugin.start.return_value = {"ok": True}

    def block_before_container_start(*_args, **_kwargs) -> None:
        preparation_started.set()
        release_preparation.wait(3)

    def mark_worker_finished(*_args, **_kwargs) -> None:
        worker_finished.set()

    original_worker = lifecycle_service._run_lifecycle_job

    def run_worker(*args, **kwargs) -> None:
        try:
            original_worker(*args, **kwargs)
        finally:
            mark_worker_finished()

    with patch(
        "services.server_lifecycle_service.get_plugin",
        return_value=plugin,
    ), patch(
        "services.server_lifecycle_service._run_pre_start_backup_if_enabled",
        side_effect=block_before_container_start,
    ), patch(
        "services.server_lifecycle_service.open_ports",
    ), patch(
        "services.server_lifecycle_service.iptables_accept_server",
    ), patch(
        "services.server_lifecycle_service.close_ports",
    ), patch(
        "services.server_lifecycle_service.iptables_revoke_server",
    ), patch(
        "services.server_lifecycle_service.docker_service.remove",
        return_value={"ok": True},
    ) as remove, patch(
        "services.server_lifecycle_service._run_lifecycle_job",
        side_effect=run_worker,
    ):
        queue_lifecycle_operation(db, server, "start")
        assert preparation_started.wait(2)
        queue_lifecycle_operation(db, server, "kill")
        release_preparation.set()
        assert worker_finished.wait(3)

    plugin.start.assert_not_called()
    assert remove.called
    db.expire_all()
    persisted = db.query(Server).filter(Server.id == server.id).first()
    assert persisted is not None
    assert persisted.desired_power_state == "stopped"
    assert persisted.status == "stopped"
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


def _steam_plugin(update_available: bool = False):
    from blueprints.schema import BlueprintUpdateStrategy

    class _Source:
        def __init__(self, type_value: str) -> None:
            self.type = SimpleNamespace(value=type_value)

        def effective_update_strategy(self):
            return BlueprintUpdateStrategy.ALWAYS_VALIDATE

    class Plugin:
        def __init__(self) -> None:
            self.validate_calls = 0
            self.check_calls = 0

        def get_blueprint(self):
            return SimpleNamespace(source=_Source("steam"))

        def prepare_for_updates(self, server):
            return None

        def check_for_server_file_update(self, server):
            self.check_calls += 1
            return {"action": "update" if update_available else "none"}

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
    from blueprints.schema import BlueprintUpdateStrategy

    class _Source:
        def __init__(self, type_value: str) -> None:
            self.type = SimpleNamespace(value=type_value)

        def effective_update_strategy(self):
            return BlueprintUpdateStrategy.CHECK_BASED

    class Plugin:
        def __init__(self) -> None:
            self.check_calls = 0
            self.update_calls = 0

        def get_blueprint(self):
            return SimpleNamespace(source=_Source("http"))

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


def _docker_only_plugin():
    from blueprints.schema import BlueprintUpdateStrategy

    class _Source:
        def __init__(self, type_value: str) -> None:
            self.type = SimpleNamespace(value=type_value)

        def effective_update_strategy(self):
            return BlueprintUpdateStrategy.NONE

    class Plugin:
        def __init__(self) -> None:
            self.validate_calls = 0
            self.update_calls = 0
            self.check_calls = 0

        def get_blueprint(self):
            return SimpleNamespace(source=_Source("dockerOnly"))

        def prepare_for_updates(self, server):
            return None

        def check_for_server_file_update(self, server):
            self.check_calls += 1
            return {"action": "none"}

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


def test_start_always_runs_steam_validate_for_steam_source():
    """Steam-Blueprints müssen bei jedem Start ein SteamCMD validate ausführen,
    damit Game-Binaries (Patches, Security-Updates) aktuell bleiben.
    Der passive Update-Check liefert für Steam absichtlich 'none' - das
    Lifecycle überschreibt das deklarativ für Steam-Source.
    """
    server = Server(id=21, name="Steam", game_type="dayz", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _steam_plugin(update_available=False)

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"):
        _run_start(db, server, plugin)

    assert plugin.validate_calls == 1
    assert server.status == "running"


def test_start_skips_update_for_http_source_when_no_update_available():
    server = Server(id=25, name="Http", game_type="custom", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _http_plugin(update_available=False)

    with patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"):
        _run_start(db, server, plugin)

    assert plugin.check_calls == 1
    assert plugin.update_calls == 0
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


def test_restart_always_runs_steam_validate_for_steam_source():
    """Steam-Blueprints müssen bei jedem Restart ein SteamCMD validate ausführen."""
    server = Server(id=22, name="Steam", game_type="dayz", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _steam_plugin(update_available=False)

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.close_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch("services.server_lifecycle_service.iptables_revoke_server"):
        _run_restart(db, server, plugin)

    assert plugin.validate_calls == 1
    assert server.status == "running"


def test_restart_skips_update_for_http_source_when_no_update_available():
    server = Server(id=26, name="Http", game_type="custom", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _http_plugin(update_available=False)

    with patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.close_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch("services.server_lifecycle_service.iptables_revoke_server"):
        _run_restart(db, server, plugin)

    assert plugin.check_calls == 1
    assert plugin.update_calls == 0
    assert server.status == "running"


def test_start_skips_update_for_docker_only_source():
    """dockerOnly-Sourcen haben keine Auto-Update-Mechanik.

    Der Provider-Neutralitaets-Test bestaetigt: weder perform_server_file_update
    noch check_for_server_file_update werden aufgerufen, der Container startet
    trotzdem. Source-Strategie 'none' wird respektiert.
    """
    server = Server(id=27, name="DockerOnly", game_type="custom", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _docker_only_plugin()

    with patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"):
        _run_start(db, server, plugin)

    assert plugin.check_calls == 0
    assert plugin.update_calls == 0
    assert server.status == "running"


def test_restart_skips_update_for_docker_only_source():
    server = Server(id=28, name="DockerOnly", game_type="custom", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = _docker_only_plugin()

    with patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.close_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch("services.server_lifecycle_service.iptables_revoke_server"):
        _run_restart(db, server, plugin)

    assert plugin.check_calls == 0
    assert plugin.update_calls == 0
    assert server.status == "running"


def test_start_respects_explicit_check_based_for_steam_source():
    """Blueprint kann Steam-Default ueberschreiben und checkBased erzwingen.

    Provider-neutraler Test: eine Steam-Source mit updateStrategy=checkBased
    fuehrt KEIN perform_server_file_update aus, wenn der passive Check 'none'
    meldet. Validiert, dass die Policy im Blueprint liegt, nicht im Core.
    """
    from blueprints.schema import BlueprintUpdateStrategy

    class _Source:
        def __init__(self) -> None:
            self.type = SimpleNamespace(value="steam")
            self.updateStrategy = BlueprintUpdateStrategy.CHECK_BASED

        def effective_update_strategy(self):
            return self.updateStrategy

    class Plugin(_steam_plugin(update_available=False).__class__):
        def get_blueprint(self):
            return SimpleNamespace(source=_Source())

    server = Server(id=29, name="Steam", game_type="dayz", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = Plugin()

    with patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"):
        _run_start(db, server, plugin)

    assert plugin.check_calls == 1
    assert plugin.validate_calls == 0
    assert server.status == "running"


def test_start_respects_explicit_always_validate_for_http_source():
    """HTTP-Source kann aggressiv auf alwaysValidate umgestellt werden.

    Provider-neutraler Test: ein HTTP-Blueprint mit updateStrategy=alwaysValidate
    fuehrt perform_server_file_update IMMER aus, auch wenn der passive Check
    'none' meldet. Validiert die generische Mechanik fuer Wine/Linux-Binaries
    die selbst versionieren.
    """
    from blueprints.schema import BlueprintUpdateStrategy

    class _Source:
        def __init__(self) -> None:
            self.type = SimpleNamespace(value="http")
            self.updateStrategy = BlueprintUpdateStrategy.ALWAYS_VALIDATE

        def effective_update_strategy(self):
            return self.updateStrategy

    class Plugin(_http_plugin(update_available=False).__class__):
        def get_blueprint(self):
            return SimpleNamespace(source=_Source())

    server = Server(id=30, name="Http", game_type="custom", install_dir="/tmp/test", public_bind_ip="127.0.0.1")
    server.ports = []
    db = MagicMock(spec=Session)
    plugin = Plugin()

    with patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"):
        _run_start(db, server, plugin)

    assert plugin.update_calls == 1
    assert server.status == "running"


def test_lifecycle_job_applies_recovery_suspension(db: Session) -> None:
    """Verify that starting a server lifecycle job sets and clears the recovery lease suspension."""
    from services.server_lifecycle_service import _run_lifecycle_job
    from models import Server, Node

    node = Node(id=99, name="node-99", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=88,
        name="SuspensionTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    plugin = MagicMock()
    plugin.start.return_value = {"ok": True}

    def fake_set(_db, _server, operation_id, reason, suspend_until):
        assert suspend_until - datetime.now(timezone.utc) > timedelta(hours=3, minutes=59)
        _server.guardian_recovery_suspension = json.dumps({
            "operation_id": operation_id,
            "reason": reason,
            "suspend_until": suspend_until.isoformat(),
        })

    def fake_clear(_db, _server, operation_id):
        _server.guardian_recovery_suspension = None

    def fake_reconcile(_db, _server):
        if _server.guardian_recovery_suspension:
            _server.guardian_agent_recovery_suspension_json = _server.guardian_recovery_suspension
        else:
            _server.guardian_agent_recovery_suspension_json = None
        return {
            "payload_hash": "sha256:dummy",
            "recovery_suspension": (
                json.loads(_server.guardian_recovery_suspension)
                if _server.guardian_recovery_suspension
                else None
            ),
        }

    with patch("services.guardian_state_service.set_recovery_suspension", side_effect=fake_set) as mock_set, \
         patch("services.guardian_state_service.clear_recovery_suspension", side_effect=fake_clear) as mock_clear, \
         patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=fake_reconcile) as mock_reconcile, \
         patch("services.server_lifecycle_service.get_plugin", return_value=plugin), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"):

        _run_lifecycle_job(server.id, "start")

        mock_set.assert_called_once()
        mock_clear.assert_called_once()
        assert mock_reconcile.call_count == 2


def test_lifecycle_does_not_start_before_agent_accepts_lease(db: Session) -> None:
    from services.server_lifecycle_service import _run_lifecycle_job
    from models import Server, Node
    node = Node(id=102, name="node-102", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=90,
        name="LeaseAcceptTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    plugin = MagicMock()
    plugin.start.return_value = {"ok": True}

    with patch("services.guardian_state_service.set_recovery_suspension"), \
         patch("services.guardian_state_service.clear_recovery_suspension"), \
         patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=RuntimeError("Lease sync failed")), \
         patch("services.server_lifecycle_service.get_plugin", return_value=plugin):

        with pytest.raises(RuntimeError, match="Lease sync failed"):
            _run_lifecycle_job(server.id, "start")

        # Crucial check: plugin start must NOT execute if lease sync failed
        plugin.start.assert_not_called()


def test_lease_is_immediately_synchronized(db: Session) -> None:
    from services.server_lifecycle_service import guardian_recovery_suspension_lease
    from models import Server, Node
    node = Node(id=103, name="node-103", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=91,
        name="ImmediateLeaseSyncTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    reconcile_calls = []

    def fake_reconcile(_db, _server):
        reconcile_calls.append(_server.guardian_recovery_suspension)
        _server.guardian_agent_recovery_suspension_json = _server.guardian_recovery_suspension
        return {
            "payload_hash": "sha256:dummy",
            "recovery_suspension": (
                json.loads(_server.guardian_recovery_suspension)
                if _server.guardian_recovery_suspension
                else None
            ),
        }

    with patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=fake_reconcile):
        with guardian_recovery_suspension_lease(db, server, "unit-test-lease"):
            # Verify that reconcile was invoked immediately upon entering the context manager
            assert len(reconcile_calls) == 1
            assert reconcile_calls[0] is not None
            assert "unit-test-lease" in reconcile_calls[0]


def test_lease_clear_is_immediately_synchronized(db: Session) -> None:
    from services.server_lifecycle_service import guardian_recovery_suspension_lease
    from models import Server, Node
    node = Node(id=104, name="node-104", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=92,
        name="ImmediateLeaseClearSyncTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    reconcile_calls = []

    def fake_reconcile(_db, _server):
        reconcile_calls.append(_server.guardian_recovery_suspension)
        if _server.guardian_recovery_suspension:
            _server.guardian_agent_recovery_suspension_json = _server.guardian_recovery_suspension
        else:
            _server.guardian_agent_recovery_suspension_json = None
        return {
            "payload_hash": "sha256:dummy",
            "recovery_suspension": (
                json.loads(_server.guardian_recovery_suspension)
                if _server.guardian_recovery_suspension
                else None
            ),
        }

    with patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=fake_reconcile):
        with guardian_recovery_suspension_lease(db, server, "unit-test-clear-lease"):
            pass

    # Reconcile called twice: 1st on set (suspension active), 2nd on clear (suspension cleared)
    assert len(reconcile_calls) == 2
    assert reconcile_calls[0] is not None
    assert "unit-test-clear-lease" in reconcile_calls[0]
    assert reconcile_calls[1] is None


def test_failed_lease_sync_aborts_lifecycle_operation(db: Session) -> None:
    from services.server_lifecycle_service import _run_lifecycle_job
    from models import Server, Node

    node = Node(id=101, name="node-101", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=89,
        name="AbortTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    plugin = MagicMock()
    plugin.start.return_value = {"ok": True}

    with patch("services.guardian_state_service.set_recovery_suspension"), \
         patch("services.guardian_state_service.clear_recovery_suspension"), \
         patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=RuntimeError("Sync failed")), \
         patch("services.server_lifecycle_service.get_plugin", return_value=plugin) as mock_get_plugin, \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"):

        with pytest.raises(RuntimeError, match="Sync failed"):
            _run_lifecycle_job(server.id, "start")

        # The lifecycle operation (plugin.start) should not have been called because sync failed
        plugin.start.assert_not_called()


def test_lifecycle_aborts_if_agent_does_not_confirm_lease(db: Session) -> None:
    """If reconcile returns success dict but agent observed state does NOT match op_id, operation aborts."""
    from services.server_lifecycle_service import guardian_recovery_suspension_lease
    from models import Server, Node

    node = Node(id=105, name="node-105", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=93,
        name="UnconfirmedLeaseTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    # Fake reconcile returns success dict but leaves guardian_agent_recovery_suspension_json as None
    def fake_reconcile(_db, _server):
        return {"payload_hash": "sha256:dummy"}

    with patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=fake_reconcile):
        with pytest.raises(RuntimeError, match="lease sync failed"):
            with guardian_recovery_suspension_lease(db, server, "unit-test-unconfirmed"):
                pass


def test_lifecycle_aborts_if_transient_sync_error_returns_none_hash(db: Session) -> None:
    """If reconcile swallows a transient error and returns payload_hash=None, operation aborts."""
    from services.server_lifecycle_service import guardian_recovery_suspension_lease
    from models import Server, Node

    node = Node(id=106, name="node-106", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=94,
        name="TransientSyncErrorTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    def fake_reconcile(_db, _server):
        return {"payload_hash": None, "observed_state": "unknown"}

    with patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=fake_reconcile):
        with pytest.raises(RuntimeError, match="lease sync failed"):
            with guardian_recovery_suspension_lease(db, server, "unit-test-transient"):
                pass


def test_lease_cleanup_failure_does_not_fail_completed_operation(db: Session) -> None:
    """Cleanup is retried by reconciliation and must not mask completed work."""
    from services.server_lifecycle_service import guardian_recovery_suspension_lease
    from models import Server, Node

    node = Node(id=107, name="node-107", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=95,
        name="UnconfirmedRemovalTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    # Der erste Sync bestätigt den Lease, der zweite — die Aufräumung im
    # finally — scheitert am Transport. Genau dieser Fehlschlag fehlte hier
    # bisher: ohne ihn lief der Test den Erfolgspfad ab und hätte einen
    # durchschlagenden Fehler nie bemerkt.
    aufrufe = {"n": 0}

    def fake_reconcile(_db, _server):
        aufrufe["n"] += 1
        if aufrufe["n"] == 1:
            return {
                "payload_hash": "sha256:dummy",
                "recovery_suspension": json.loads(_server.guardian_recovery_suspension),
            }
        raise RuntimeError("guardian sync transport failed")

    block_gelaufen = []
    with patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=fake_reconcile):
        with guardian_recovery_suspension_lease(db, server, "unit-test-removal"):
            block_gelaufen.append(True)

    # Die Operation im Block gilt als abgeschlossen: kein Fehler dringt nach
    # aussen, obwohl die Aufräumung wirklich versucht wurde.
    assert block_gelaufen == [True]
    assert aufrufe["n"] == 2

    # Die dauerhafte Absicht im Panel ist trotzdem gelöscht — nur die
    # Zustellung an den Agenten fehlt, und die holt die Reconciliation nach.
    db.refresh(server)
    assert server.guardian_recovery_suspension is None


def test_restart_server_sync_success(db: Session) -> None:
    """Tests the success path of _restart_server_sync: no exception is raised and status is running."""
    from services.server_lifecycle_service import _restart_server_sync
    from models import Server, Node

    node = Node(id=201, name="node-201", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=201,
        name="RestartSuccessTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    saved_json = [None]

    def fake_reconcile(_db, _server):
        if _server.guardian_recovery_suspension:
            saved_json[0] = _server.guardian_recovery_suspension
        else:
            saved_json[0] = None
        _server.guardian_agent_recovery_suspension_json = saved_json[0]
        return {
            "payload_hash": "sha256:dummy",
            "recovery_suspension": (
                json.loads(_server.guardian_recovery_suspension)
                if _server.guardian_recovery_suspension
                else None
            ),
        }

    # Mock _run_restart to simulate success (which sets status to running)
    def fake_run_restart(_db, _server, _plugin):
        _server.status = "running"
        _db.commit()

    with patch("services.server_lifecycle_service.SessionLocal", return_value=db), \
         patch.object(db, "close", return_value=None), \
         patch("services.server_lifecycle_service.get_plugin", return_value=MagicMock()), \
         patch("services.server_lifecycle_service._run_restart", side_effect=fake_run_restart), \
         patch("services.server_lifecycle_service._append_console_log") as mock_log, \
         patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=fake_reconcile):

        result = _restart_server_sync(server.id)

        assert result == {
            "message": "Restart-Befehl gesendet",
            "status": "running",
        }
        db.refresh(server)
        assert server.status == "running"


def test_restart_server_sync_failure_reraises_original_exception(db: Session) -> None:
    """Tests the failure path of _restart_server_sync: status is set to failed and the original exception is re-raised."""
    from services.server_lifecycle_service import _restart_server_sync
    from models import Server, Node

    node = Node(id=202, name="node-202", host="http://127.0.0.1", status="online", auth_token_enc="test-enc-v1:00:00")
    server = Server(
        id=202,
        name="RestartFailureTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
        node=node,
    )
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    saved_json = [None]

    def fake_reconcile(_db, _server):
        if _server.guardian_recovery_suspension:
            saved_json[0] = _server.guardian_recovery_suspension
        else:
            saved_json[0] = None
        _server.guardian_agent_recovery_suspension_json = saved_json[0]
        return {
            "payload_hash": "sha256:dummy",
            "recovery_suspension": (
                json.loads(_server.guardian_recovery_suspension)
                if _server.guardian_recovery_suspension
                else None
            ),
        }

    # Mock _run_restart to raise a specific exception
    original_error = ValueError("Simulation of restart failure")

    with patch("services.server_lifecycle_service.SessionLocal", return_value=db), \
         patch.object(db, "close", return_value=None), \
         patch("services.server_lifecycle_service.get_plugin", return_value=MagicMock()), \
         patch("services.server_lifecycle_service._run_restart", side_effect=original_error), \
         patch("services.server_lifecycle_service._append_console_log") as mock_log, \
         patch("services.guardian_sync_service.compile_and_sync_desired_state", side_effect=fake_reconcile):

        with pytest.raises(ValueError) as exc_info:
            _restart_server_sync(server.id)

        assert exc_info.value is original_error
        db.refresh(server)
        assert server.status == "failed"
        assert "Simulation of restart failure" in server.status_message
