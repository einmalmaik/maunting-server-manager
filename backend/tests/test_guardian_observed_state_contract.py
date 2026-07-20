from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from models import Server, Node
from models.server_port import ServerPort
from services.guardian_sync_service import reconcile_guardian_server, GuardianContractError, compile_desired_state
from blueprints.schema import load_blueprint_dict


def _server() -> Server:
    srv = Server(
        id=42,
        name="TestSrv",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="running",
        desired_state_generation=7,  # expected generation is 7
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
    )
    srv.ports = [
        ServerPort(role="game", port=25565, protocol="tcp"),
    ]
    return srv


def test_observed_state_contract_vectors(db: Session) -> None:
    # 1. Load contract vectors
    root = Path(__file__).resolve().parents[2]
    vectors_path = root / "tests" / "fixtures" / "guardian_observed_state_vectors.json"
    assert vectors_path.is_file(), f"Vectors not found at {vectors_path}"

    with open(vectors_path, "r", encoding="utf-8") as f:
        vectors = json.load(f)

    # Compile mock capabilities & blueprint
    client = MagicMock()
    client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    client.get_incidents.return_value = []

    from blueprints.schema import load_blueprint_dict
    
    bp_dict = {
        "version": 1,
        "meta": {
            "id": "minecraft",
            "name": "Minecraft",
            "category": "steam_game",
            "description": "desc",
        },
        "runtime": {
            "image": "ubuntu:latest",
            "startup": "echo",
        },
        "ports": [],
        "source": {
            "type": "dockerOnly",
            "updateStrategy": "none",
        },
        "health": {},
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint

    for vector in vectors:
        desc = vector["description"]
        observed = vector["observed"]

        # Setup Node & Server in DB
        node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
        server = _server()
        server.node = node
        server.id = None
        db.add_all([node, server])
        db.commit()
        db.refresh(server)

        observed["server_id"] = server.id
        observed["supported_schema_version"] = 1
        if "observed_runtime_state" not in observed:
            observed["observed_runtime_state"] = observed["guardian_observed_state"]

        # Set mock return for this vector
        client.get_guardian_state.return_value = observed

        with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
            if desc == "normalen Healthy State":
                reconcile_guardian_server(db, server, node_client=client)
                db.refresh(server)
                assert server.guardian_observed_state == "healthy"
                assert server.guardian_container_status == "running"
                assert server.guardian_probe_timestamp is not None
                assert server.guardian_transition_timestamp is not None
                assert server.guardian_accepted_generation == 7
                assert server.guardian_last_payload_hash == observed["payload_hash"]
                assert server.guardian_last_sync_at is not None
                assert server.guardian_sync_error_statistics is None

            elif desc == "Recovering State mit aktivem Incident":
                reconcile_guardian_server(db, server, node_client=client)
                db.refresh(server)
                assert server.guardian_observed_state == "recovering"
                assert server.guardian_active_incident_uuid == "550e8400-e29b-41d4-a716-446655440000"

            elif desc == "Quarantined State":
                reconcile_guardian_server(db, server, node_client=client)
                db.refresh(server)
                assert server.guardian_observed_state == "quarantined"
                # Canonical JSON matching
                expected_q = json.dumps(observed["quarantine"], sort_keys=True, separators=(",", ":"))
                assert server.guardian_agent_quarantine_json == expected_q

            elif desc == "aktive Recovery Suspension":
                reconcile_guardian_server(db, server, node_client=client)
                db.refresh(server)
                # Canonical JSON matching
                expected_s = json.dumps(observed["recovery_suspension"], sort_keys=True, separators=(",", ":"))
                assert server.guardian_agent_recovery_suspension_json == expected_s

            elif desc == "veraltete akzeptierte Generation":
                # Must raise error
                with pytest.raises(Exception) as excinfo:
                    reconcile_guardian_server(db, server, node_client=client)
                
                db.refresh(server)
                # Observed-State-Daten dürfen trotzdem gespeichert werden
                assert server.guardian_accepted_generation == 6
                # Sync darf aber nicht als erfolgreich gelten -> error saved
                assert server.guardian_sync_error_statistics is not None
                err_data = json.loads(server.guardian_sync_error_statistics)
                assert err_data["code"] == "guardian_generation_mismatch"
                assert err_data["expected_generation"] == 7
                assert err_data["accepted_generation"] == 6

            elif desc == "abweichenden Payload-Hash":
                # Must raise error
                with pytest.raises(Exception) as excinfo:
                    reconcile_guardian_server(db, server, node_client=client)

                db.refresh(server)
                assert server.guardian_sync_error_statistics is not None
                err_data = json.loads(server.guardian_sync_error_statistics)
                assert err_data["code"] == "guardian_payload_hash_mismatch"
                assert err_data["expected_payload_hash"] == "sha256:de0ecd22bcacf8f5d4bf47bc301d40c1d7b589a6dbee33974a4c6ec530ea321e"
                assert err_data["accepted_payload_hash"] == "sha256:0000000000000000000000000000000000000000000000000000000000000000"

        # Cleanup db for next vector
        db.delete(server)
        db.delete(node)
        db.commit()


def test_invalid_guardian_state_rejected(db: Session) -> None:
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = _server()
    server.node = node
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    client = MagicMock()
    client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    client.get_incidents.return_value = []
    
    # invalid observed state
    client.get_guardian_state.return_value = {
        "schema_version": 1,
        "supported_schema_version": 1,
        "server_id": server.id,
        "accepted_generation": 7,
        "payload_hash": "sha256:de0ecd22bcacf8f5d4bf47bc301d40c1d7b589a6dbee33974a4c6ec530ea321e",
        "guardian_observed_state": "invalid_state_here",
        "observed_runtime_state": "invalid_state_here",
        "container_state": "running",
        "last_probe_at": "2026-07-20T12:00:00Z",
        "last_transition_at": "2026-07-20T11:59:00Z",
    }

    plugin = MagicMock()
    from blueprints.schema import load_blueprint_dict
    plugin.get_blueprint.return_value = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "minecraft", "name": "Minecraft", "category": "steam_game", "description": "desc"},
        "runtime": {"image": "ubuntu:latest", "startup": "echo"},
        "ports": [],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {},
    })

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        with pytest.raises(ValueError, match="invalid Guardian observed state"):
            reconcile_guardian_server(db, server, node_client=client)

    db.delete(server)
    db.delete(node)
    db.commit()


