"""Unit & integration tests for Guardian UI endpoints and guardian_enabled boolean logic."""

from datetime import datetime, timezone
import json
from sqlalchemy.orm import Session

from models import Server, Incident, User
from routers.servers import _server_response
from routers.incidents import list_incidents, resolve_incident


def test_server_response_guardian_enabled_for_conan(db: Session) -> None:
    """Verify that Conan Exiles (UE5) blueprint sets guardian_enabled=True."""
    server = Server(
        name="Test Conan Server",
        game_type="conan_exiles_ue5",
        install_dir="/tmp/conan",
        status="running",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    resp = _server_response(server)
    assert resp.guardian_enabled is True


def test_server_response_guardian_enabled_false_for_custom(db: Session) -> None:
    """Verify that a server without Guardian configuration returns guardian_enabled=False."""
    server = Server(
        name="Dummy Game",
        game_type="unknown_dummy_game",
        install_dir="/tmp/dummy",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    resp = _server_response(server)
    assert resp.guardian_enabled is False


def test_list_and_resolve_incidents(db: Session) -> None:
    """Verify incident listing and manual resolution via router handlers."""
    server = Server(
        name="Incident Test Server",
        game_type="conan_exiles_ue5",
        install_dir="/tmp/inc_test",
        status="running",
    )
    user = User(username="testadmin", password_hash="dummyhash", is_owner=True)
    db.add_all([server, user])
    db.commit()
    db.refresh(server)
    db.refresh(user)

    # Add an unresolved incident
    inc = Incident(
        uuid="inc-test-uuid-123",
        server_id=server.id,
        title="Autopilot: process_not_running",
        description="Hang detected on GameThread: ConanSandboxServer crashed",
        type="process_not_running",
        status="open",
        fingerprint="fp_conan_hang",
        created_at=datetime.now(timezone.utc),
        attempts=json.dumps([{"attempt": 1, "action": "restart", "result": "success"}]),
        occurrences=1,
    )
    db.add(inc)
    db.commit()
    db.refresh(inc)

    # Test list_incidents
    incidents = list_incidents(server_id=server.id, user=user, db=db)
    assert len(incidents) == 1
    assert incidents[0]["id"] == inc.id
    assert incidents[0]["title"] == "Autopilot: process_not_running"
    assert incidents[0]["status"] == "open"
    assert len(incidents[0]["attempts"]) == 1

    # Test resolve_incident
    server.guardian_observed_state = "quarantined"
    db.commit()

    res = resolve_incident(server_id=server.id, inc_id=inc.id, user=user, db=db)
    assert res == {"ok": True}

    db.refresh(inc)
    db.refresh(server)
    assert inc.status == "resolved"
    assert inc.resolved_at is not None
    assert server.guardian_observed_state == "healthy"
    assert server.guardian_quarantine_control is not None
    assert "clear" in server.guardian_quarantine_control
