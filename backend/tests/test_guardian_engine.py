import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from models import ChangeEvent, Incident, Server, Node
from services.change_timeline_service import log_change_event
from services.server_lifecycle_service import sync_desired_state_to_agent
from services.scheduler_service import _guardian_reconciliation_task


def test_incident_and_change_event_creation():
    db = MagicMock(spec=Session)
    incident = Incident(
        server_id=1,
        title="Test Incident",
        description="OOM crash",
        type="out_of_memory",
        status="open",
        fingerprint="GUARDIAN_1_oom",
        attempts=json.dumps([{"stage": 2, "recovery_action": "restart"}])
    )
    db.add(incident)
    db.commit()
    
    assert incident.server_id == 1
    assert incident.type == "out_of_memory"
    assert incident.status == "open"
    assert incident.resolved_at is None


def test_log_change_event():
    db = MagicMock(spec=Session)
    
    log_change_event(
        db,
        server_id=2,
        event_type="config_change",
        description="Blueprint RAM limit modified",
        details={"ram_limit_mb": 8192}
    )
    
    assert db.add.called
    db.commit.assert_called_once()


def test_sync_desired_state_to_agent():
    node = Node(id=1, status="online", auth_token_enc="encrypted")
    server = Server(id=42, game_type="minecraft", node=node)
    
    plugin = MagicMock()
    bp = MagicMock()
    bp.health = None
    bp.logs = None
    bp.diagnostics = None
    bp.recovery = None
    bp.updates = None
    bp.backups = None
    plugin.get_blueprint.return_value = bp
    
    client_mock = MagicMock()
    
    with patch("services.server_lifecycle_service.get_plugin", return_value=plugin), \
         patch("services.node_client.NodeClient") as mock_class:
        mock_class.from_node.return_value = client_mock
        sync_desired_state_to_agent(server, "running")
        
    client_mock.set_desired_state.assert_called_once()
    args, kwargs = client_mock.set_desired_state.call_args
    assert args[0] == "msm-srv-42"
    assert args[1]["status"] == "running"


def test_guardian_reconciliation_task():
    node = Node(id=1, status="online", auth_token_enc="encrypted")
    server = Server(id=42, game_type="minecraft", node=node, status="running")
    
    fake_db = MagicMock(spec=Session)
    fake_db.query.return_value.filter.return_value.all.return_value = [server]
    
    client_mock = MagicMock()
    # Mock return value of node agent incidents
    client_mock.get_incidents.return_value = [
        {
            "type": "out_of_memory",
            "stage": 2,
            "recovery_action": "controlled_memory_recovery",
            "result": "success",
            "message": "Out of memory recovery completed successfully"
        }
    ]
    
    plugin = MagicMock()
    bp = MagicMock()
    bp.health = None
    bp.logs = None
    bp.diagnostics = None
    bp.recovery = None
    bp.updates = None
    bp.backups = None
    plugin.get_blueprint.return_value = bp
    
    with patch("services.scheduler_service.SessionLocal", return_value=fake_db), \
         patch("services.node_client.NodeClient") as mock_class, \
         patch("games.get_plugin", return_value=plugin):
        mock_class.from_node.return_value = client_mock
        import asyncio
        asyncio.run(_guardian_reconciliation_task())
        
    client_mock.get_incidents.assert_called_once_with("msm-srv-42")
    client_mock.clear_incidents.assert_called_once_with("msm-srv-42")
    assert fake_db.add.called
    assert fake_db.commit.called