def test_invalid_date_rejected(db: Session) -> None:
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = _server()
    server.node = node
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    client = MagicMock()
    client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    client.get_incidents.return_value = []
    
    # invalid date format
    client.get_guardian_state.return_value = {
        "schema_version": 1,
        "supported_schema_version": 1,
        "server_id": server.id,
        "accepted_generation": 7,
        "payload_hash": "sha256:de0ecd22bcacf8f5d4bf47bc301d40c1d7b589a6dbee33974a4c6ec530ea321e",
        "guardian_observed_state": "healthy",
        "observed_runtime_state": "healthy",
        "container_state": "running",
        "last_probe_at": "not-a-date",
        "last_transition_at": "2026-07-20T11:59:00Z",
    }

    plugin = MagicMock()
    from blueprints.schema import load_blueprint_dict
    plugin.get_blueprint.return_value = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "minecraft", "name": "Minecraft", "category": "steam_game", "description": "desc"},
        "runtime": {"image": "ubuntu:latest", "startup": "echo"},
        "ports": [],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {},
    })

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        with pytest.raises(GuardianContractError) as excinfo:
            reconcile_guardian_server(db, server, node_client=client)
        assert excinfo.value.code == "guardian_invalid_observed_timestamp"

    db.delete(server)
    db.delete(node)
    db.commit()


def test_idempotency_and_no_generation_change(db: Session) -> None:
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = _server()
    server.node = node
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    client = MagicMock()
    client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    client.get_incidents.return_value = []
    
    plugin = MagicMock()
    from blueprints.schema import load_blueprint_dict
    plugin.get_blueprint.return_value = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "minecraft", "name": "Minecraft", "category": "steam_game", "description": "desc"},
        "runtime": {"image": "ubuntu:latest", "startup": "echo"},
        "ports": [],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {},
    })

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        
        observed = {
            "schema_version": 1,
            "supported_schema_version": 1,
            "server_id": server.id,
            "accepted_generation": 7,
            "payload_hash": payload["payload_hash"],
            "guardian_observed_state": "healthy",
            "observed_runtime_state": "healthy",
            "container_state": "running",
            "active_incident_uuid": None,
            "last_probe_at": "2026-07-20T12:00:00Z",
            "last_transition_at": "2026-07-20T11:59:00Z",
        }
        client.get_guardian_state.return_value = observed

        # Run 1
        reconcile_guardian_server(db, server, node_client=client)
        db.refresh(server)
        gen_before = server.desired_state_generation
        hash_before = server.guardian_last_payload_hash

        # Run 2
        reconcile_guardian_server(db, server, node_client=client)
        db.refresh(server)
        
        # Idempotent checks
        assert server.desired_state_generation == gen_before
        assert server.guardian_last_payload_hash == hash_before
        assert server.guardian_observed_state == "healthy"

    db.delete(server)
    db.delete(node)
    db.commit()


def test_previous_sync_error_cleared_on_success(db: Session) -> None:
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = _server()
    server.node = node
    server.guardian_sync_error_statistics = json.dumps({"code": "some_error", "timestamp": "..."})
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    client = MagicMock()
    client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    client.get_incidents.return_value = []
    
    plugin = MagicMock()
    from blueprints.schema import load_blueprint_dict
    plugin.get_blueprint.return_value = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "minecraft", "name": "Minecraft", "category": "steam_game", "description": "desc"},
        "runtime": {"image": "ubuntu:latest", "startup": "echo"},
        "ports": [],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {},
    })

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        observed = {
            "schema_version": 1,
            "supported_schema_version": 1,
            "server_id": server.id,
            "accepted_generation": 7,
            "payload_hash": payload["payload_hash"],
            "guardian_observed_state": "healthy",
            "observed_runtime_state": "healthy",
            "container_state": "running",
            "last_probe_at": "2026-07-20T12:00:00Z",
            "last_transition_at": "2026-07-20T11:59:00Z",
        }
        client.get_guardian_state.return_value = observed

        reconcile_guardian_server(db, server, node_client=client)
        db.refresh(server)
        
        # Previous error statistics should be cleared on successful sync
        assert server.guardian_sync_error_statistics is None

    db.delete(server)
    db.delete(node)
    db.commit()


