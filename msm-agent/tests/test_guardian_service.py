from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from config import settings
from services import guardian_service
from services.guardian_contract import canonical_payload_hash
from services.guardian_service import DesiredStateRejected


def _payload(
    *,
    generation: int = 1,
    desired_power_state: str = "running",
    max_attempts: int = 3,
    quarantine_control: dict | None = None,
    suspension: dict | None = None,
) -> dict:
    value = {
        "schema_version": 1,
        "server_id": 42,
        "generation": generation,
        "desired_power_state": desired_power_state,
        "recovery_suspension": suspension,
        "quarantine_control": quarantine_control,
        "guardian": {
            "health_checks": [
                {
                    "check_id": "process",
                    "type": "process",
                    "interval_seconds": 1,
                    "timeout_seconds": 1,
                    "failure_threshold": 1,
                    "success_threshold": 1,
                    "required_for_startup": True,
                    "required_for_verification": True,
                }
            ],
            "startup": {
                "grace_period_seconds": 0,
                "timeout_seconds": 5,
                "success_patterns": [],
                "failure_patterns": [],
            },
            "verification": {
                "minimum_healthy_duration_seconds": 0,
                "required_consecutive_successes": 1,
                "verification_timeout_seconds": 5,
            },
            "logs": {"sources": [], "redact": [], "max_tail_bytes": 4096},
            "diagnostics": {"parsers": ["linux-oom"]},
            "recovery": {
                "policies": [{"match": "process_not_running", "action": "restart"}],
                "safe_lock_files": [],
                "max_attempts": max_attempts,
                "attempt_window_seconds": 1800,
                "cooldown_seconds": 1,
            },
            "backups": {"before_risky_action": True, "protected_paths": []},
        },
    }
    value["payload_hash"] = canonical_payload_hash(value)
    return value


@pytest.fixture()
def guardian_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    server_root = tmp_path / "servers"
    server_root.mkdir()
    (server_root / "42").mkdir()
    guardian_root = tmp_path / "guardian"
    monkeypatch.setattr(settings, "servers_dir", str(server_root))
    monkeypatch.setattr(settings, "guardian_state_dir", str(guardian_root))
    guardian_service.reset_guardian_service_for_tests()
    yield guardian_root
    guardian_service.reset_guardian_service_for_tests()


def test_generation_and_hash_acceptance_rules(guardian_paths: Path) -> None:
    first = _payload(generation=2)
    assert guardian_service.accept_desired_state(42, first)["result"] == "updated"
    assert guardian_service.accept_desired_state(42, first)["result"] == "unchanged"

    stale = _payload(generation=1)
    with pytest.raises(DesiredStateRejected) as stale_error:
        guardian_service.accept_desired_state(42, stale)
    assert stale_error.value.code == "stale_generation"

    conflict = _payload(generation=2, desired_power_state="stopped")
    with pytest.raises(DesiredStateRejected) as conflict_error:
        guardian_service.accept_desired_state(42, conflict)
    assert conflict_error.value.code == "generation_conflict"


def test_invalid_hash_and_unresolved_placeholder_are_rejected(guardian_paths: Path) -> None:
    invalid = _payload()
    invalid["payload_hash"] = "sha256:" + "0" * 64
    with pytest.raises(DesiredStateRejected) as hash_error:
        guardian_service.accept_desired_state(42, invalid)
    assert hash_error.value.code == "invalid_payload_hash"

    unresolved = _payload()
    unresolved["guardian"]["health_checks"][0]["target_host"] = "{{PORT:game}}"
    unresolved["payload_hash"] = canonical_payload_hash(unresolved)
    with pytest.raises(DesiredStateRejected) as token_error:
        guardian_service.accept_desired_state(42, unresolved)
    assert token_error.value.code == "unresolved_placeholder"


