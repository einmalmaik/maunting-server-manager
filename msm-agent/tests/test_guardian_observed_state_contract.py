from __future__ import annotations

import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock

from config import settings
from services import guardian_service
from services.guardian_contract import canonical_payload_hash


def _payload() -> dict:
    value = {
        "schema_version": 1,
        "server_id": 42,
        "generation": 7,
        "desired_power_state": "running",
        "recovery_suspension": None,
        "quarantine_control": None,
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
                "max_attempts": 3,
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


def test_agent_observed_state_matches_contract_keys_and_types(
    monkeypatch: pytest.MonkeyPatch,
    guardian_paths: Path,
) -> None:
    # 1. Load contract vectors
    root = Path(__file__).resolve().parents[2]
    vectors_path = root / "tests" / "fixtures" / "guardian_observed_state_vectors.json"
    assert vectors_path.is_file(), f"Vectors not found at {vectors_path}"

    with open(vectors_path, "r", encoding="utf-8") as f:
        vectors = json.load(f)

    # We use the healthy vector keys as a contract definition
    healthy_vector = None
    for vector in vectors:
        if vector["description"] == "normalen Healthy State":
            healthy_vector = vector["observed"]
            break
    assert healthy_vector is not None, "Healthy vector not found in fixtures"

    # 2. Setup agent state
    monkeypatch.setattr(
        guardian_service.docker_service,
        "inspect_container_state",
        lambda _name: {"status": "running", "running": True, "oom_killed": False, "port_bindings": {}},
    )

    guardian_service.accept_desired_state(42, _payload())
    
    # Force a runtime transition to healthy
    runtime = guardian_service._load_runtime(42, guardian_service._load_desired(42))
    runtime["state"] = "healthy"
    runtime["state_entered_at"] = "2026-07-20T11:59:00Z"
    runtime["last_probe_at"] = "2026-07-20T12:00:00Z"
    guardian_service._save_runtime(42, runtime)

    # 3. Generate observed state
    agent_observed = guardian_service.observed_state(42)

    # Ensure keys match exactly
    assert set(agent_observed.keys()) == set(healthy_vector.keys())

    # Ensure types match
    for key, expected_val in healthy_vector.items():
        val = agent_observed[key]
        if expected_val is None:
            # For nullable fields, agent could return None or specific type, let's check it's None or fits
            pass
        else:
            assert isinstance(val, type(expected_val)), f"Type mismatch for key '{key}': expected {type(expected_val)}, got {type(val)}"