def test_hash_mismatch_does_not_overwrite_desired_payload_hash(db: Session) -> None:
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = _server()
    server.node = node
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    client = MagicMock()
    client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    client.get_incidents.return_value = []
    
    plugin = MagicMock()
    from blueprints.schema import load_blueprint_dict
    plugin.get_blueprint.return_value = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "minecraft", "name": "Minecraft", "category": "steam_game", "description": "desc"},
        "runtime": {"image": "ubuntu:latest", "startup": "echo"},
        "ports": [],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {},
    })

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        server.guardian_last_payload_hash = payload["payload_hash"]
        db.commit()

        observed = {
            "schema_version": 1,
            "supported_schema_version": 1,
            "server_id": server.id,
            "accepted_generation": 7,
            "payload_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
            "guardian_observed_state": "healthy",
            "observed_runtime_state": "healthy",
            "container_state": "running",
            "active_incident_uuid": None,
            "last_probe_at": "2026-07-20T12:00:00Z",
            "last_transition_at": "2026-07-20T11:59:00Z",
        }
        client.get_guardian_state.return_value = observed

        with pytest.raises(Exception):
            reconcile_guardian_server(db, server, node_client=client)

        db.refresh(server)
        
        # Original desired hash stays in last_payload_hash
        assert server.guardian_last_payload_hash == payload["payload_hash"]
        # Mismatched hash is stored in accepted_payload_hash
        assert server.guardian_accepted_payload_hash == "sha256:0000000000000000000000000000000000000000000000000000000000000000"

    db.delete(server)
    db.delete(node)
    db.commit()


def _run_validation_test(db: Session, observed_override: dict, expected_error_code: str) -> None:
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = _server()
    server.node = node
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    client = MagicMock()
    client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    client.get_incidents.return_value = []
    
    plugin = MagicMock()
    from blueprints.schema import load_blueprint_dict
    plugin.get_blueprint.return_value = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "minecraft", "name": "Minecraft", "category": "steam_game", "description": "desc"},
        "runtime": {"image": "ubuntu:latest", "startup": "echo"},
        "ports": [],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {},
    })

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        server.guardian_last_payload_hash = payload["payload_hash"]
        db.commit()

        observed = {
            "schema_version": 1,
            "supported_schema_version": 1,
            "server_id": server.id,
            "accepted_generation": 7,
            "payload_hash": payload["payload_hash"],
            "guardian_observed_state": "healthy",
            "observed_runtime_state": "healthy",
            "container_state": "running",
            "active_incident_uuid": None,
            "last_probe_at": "2026-07-20T12:00:00Z",
            "last_transition_at": "2026-07-20T11:59:00Z",
            "quarantine": None,
            "recovery_suspension": None,
        }
        observed.update(observed_override)
        
        # Remove keys if their override is explicitly set to a special token
        for k, v in list(observed.items()):
            if v == "__DELETE__":
                del observed[k]

        client.get_guardian_state.return_value = observed

        with pytest.raises(GuardianContractError) as excinfo:
            reconcile_guardian_server(db, server, node_client=client)
        assert excinfo.value.code == expected_error_code

    db.delete(server)
    db.delete(node)
    db.commit()


def test_wrong_server_id_is_rejected(db: Session) -> None:
    _run_validation_test(db, {"server_id": 999}, "guardian_server_id_mismatch")

def test_wrong_schema_version_is_rejected(db: Session) -> None:
    _run_validation_test(db, {"schema_version": 99}, "guardian_schema_version_mismatch")

def test_wrong_supported_schema_version_is_rejected(db: Session) -> None:
    _run_validation_test(db, {"supported_schema_version": 99}, "guardian_schema_version_mismatch")

def test_missing_guardian_observed_state_is_rejected(db: Session) -> None:
    _run_validation_test(db, {"guardian_observed_state": "__DELETE__"}, "guardian_missing_observed_field")

def test_missing_container_state_is_rejected(db: Session) -> None:
    _run_validation_test(db, {"container_state": "__DELETE__"}, "guardian_missing_observed_field")

def test_conflicting_observed_states_are_rejected(db: Session) -> None:
    _run_validation_test(db, {"guardian_observed_state": "healthy", "observed_runtime_state": "degraded"}, "guardian_observed_state_mismatch")

def test_invalid_payload_hash_format_is_rejected(db: Session) -> None:
    _run_validation_test(db, {"payload_hash": "invalid_hash"}, "guardian_invalid_observed_field")
    _run_validation_test(db, {"payload_hash": "sha256:SHORT"}, "guardian_invalid_observed_field")

def test_naive_timestamp_is_rejected(db: Session) -> None:
    _run_validation_test(db, {"last_transition_at": "2026-07-20T12:00:00"}, "guardian_invalid_observed_timestamp")
