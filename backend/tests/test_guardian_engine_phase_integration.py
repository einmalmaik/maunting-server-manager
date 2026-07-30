from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from blueprints.schema import load_blueprint_dict
from models import Incident, Server
from models.server_port import ServerPort
from services.guardian_runtime_compiler import (
    GuardianCompileError,
    canonical_payload_hash,
    compile_desired_state,
    validate_agent_capabilities,
)
from services.guardian_state_service import (
    ensure_guardian_config_generation,
    set_desired_power_state,
)
from services.guardian_sync_service import (
    ingest_incidents_and_ack,
    reconcile_guardian_server,
)


def _valid_blueprint_dict() -> dict:
    return {
        "version": 1,
        "meta": {
            "id": "guardian_phase_integration_test",
            "name": "Guardian Integration Test Blueprint",
            "category": "bot",
            "description": "Integration test blueprint for Guardian Engine phases",
        },
        "runtime": {
            "image": "synthetic.invalid/runtime:1",
            "startup": "./start_game",
            "env": {},
        },
        "ports": [
            {"name": "game", "protocol": "tcp"},
            {"name": "query", "protocol": "udp"},
        ],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {
            "process": {"required": True, "id": "process"},
            "port": {
                "id": "game-tcp-port",
                "protocol": "tcp",
                "port": "{{SERVER_PORT}}",
                "timeout": "5s",
                "interval": "10s",
            },
            "application": {
                "id": "app-query",
                "type": "minecraft-query",
                "port": "{{PORT:query}}",
                "interval": "15s",
                "timeout": "3s",
            },
            "startup": {
                "grace_period_seconds": 10,
                "timeout_seconds": 120,
            },
        },
        "diagnostics": {"parsers": ["linux-oom", "port-conflict"]},
        "recovery": {
            "policies": [
                {"match": "linux-oom", "action": "graceful_restart"},
                {"match": "port-conflict", "action": "clear_declared_lock_files"},
            ],
            "safe_lock_files": [
                {"path": "runtime/server.lock", "reason": "synthetic lock"}
            ],
        },
    }


def _create_test_server(db: Session, *, desired: str = "running", generation: int = 1) -> Server:
    server = Server(
        name="Phase-Integration-Server",
        game_type="guardian_phase_integration_test",
        install_dir="/synthetic/guardian/integration",
        status="stopped",
        desired_power_state=desired,
        desired_state_generation=generation,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
    )
    server.ports = [
        ServerPort(role="game", port=25565, protocol="tcp"),
        ServerPort(role="query", port=25566, protocol="udp"),
    ]
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


# ============================================================================
# PHASE 1: Blueprint Compiler & Schema Validation (Positive & Negative)
# ============================================================================

def test_phase1_compiler_positive_compilation_and_canonical_hashing() -> None:
    """Verifies that a valid blueprint compiles correctly into a desired_state payload with a stable canonical hash."""
    bp = load_blueprint_dict(_valid_blueprint_dict())
    server = Server(
        id=101,
        name="TestServer",
        game_type="guardian_phase_integration_test",
        install_dir="/synthetic/path",
        status="running",
        desired_power_state="running",
        desired_state_generation=5,
        public_bind_ip="127.0.0.1",
    )
    server.ports = [
        ServerPort(role="game", port=25565, protocol="tcp"),
        ServerPort(role="query", port=25566, protocol="udp"),
    ]

    payload = compile_desired_state(server, bp)

    # Verify structural integrity of compiled payload
    assert payload["schema_version"] == 1
    assert payload["server_id"] == 101
    assert payload["generation"] == 5
    assert payload["desired_power_state"] == "running"
    assert "payload_hash" in payload
    assert payload["payload_hash"] == canonical_payload_hash(payload)

    # Verify key deterministic property of canonical_payload_hash:
    # Modifying any field MUST change the payload hash
    tampered_payload = dict(payload)
    tampered_payload["desired_power_state"] = "stopped"
    assert canonical_payload_hash(tampered_payload) != payload["payload_hash"]


