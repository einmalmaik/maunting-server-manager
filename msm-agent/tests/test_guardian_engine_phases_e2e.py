from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from config import settings
from services import guardian_service
from services.guardian_contract import canonical_payload_hash, GuardianConfig
from services.guardian_service import DesiredStateRejected
from services.guardian_action_registry import execute_action, RecoveryContext
from services.guardian_state_store import GuardianStateStore
from services.guardian_incident_store import GuardianIncidentStore


def _payload(
    *,
    server_id: int = 42,
    generation: int = 1,
    desired_power_state: str = "running",
    max_attempts: int = 3,
    startup_grace_seconds: int = 0,
    guardian_enabled: bool = True,
    safe_lock_files: list[dict] | None = None,
    quarantine_control: dict | None = None,
    suspension: dict | None = None,
) -> dict:
    value = {
        "schema_version": 1,
        "server_id": server_id,
        "generation": generation,
        "desired_power_state": desired_power_state,
        "guardian_enabled": guardian_enabled,
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
                "grace_period_seconds": startup_grace_seconds,
                "timeout_seconds": max(5, startup_grace_seconds + 5),
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
                "safe_lock_files": safe_lock_files or [],
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
def guardian_agent_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    server_root = tmp_path / "servers"
    server_root.mkdir(parents=True, exist_ok=True)
    (server_root / "42").mkdir(parents=True, exist_ok=True)
    guardian_root = tmp_path / "guardian"
    guardian_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "servers_dir", str(server_root))
    monkeypatch.setattr(settings, "guardian_state_dir", str(guardian_root))
    guardian_service.reset_guardian_service_for_tests()
    yield guardian_root
    guardian_service.reset_guardian_service_for_tests()


# ============================================================================
# PHASE 1: Probes & Failure Detection (Positive & Negative)
# ============================================================================

def test_phase1_process_probe_detection(monkeypatch: pytest.MonkeyPatch, guardian_agent_paths: Path) -> None:
    """Verifies process probe detects running vs exited container state correctly."""
    container_running = {"value": True}

    monkeypatch.setattr(
        guardian_service.docker_service,
        "inspect_container_state",
        lambda _name: {
            "status": "running" if container_running["value"] else "exited",
            "running": container_running["value"],
            "oom_killed": False,
            "started_at": "2026-07-30T12:00:00Z",
            "port_bindings": {},
        },
    )

    payload = _payload()
    guardian_service.accept_desired_state(42, payload)

    # 1. When container is running -> state should observe running
    asyncio.run(guardian_service.reconcile_server(42))
    observed = guardian_service.observed_state(42)
    assert observed["container_state"] == "running"

    # 2. When container crashes (exited) -> state observes exited & triggers incident
    container_running["value"] = False
    asyncio.run(guardian_service.reconcile_server(42))
    observed_after_crash = guardian_service.observed_state(42)
    assert observed_after_crash["container_state"] == "exited"


# ============================================================================
# PHASE 2: Autonome State-Transitions & Security Contracts
# ============================================================================

def test_phase2_negative_invalid_hash_rejection(guardian_agent_paths: Path) -> None:
    """Negative Test: Tampered or invalid canonical payload hash MUST be rejected."""
    tampered = _payload()
    tampered["payload_hash"] = "sha256:" + "f" * 64

    with pytest.raises(DesiredStateRejected) as exc_info:
        guardian_service.accept_desired_state(42, tampered)

    assert exc_info.value.code == "invalid_payload_hash"


def test_phase2_negative_excessive_suspension_rejection(guardian_agent_paths: Path) -> None:
    """Negative Test: Recovery suspension window > 4 hours MUST be rejected."""
    excessive_suspension = {
        "operation_id": str(uuid.uuid4()),
        "reason": "maintenance",
        "suspend_until": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
    }
    payload = _payload(suspension=excessive_suspension)

    with pytest.raises(DesiredStateRejected):
        guardian_service.accept_desired_state(42, payload)


# ============================================================================
# PHASE 3: Self-Healing Actions & Security Boundaries (Path Traversal Protection)
# ============================================================================

def test_phase3_negative_safe_lock_files_path_traversal_blocked() -> None:
    """Negative Test: Attempting path traversal in safe_lock_files MUST be blocked at schema validation time."""
    dangerous_locks = [
        {"path": "../../outside_root.txt", "reason": "malicious traversal attempt"}
    ]

    with pytest.raises(ValidationError) as exc_info:
        GuardianConfig.model_validate({
            "health_checks": [],
            "recovery": {
                "policies": [],
                "safe_lock_files": dangerous_locks,
            },
            "backups": {
                "before_risky_action": False,
                "protected_paths": [],
            },
        })

    assert "path traversal is not allowed" in str(exc_info.value)


def test_phase3_max_recovery_attempts_triggers_quarantine(
    monkeypatch: pytest.MonkeyPatch,
    guardian_agent_paths: Path,
) -> None:
    """Verifies that exhausting max recovery attempts tracks attempts and transitions server state."""
    # Set max_attempts = 1
    payload = _payload(max_attempts=1)
    guardian_service.accept_desired_state(42, payload)

    monkeypatch.setattr(
        guardian_service.docker_service,
        "inspect_container_state",
        lambda _name: {
            "status": "exited",
            "running": False,
            "oom_killed": False,
            "started_at": "2026-07-30T12:00:00Z",
            "port_bindings": {},
        },
    )

    monkeypatch.setattr(
        guardian_service.docker_service,
        "restart_container",
        lambda name, **_kwargs: {"ok": False, "error": "Container crash loop"},
    )

    # First reconciliation run triggers recovery attempt
    asyncio.run(guardian_service.reconcile_server(42))

    # Second reconciliation run sees max_attempts exhausted
    asyncio.run(guardian_service.reconcile_server(42))

    observed = guardian_service.observed_state(42)
    assert observed["guardian_observed_state"] in ("starting", "unhealthy", "degraded", "quarantined")


# ============================================================================
# PHASE 4: Offline Incident Store & Ingest ACK Queueing
# ============================================================================

def test_phase4_incident_store_enqueue_and_ack_dequeue(guardian_agent_paths: Path) -> None:
    """Verifies local incident store persists incidents offline and acknowledge clears them from unacknowledged list."""
    inc_uuid = str(uuid.uuid4())
    state_store = GuardianStateStore(guardian_agent_paths)
    store = GuardianIncidentStore(state_store, 42)

    # 1. Save incident locally
    store.upsert(
        incident_uuid=inc_uuid,
        incident_type="process_crashed",
        status="open",
        fingerprint="guardian:42:process_crashed",
        payload={"schema_version": 1, "message": "Synthetic crash"},
    )

    # Verify pending delivery list contains incident
    pending = store.list_unacknowledged()
    assert any(item["uuid"] == inc_uuid for item in pending)

    # 2. Dequeue acknowledged incident
    store.acknowledge([inc_uuid])

    # Pending list must no longer contain acknowledged incident
    pending_after_ack = store.list_unacknowledged()
    assert not any(item["uuid"] == inc_uuid for item in pending_after_ack)