def test_suspension_expiry_and_bound_are_validated(guardian_paths: Path) -> None:
    operation_id = str(uuid.uuid4())
    active = {
        "operation_id": operation_id,
        "reason": "server_update",
        "suspend_until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    guardian_service.accept_desired_state(42, _payload(suspension=active))
    desired = guardian_service._load_desired(42)
    assert desired is not None and guardian_service._suspension_active(desired)

    too_long = {
        **active,
        "suspend_until": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
    }
    with pytest.raises(DesiredStateRejected):
        guardian_service.accept_desired_state(42, _payload(generation=2, suspension=too_long))


def test_startup_health_recovery_and_verification(monkeypatch: pytest.MonkeyPatch, guardian_paths: Path) -> None:
    runtime_state = {"running": True}

    def inspect(_name: str) -> dict:
        return {
            "status": "running" if runtime_state["running"] else "exited",
            "running": runtime_state["running"],
            "oom_killed": False,
            "port_bindings": {},
        }

    def restart(_name: str, timeout: int | None = None) -> dict:
        runtime_state["running"] = True
        return {"ok": True}

    monkeypatch.setattr(guardian_service.docker_service, "inspect_container_state", inspect)
    monkeypatch.setattr(guardian_service.docker_service, "restart_container", restart)
    guardian_service.accept_desired_state(42, _payload())

    asyncio.run(guardian_service.reconcile_server(42))
    assert guardian_service.observed_state(42)["guardian_observed_state"] == "healthy"

    runtime_state["running"] = False
    runtime = guardian_service._load_runtime(42, guardian_service._load_desired(42))
    runtime["probe_states"]["process"]["next_run_at"] = "2000-01-01T00:00:00Z"
    guardian_service._save_runtime(42, runtime)
    asyncio.run(guardian_service.reconcile_server(42))
    assert guardian_service.observed_state(42)["guardian_observed_state"] == "verifying"
    incidents = guardian_service.list_incidents(42)
    assert len(incidents) == 1
    assert incidents[0]["status"] == "verifying"

    asyncio.run(guardian_service.reconcile_server(42))
    assert guardian_service.observed_state(42)["guardian_observed_state"] == "healthy"
    resolved = guardian_service.list_incidents(42)
    assert len(resolved) == 1
    assert resolved[0]["status"] == "resolved"


def test_restart_success_does_not_resolve_an_unhealthy_service(
    monkeypatch: pytest.MonkeyPatch,
    guardian_paths: Path,
) -> None:
    running = {"value": True}

    def inspect(_name: str) -> dict:
        return {
            "status": "running" if running["value"] else "exited",
            "running": running["value"],
            "oom_killed": False,
            "port_bindings": {},
        }

    monkeypatch.setattr(guardian_service.docker_service, "inspect_container_state", inspect)
    monkeypatch.setattr(
        guardian_service.docker_service,
        "restart_container",
        lambda *_args, **_kwargs: {"ok": True},
    )
    guardian_service.accept_desired_state(42, _payload())
    asyncio.run(guardian_service.reconcile_server(42))
    running["value"] = False
    runtime = guardian_service._load_runtime(42, guardian_service._load_desired(42))
    runtime["probe_states"]["process"]["next_run_at"] = "2000-01-01T00:00:00Z"
    guardian_service._save_runtime(42, runtime)
    asyncio.run(guardian_service.reconcile_server(42))
    assert guardian_service.observed_state(42)["guardian_observed_state"] == "verifying"
    asyncio.run(guardian_service.reconcile_server(42))
    assert guardian_service.observed_state(42)["guardian_observed_state"] == "verifying"
    assert guardian_service.list_incidents(42)[0]["status"] == "verifying"


def test_quarantine_is_durable_and_new_generation_can_clear_it(
    monkeypatch: pytest.MonkeyPatch,
    guardian_paths: Path,
) -> None:
    monkeypatch.setattr(
        guardian_service.docker_service,
        "inspect_container_state",
        lambda _name: {"status": "exited", "running": False, "oom_killed": False, "port_bindings": {}},
    )
    monkeypatch.setattr(
        guardian_service.docker_service,
        "restart_container",
        lambda *_args, **_kwargs: {"ok": False},
    )
    guardian_service.accept_desired_state(42, _payload(max_attempts=1))
    runtime = guardian_service._load_runtime(42, guardian_service._load_desired(42))
    runtime["state"] = "unhealthy"
    runtime["active_incident_type"] = "process_not_running"
    runtime["attempts"] = [{"at": datetime.now(timezone.utc).isoformat(), "result": "failed"}]
    guardian_service._save_runtime(42, runtime)
    asyncio.run(guardian_service.reconcile_server(42))
    assert guardian_service.observed_state(42)["guardian_observed_state"] == "quarantined"

    guardian_service.reset_guardian_service_for_tests()
    assert guardian_service.observed_state(42)["guardian_observed_state"] == "quarantined"

    clear_id = str(uuid.uuid4())
    guardian_service.accept_desired_state(
        42,
        _payload(
            generation=2,
            quarantine_control={"clear": True, "operation_id": clear_id},
        ),
    )
    assert guardian_service.observed_state(42)["guardian_observed_state"] == "starting"


def test_incident_acknowledgement_is_partial_and_idempotent(guardian_paths: Path) -> None:
    guardian_service.accept_desired_state(42, _payload())
    store = guardian_service.GuardianIncidentStore(guardian_service.get_state_store(), 42)
    first = store.create(
        incident_type="synthetic",
        status="open",
        fingerprint="one",
        payload={"schema_version": 1, "message": "synthetic", "attempts": []},
    )["uuid"]
    second = store.create(
        incident_type="synthetic",
        status="open",
        fingerprint="two",
        payload={"schema_version": 1, "message": "synthetic", "attempts": []},
    )["uuid"]
    assert guardian_service.acknowledge_incidents(42, [first]) == [first]
    assert [row["uuid"] for row in guardian_service.list_incidents(42)] == [second]
    assert guardian_service.acknowledge_incidents(42, [first]) == [first]
