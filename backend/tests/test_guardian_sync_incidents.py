import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json

from models import Server, Node
from models.server_port import ServerPort
from services.guardian_sync_service import reconcile_guardian_server, GuardianContractError

def test_incident_failure_does_not_rollback_successful_observed_sync(db: Session) -> None:
    # Setup Node & Server in DB
    node = Node(id=1, name="node-1", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = Server(
        name="TestSrv",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="running",
        desired_state_generation=7,
        guardian_observed_state="unknown",
        guardian_quarantine_control='{"clear":true,"operation_id":"11111111-1111-1111-1111-111111111111"}',
        public_bind_ip="127.0.0.1",
    )
    server.ports = [ServerPort(role="game", port=25565, protocol="tcp")]
    server.node = node
    db.add_all([node, server])
    db.commit()
    db.refresh(server)

    # Compile mock capabilities & blueprint
    client = MagicMock()
    client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "probe_types": ["process"],
        "diagnostic_parsers": [],
        "recovery_actions": [],
    }
    
    # Force get_incidents to fail
    client.get_incidents.side_effect = Exception("API Error fetching incidents")

    from blueprints.schema import load_blueprint_dict
    bp_dict = {
        "version": 1,
        "meta": {"id": "minecraft", "name": "Minecraft", "category": "steam_game", "description": "desc"},
        "runtime": {"image": "ubuntu:latest", "startup": "echo"},
        "ports": [],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {},
    }
    blueprint = load_blueprint_dict(bp_dict)
    plugin = MagicMock()
    plugin.get_blueprint.return_value = blueprint

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin), patch(
        "services.guardian_restart_service._trigger_guardian_auto_restart"
    ) as auto_restart:
        from services.guardian_sync_service import compile_desired_state
        payload = compile_desired_state(db, server)
        
        client.get_guardian_state.return_value = {
            "schema_version": 1,
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
            "supported_schema_version": 1
        }
        
        with pytest.raises(Exception, match="API Error fetching incidents"):
            reconcile_guardian_server(db, server, node_client=client)
        auto_restart.assert_called_once_with(db, server.id)
            
    db.refresh(server)
    
    # 1. Observed State must be saved
    assert server.guardian_observed_state == "healthy"
    assert server.guardian_accepted_generation == 7
    assert server.guardian_quarantine_control is None
    # Retiring quarantine_control changes the next desired payload → gen must bump
    # so the Agent never sees same generation + different payload_hash (409 conflict).
    assert server.desired_state_generation == 8
    
    # 2. Last sync time is set
    assert server.guardian_last_sync_at is not None
    
    # 3. Incident error is recorded
    assert server.guardian_sync_error_statistics is not None
    stats = json.loads(server.guardian_sync_error_statistics)
    assert stats["last_error_message"] == "API Error fetching incidents"


def test_quarantine_retire_bumps_desired_generation(db: Session) -> None:
    """Same-gen content change after clear-intent retire is a contract bug.

    Agent rejects POST desired-state with HTTP 409 generation_conflict when
    generation is unchanged but payload_hash differs. Retiring
    guardian_quarantine_control must therefore bump desired_state_generation.
    """
    node = Node(id=2, name="node-2", host="http://127.0.0.1", status="online", auth_token_enc="enc")
    server = Server(
        name="QuarantineRetire",
        game_type="minecraft",
        install_dir="/tmp/test-qr",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=42,
        guardian_observed_state="unknown",
        guardian_quarantine_control='{"clear":true,"operation_id":"3f2c5a82-0baa-46f0-95fe-7ea295354a6b"}',
        public_bind_ip="127.0.0.1",
    )
    server.ports = [ServerPort(role="game", port=25566, protocol="tcp")]
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
    client.set_desired_state.side_effect = lambda _name, payload: {
        "ok": True,
        "result": "updated",
        "generation": payload["generation"],
        "payload_hash": payload["payload_hash"],
    }

    from blueprints.schema import load_blueprint_dict

    plugin = MagicMock()
    plugin.get_blueprint.return_value = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "minecraft", "name": "Minecraft", "category": "steam_game", "description": "desc"},
        "runtime": {"image": "ubuntu:latest", "startup": "echo"},
        "ports": [],
        "source": {"type": "dockerOnly", "updateStrategy": "none"},
        "health": {},
    })

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin), patch(
        "services.guardian_restart_service._trigger_guardian_auto_restart"
    ):
        from services.guardian_sync_service import compile_desired_state

        payload_with_clear = compile_desired_state(db, server)
        assert payload_with_clear.get("quarantine_control") is not None
        assert payload_with_clear["generation"] == 42

        client.get_guardian_state.return_value = {
            "schema_version": 1,
            "supported_schema_version": 1,
            "server_id": server.id,
            "accepted_generation": 42,
            "payload_hash": payload_with_clear["payload_hash"],
            "guardian_observed_state": "healthy",
            "observed_runtime_state": "healthy",
            "container_state": "exited",
            "active_incident_uuid": None,
            "last_probe_at": "2026-07-20T12:00:00Z",
            "last_transition_at": "2026-07-20T11:59:00Z",
            "quarantine": None,
            "recovery_suspension": None,
        }

        stats = reconcile_guardian_server(db, server, node_client=client)

    db.refresh(server)
    assert stats.get("ok") is True or server.guardian_last_sync_at is not None
    assert server.guardian_quarantine_control is None
    assert server.desired_state_generation == 43
    assert server.guardian_accepted_generation == 42

    # Next compile must be a new generation without clear-intent (no same-gen hash clash).
    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        from services.guardian_sync_service import compile_desired_state

        next_payload = compile_desired_state(db, server)
    assert next_payload["generation"] == 43
    assert next_payload.get("quarantine_control") is None
    assert next_payload["payload_hash"] != payload_with_clear["payload_hash"]