def test_phase1_compiler_negative_unresolved_placeholder_rejection() -> None:
    """Negative Test: Unresolved blueprint placeholder MUST raise GuardianCompileError."""
    bp_dict = _valid_blueprint_dict()
    bp_dict["health"]["application"]["port"] = "{{PORT:nonexistent_role}}"
    bp = load_blueprint_dict(bp_dict)

    server = Server(
        id=102,
        name="TestServer",
        game_type="guardian_phase_integration_test",
        install_dir="/synthetic/path",
        status="stopped",
        desired_power_state="running",
        desired_state_generation=1,
        public_bind_ip="127.0.0.1",
    )
    server.ports = [ServerPort(role="game", port=25565, protocol="tcp")]

    with pytest.raises(GuardianCompileError) as exc_info:
        compile_desired_state(server, bp)

    assert exc_info.value.code == "unresolved_placeholder"
    assert "nonexistent_role" in str(exc_info.value)


def test_phase1_compiler_negative_missing_bind_ip_rejection() -> None:
    """Negative Test: Missing public bind IP MUST reject compilation for network probes."""
    bp = load_blueprint_dict(_valid_blueprint_dict())
    server = Server(
        id=103,
        name="TestServer",
        game_type="guardian_phase_integration_test",
        install_dir="/synthetic/path",
        status="stopped",
        desired_power_state="running",
        desired_state_generation=1,
        public_bind_ip=None,  # Unavailable
    )
    server.ports = [
        ServerPort(role="game", port=25565, protocol="tcp"),
        ServerPort(role="query", port=25566, protocol="udp"),
    ]

    with pytest.raises(GuardianCompileError) as exc_info:
        compile_desired_state(server, bp)

    assert exc_info.value.code == "probe_target_unavailable"


def test_phase1_capability_validation_negative_rejects_unsupported() -> None:
    """Negative Test: Agent capability mismatch MUST list all unsupported items."""
    bp = load_blueprint_dict(_valid_blueprint_dict())
    server = Server(
        id=104,
        name="TestServer",
        game_type="guardian_phase_integration_test",
        install_dir="/synthetic/path",
        status="running",
        desired_power_state="running",
        desired_state_generation=1,
        public_bind_ip="127.0.0.1",
    )
    server.ports = [
        ServerPort(role="game", port=25565, protocol="tcp"),
        ServerPort(role="query", port=25566, protocol="udp"),
    ]
    payload = compile_desired_state(server, bp)

    # Crippled Agent capabilities
    crippled_agent = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],  # Missing 'tcp' and 'minecraft-query'
        "diagnostic_parsers": [],    # Missing 'linux-oom' and 'port-conflict'
        "recovery_actions": [],      # Missing 'graceful_restart' and 'clear_declared_lock_files'
    }

    with pytest.raises(GuardianCompileError) as exc_info:
        validate_agent_capabilities(payload, crippled_agent)

    unsupported = exc_info.value.details["unsupported"]
    assert "tcp" in unsupported["probe_types"]
    assert "minecraft-query" in unsupported["probe_types"]
    assert "linux-oom" in unsupported["diagnostic_parsers"]
    assert "port-conflict" in unsupported["diagnostic_parsers"]
    assert "graceful_restart" in unsupported["recovery_actions"]
    assert "clear_declared_lock_files" in unsupported["recovery_actions"]


# ============================================================================
# PHASE 2: Generation Contract & Reconciliation
# ============================================================================

def test_phase2_generation_increments_only_on_intent_change(db: Session) -> None:
    """Verifies generation contract: generation increments when power state or config changes, not on redundant calls."""
    server = _create_test_server(db, desired="stopped", generation=1)

    # 1. State change from stopped -> running (Intent change -> Generation MUST increment)
    changed = set_desired_power_state(db, server, "running")
    assert changed is True
    assert server.desired_power_state == "running"
    assert server.desired_state_generation == 2

    # 2. Duplicate state set (No change -> Generation MUST NOT increment)
    changed_again = set_desired_power_state(db, server, "running")
    assert changed_again is False
    assert server.desired_state_generation == 2

    # 3. State change from running -> stopped
    changed_stop = set_desired_power_state(db, server, "stopped")
    assert changed_stop is True
    assert server.desired_state_generation == 3


