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

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
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
            
    db.refresh(server)
    
    # 1. Observed State must be saved
    assert server.guardian_observed_state == "healthy"
    assert server.guardian_accepted_generation == 7
    
    # 2. Last sync time is set
    assert server.guardian_last_sync_at is not None
    
    # 3. Incident error is recorded
    assert server.guardian_sync_error_statistics is not None
    stats = json.loads(server.guardian_sync_error_statistics)
    assert stats["last_error_message"] == "API Error fetching incidents"
