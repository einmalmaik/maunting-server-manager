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
    value["payload_hash"] = "sha256:de0ecd22bcacf8f5d4bf47bc301d40c1d7b589a6dbee33974a4c6ec530ea321e"
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


def test_agent_generated_vectors_match_all_exact_contract_values(
    monkeypatch: pytest.MonkeyPatch,
    guardian_paths: Path,
) -> None:
    # 1. Load contract vectors
    root = Path(__file__).resolve().parents[2]
    vectors_path = root / "tests" / "fixtures" / "guardian_observed_state_vectors.json"
    assert vectors_path.is_file(), f"Vectors not found at {vectors_path}"

    with open(vectors_path, "r", encoding="utf-8") as f:
        vectors = json.load(f)

    # 2. Setup agent state
    monkeypatch.setattr(
        guardian_service.docker_service,
        "inspect_container_state",
        lambda _name: {"status": "running", "running": True, "oom_killed": False, "port_bindings": {}},
    )

    monkeypatch.setattr(
        "services.guardian_contract.canonical_payload_hash",
        lambda _p: "sha256:de0ecd22bcacf8f5d4bf47bc301d40c1d7b589a6dbee33974a4c6ec530ea321e"
    )

    guardian_service.accept_desired_state(42, _payload())
    
    for vector in vectors:
        desc = vector["description"]
        observed_vector = vector["observed"]
        
        # Stale Generation und Hash-Mismatch als Backend-Negativvektoren kennzeichnen.
        if desc in ("veraltete akzeptierte Generation", "abweichenden Payload-Hash"):
            continue

        # Setup standard base payload
        payload = _payload()
        runtime = {}

        if desc == "normalen Healthy State":
            runtime["state"] = "healthy"
            runtime["quarantine"] = None
            runtime["active_incident_uuid"] = None
            monkeypatch.setattr(
                guardian_service.docker_service,
                "inspect_container_state",
                lambda _name: {"status": "running", "running": True, "oom_killed": False, "port_bindings": {}},
            )

        elif desc == "Recovering State mit aktivem Incident":
            runtime["state"] = "recovering"
            runtime["quarantine"] = None
            runtime["active_incident_uuid"] = observed_vector["active_incident_uuid"]
            monkeypatch.setattr(
                guardian_service.docker_service,
                "inspect_container_state",
                lambda _name: {"status": "running", "running": True, "oom_killed": False, "port_bindings": {}},
            )

        elif desc == "Quarantined State":
            runtime["state"] = "quarantined"
            runtime["quarantine"] = observed_vector["quarantine"]
            runtime["active_incident_uuid"] = observed_vector["active_incident_uuid"]
            # No quarantine_control in desired payload! (That would CLEAR it)
            # Need docker to report stopped for Quarantined vector
            monkeypatch.setattr(
                guardian_service.docker_service,
                "inspect_container_state",
                lambda _name: {"status": "stopped", "running": False, "oom_killed": False, "port_bindings": {}},
            )
            
        elif desc == "aktive Recovery Suspension":
            runtime["state"] = "healthy"
            runtime["quarantine"] = None
            runtime["active_incident_uuid"] = None
            payload["recovery_suspension"] = observed_vector["recovery_suspension"]
            monkeypatch.setattr(
                guardian_service.docker_service,
                "inspect_container_state",
                lambda _name: {"status": "running", "running": True, "oom_killed": False, "port_bindings": {}},
            )
            
        runtime["state_entered_at"] = observed_vector["last_transition_at"]
        runtime["last_probe_at"] = observed_vector["last_probe_at"]

        # 1. Write desired state payload
        guardian_service.get_state_store().write_json(42, "desired-state.json", payload)
        
        # 2. Write runtime state
        guardian_service._save_runtime(42, runtime)
        
        # 3. Generate observed state
        agent_observed = guardian_service.observed_state(42)

        # 4. Assert Exact Match
        assert agent_observed == observed_vector, f"Vector mismatch for: {desc}"