def test_phase2_config_hash_change_increments_generation(db: Session) -> None:
    """Verifies that changing effective configuration (ports/blueprint) increments desired_state_generation."""
    server = _create_test_server(db, generation=10)
    bp_dict = _valid_blueprint_dict()
    bp = load_blueprint_dict(bp_dict)

    # First run computes hash
    assert ensure_guardian_config_generation(db, server, bp) is False
    assert server.desired_state_generation == 10

    # Modify port configuration
    game_port = next(p for p in server.ports if p.role == "game")
    game_port.port = 25570
    db.commit()

    # Re-evaluating MUST detect config hash change and increment generation
    assert ensure_guardian_config_generation(db, server, bp) is True
    assert server.desired_state_generation == 11

    # Second run without changes MUST retain generation 11
    assert ensure_guardian_config_generation(db, server, bp) is False
    assert server.desired_state_generation == 11


# ============================================================================
# PHASE 3: Incident Ingestion, Deduplication & ACKs (Positive & Negative)
# ============================================================================

def test_phase3_incident_ingestion_idempotency_and_quarantine_sync(db: Session) -> None:
    """Verifies incident ingestion deduplicates by UUID, updates DB, sends ACK to Agent, and syncs quarantine status."""
    server = _create_test_server(db)
    incident_uuid = str(uuid.uuid4())
    mock_node_client = MagicMock()

    incident_payload = {
        "uuid": incident_uuid,
        "server_id": server.id,
        "created_at": "2026-07-30T12:00:00Z",
        "updated_at": "2026-07-30T12:00:01Z",
        "type": "recovery_failed",
        "status": "quarantined",
        "fingerprint": f"guardian:{server.id}:quarantine",
        "payload": {
            "schema_version": 1,
            "message": "Max recovery attempts exhausted",
            "reason": "OOM_REPEATED_CRASH",
            "attempts": [{"attempt": 3, "result": "failed"}],
        },
    }

    # 1. Ingest incident first time
    acked = ingest_incidents_and_ack(db, server, mock_node_client, "msm-srv-1", [incident_payload])
    assert acked == [incident_uuid]
    mock_node_client.acknowledge_incidents.assert_called_once_with("msm-srv-1", [incident_uuid])

    # Check DB record
    db_incident = db.query(Incident).filter(Incident.uuid == incident_uuid).first()
    assert db_incident is not None
    assert db_incident.status == "quarantined"
    assert db_incident.type == "recovery_failed"

    # 2. Ingest same incident second time (Deduplication / Idempotency test)
    mock_node_client.reset_mock()
    acked_second = ingest_incidents_and_ack(db, server, mock_node_client, "msm-srv-1", [incident_payload])
    assert acked_second == [incident_uuid]
    # ACK is sent, but count in DB remains 1
    assert db.query(Incident).filter(Incident.uuid == incident_uuid).count() == 1


def test_phase3_incident_ingestion_negative_handles_client_ack_exception(db: Session) -> None:
    """Negative Test: Node client ACK failure raises exception but DB record MUST persist cleanly."""
    server = _create_test_server(db)
    incident_uuid = str(uuid.uuid4())
    mock_node_client = MagicMock()
    mock_node_client.acknowledge_incidents.side_effect = Exception("Network timeout during ACK")

    incident_payload = {
        "uuid": incident_uuid,
        "server_id": server.id,
        "created_at": "2026-07-30T12:00:00Z",
        "updated_at": "2026-07-30T12:00:01Z",
        "type": "probe_failed",
        "status": "open",
        "fingerprint": f"guardian:{server.id}:probe_failed",
        "payload": {
            "schema_version": 1,
            "message": "Probe failed",
        },
    }

    # Exception during ACK is raised after DB commit
    with pytest.raises(Exception, match="Network timeout during ACK"):
        ingest_incidents_and_ack(db, server, mock_node_client, "msm-srv-1", [incident_payload])

    # Incident MUST still be persisted in DB despite the subsequent ACK network failure
    assert db.query(Incident).filter(Incident.uuid == incident_uuid).count() == 1
