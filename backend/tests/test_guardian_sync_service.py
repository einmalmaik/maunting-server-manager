from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

# Load validate_desired_state dynamically from the agent module to avoid namespace collision on 'services'
import sys
root = Path(__file__).resolve().parents[2]
contract_path = root / "msm-agent" / "services" / "guardian_contract.py"
spec = importlib.util.spec_from_file_location("guardian_contract", contract_path)
guardian_contract = importlib.util.module_from_spec(spec)
sys.modules["guardian_contract"] = guardian_contract
spec.loader.exec_module(guardian_contract)
validate_desired_state = guardian_contract.validate_desired_state

from blueprints.schema import load_blueprint_dict
from models import Server, Node
from models.server_port import ServerPort
from services.guardian_runtime_compiler import GuardianCompileError
from services.guardian_state_service import ensure_guardian_config_generation, set_desired_power_state
from services.guardian_sync_service import compile_desired_state


def _server() -> Server:
    srv = Server(
        id=42,
        name="TestSrv",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="running",
        desired_state_generation=1,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
    )
    srv.ports = [
        ServerPort(role="game", port=25565, protocol="tcp"),
        ServerPort(role="query", port=25566, protocol="udp"),
        ServerPort(role="web", port=8080, protocol="tcp"),
    ]
    return srv


def _base_blueprint_dict() -> dict:
    return {
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


def test_minimal_process_blueprint(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "process": {
            "required": True,
            "id": "proc-check",
            "interval": "10s",
        }
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint

    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
    assert payload["schema_version"] == 1
    assert payload["desired_power_state"] == "running"
    # validate via agent
    validate_desired_state(payload, expected_server_id=server.id)


def test_process_plus_tcp_port(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "process": {"required": True, "id": "proc"},
        "port": {
            "id": "port-check",
            "protocol": "tcp",
            "port": "{{GAME_PORT}}",
        },
    }
    blueprint = load_blueprint_dict(bp_dict)
    
    # Mock get_plugin
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        
    checks = {c["check_id"]: c for c in payload["guardian"]["health_checks"]}
    assert checks["port-check"]["target_port"] == 25565
    assert checks["port-check"]["target_host"] == "127.0.0.1"


def test_minecraft_status(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "application": {
            "id": "status-check",
            "type": "minecraft-status",
        }
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        
    checks = {c["check_id"]: c for c in payload["guardian"]["health_checks"]}
    assert checks["status-check"]["type"] == "minecraft-status"
    assert checks["status-check"]["target_port"] == 25565


def test_minecraft_query_with_query_port(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "application": {
            "id": "query-check",
            "type": "minecraft-query",
        }
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        
    checks = {c["check_id"]: c for c in payload["guardian"]["health_checks"]}
    assert checks["query-check"]["target_port"] == 25566


def test_source_query(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "application": {
            "id": "src-check",
            "type": "source-query",
        }
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        
    checks = {c["check_id"]: c for c in payload["guardian"]["health_checks"]}
    assert checks["src-check"]["target_port"] == 25566


def test_http_ping_with_relative_path(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "application": {
            "id": "http-check",
            "type": "http-ping",
            "port": "{{WEB_PORT}}",
            "path": "/health",
        }
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        
    checks = {c["check_id"]: c for c in payload["guardian"]["health_checks"]}
    assert checks["http-check"]["target_port"] == 8080
    assert checks["http-check"]["path"] == "/health"


def test_allowed_port_placeholders_resolution(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "port": {
            "id": "port-check",
            "protocol": "tcp",
            "port": "{{WEB_PORT}}",
        }
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
    checks = {c["check_id"]: c for c in payload["guardian"]["health_checks"]}
    assert checks["port-check"]["target_port"] == 8080


def test_unresolved_placeholder_rejected(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "port": {
            "id": "port-check",
            "protocol": "tcp",
            "port": "{{UNKNOWN_PORT}}",
        }
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        with pytest.raises(GuardianCompileError, match="not whitelisted"):
            compile_desired_state(db, server)


def test_missing_port_role_rejected(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "port": {
            "id": "port-check",
            "protocol": "tcp",
            "port": "{{VOICE_PORT}}",
        }
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    # Server doesn't have voice port allocated
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        with pytest.raises(GuardianCompileError, match="not allocated"):
            compile_desired_state(db, server)


def test_unsupported_probe_type_rejected(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "process": {"required": True, "id": "proc"},
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    capabilities = {
        "guardian_schema_versions": [1],
        "probe_types": ["tcp"], # no 'process'
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        with pytest.raises(GuardianCompileError, match="Agent does not support"):
            compile_desired_state(db, server, capabilities=capabilities)


def test_unsupported_recovery_action_rejected(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    bp_dict["health"] = {
        "process": {"required": True, "id": "proc"},
    }
    bp_dict["recovery"] = {
        "policies": [{"match": "process_not_running", "action": "quarantine"}],
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    capabilities = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": ["restart"], # no 'quarantine'
    }
    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        with pytest.raises(GuardianCompileError, match="Agent does not support"):
            compile_desired_state(db, server, capabilities=capabilities)


def test_unsupported_schema_version_rejected(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    capabilities = {
        "guardian_schema_versions": [2], # only version 2 supported
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        with pytest.raises(GuardianCompileError, match="Agent does not support"):
            compile_desired_state(db, server, capabilities=capabilities)


def test_same_state_same_generation_and_hash(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)
    
    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload1 = compile_desired_state(db, server)
        assert server.desired_state_generation == 1
        
        payload2 = compile_desired_state(db, server)
        assert server.desired_state_generation == 1
        assert payload1["payload_hash"] == payload2["payload_hash"]


def test_changed_power_state_increments_generation(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)
    
    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        compile_desired_state(db, server)
        assert server.desired_state_generation == 1
        
        set_desired_power_state(db, server, "stopped")
        
        payload2 = compile_desired_state(db, server)
        assert server.desired_state_generation == 2
        assert payload2["desired_power_state"] == "stopped"


def test_observed_state_change_does_not_increment_generation(db: Session) -> None:
    bp_dict = _base_blueprint_dict()
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)
    
    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        compile_desired_state(db, server)
        assert server.desired_state_generation == 1
        
        server.guardian_observed_state = "healthy"
        db.commit()
        
        compile_desired_state(db, server)
        assert server.desired_state_generation == 1


def test_reconcile_saves_observed_state_fields(db: Session) -> None:
    from services.guardian_sync_service import reconcile_guardian_server
    
    bp_dict = _base_blueprint_dict()
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint
    
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = _server()
    server.node = node
    server.id = None
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
    client.get_guardian_state.return_value = {
        "schema_version": 1,
        "server_id": server.id,
        "accepted_generation": 1,
        "payload_hash": None,  # will be set dynamically
        "guardian_observed_state": "healthy",
        "container_state": "running",
        "active_incident_uuid": "some-uuid",
        "last_probe_at": "2026-07-20T12:00:00Z",
        "last_transition_at": "2026-07-20T12:01:00Z",
        "quarantine": None,
        "recovery_suspension": None,
        "supported_schema_version": 1,
    }
    client.get_incidents.return_value = []
    
    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        payload = compile_desired_state(db, server)
        client.get_guardian_state.return_value["payload_hash"] = payload["payload_hash"]
        reconcile_guardian_server(db, server, node_client=client)
        
    db.refresh(server)
    assert server.guardian_observed_state == "healthy"
    assert server.guardian_container_status == "running"
    assert server.guardian_active_incident_uuid == "some-uuid"
    assert server.guardian_probe_timestamp.replace(tzinfo=timezone.utc) == datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    assert server.guardian_transition_timestamp.replace(tzinfo=timezone.utc) == datetime(2026, 7, 20, 12, 1, tzinfo=timezone.utc)
    assert server.guardian_accepted_generation == 1
    assert server.guardian_last_payload_hash == payload["payload_hash"]
    assert server.guardian_agent_quarantine_json is None
    assert server.guardian_agent_recovery_suspension_json is None
    assert server.guardian_sync_error_statistics is None


def test_reconcile_network_failure_keeps_last_state_saves_error_stats(db: Session) -> None:
    from services.guardian_sync_service import reconcile_guardian_server
    
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = _server()
    server.node = node
    server.guardian_observed_state = "healthy"
    server.guardian_container_status = "running"
    server.id = None
    db.add_all([node, server])
    db.commit()
    db.refresh(server)
    
    client = MagicMock()
    client.get_guardian_capabilities.side_effect = Exception("network failure")
    
    with pytest.raises(Exception, match="network failure"):
        reconcile_guardian_server(db, server, node_client=client)
        
    db.refresh(server)
    # Check that observed state and container status were preserved
    assert server.guardian_observed_state == "healthy"
    assert server.guardian_container_status == "running"
    # Check that sync error stats were stored
    assert server.guardian_sync_error_statistics is not None
    err_stats = json.loads(server.guardian_sync_error_statistics)
    assert err_stats["last_error"] == "Exception"
    assert err_stats["last_error_message"] == "network failure"
