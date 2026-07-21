from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from models import Incident, Server
from services.guardian_incident_service import ingest_incidents_and_ack


def _server() -> Server:
    return Server(
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


def test_idempotent_ingestion_duplicate_uuids(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())
    incidents = [
        {
            "uuid": inc_uuid,
            "server_id": server.id,
            "type": "process_not_running",
            "status": "open",
            "fingerprint": "process-error",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "schema_version": 1,
                "message": "Process went offline",
                "attempts": [{"attempt_number": 1, "timestamp": "2026-07-20T12:00:00Z"}],
            },
        }
    ]

    # First ingestion
    ack = ingest_incidents_and_ack(db, server, client, "srv-42", incidents)
    assert len(ack) == 1
    assert ack[0] == inc_uuid

    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).first()
    assert db_inc is not None
    assert db_inc.occurrences == 1
    assert len(json.loads(db_inc.attempts)) == 1

    # Second ingestion of the exact same UUID (simulate retry)
    ack2 = ingest_incidents_and_ack(db, server, client, "srv-42", incidents)
    assert len(ack2) == 1
    assert ack2[0] == inc_uuid

    db.refresh(db_inc)
    assert db_inc.occurrences == 1  # exact UUID duplicate does not increment occurrences
    assert len(json.loads(db_inc.attempts)) == 1


def test_fingerprint_grouping_consolidates_active_incidents(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid1 = str(uuid.uuid4())
    inc_uuid2 = str(uuid.uuid4())
    
    # First incident
    inc1 = {
        "uuid": inc_uuid1,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "open",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline 1",
            "attempts": [{"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"}],
        },
    }
    
    # Second incident with different UUID but same fingerprint
    inc2 = {
        "uuid": inc_uuid2,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "recovering",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline 2",
            "attempts": [{"attempt_number": 2, "started_at": "2026-07-20T12:05:00Z"}],
        },
    }

    ingest_incidents_and_ack(db, server, client, "srv-42", [inc1])
    ingest_incidents_and_ack(db, server, client, "srv-42", [inc2])

    # Should only have one incident in DB for this fingerprint, occurrences = 2
    incidents_in_db = db.query(Incident).filter(Incident.server_id == server.id).all()
    assert len(incidents_in_db) == 1
    
    parent = incidents_in_db[0]
    assert parent.uuid == inc_uuid1  # kept parent UUID
    assert parent.occurrences == 2
    assert parent.status == "recovering"
    
    attempts = json.loads(parent.attempts)
    assert len(attempts) == 2
    assert attempts[0]["attempt_number"] == 1
    assert attempts[1]["attempt_number"] == 2


def test_incident_attempt_count_does_not_force_panel_quarantine(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())
    
    # Incident with 3 attempts but status recovering
    inc = {
        "uuid": inc_uuid,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "recovering",
        "fingerprint": "process-error-quarantine",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Too many failures",
            "attempts": [
                {"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"},
                {"attempt_number": 2, "started_at": "2026-07-20T12:05:00Z"},
                {"attempt_number": 3, "started_at": "2026-07-20T12:10:00Z"},
            ],
        },
    }

    ingest_incidents_and_ack(db, server, client, "srv-42", [inc])

    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).first()
    assert db_inc is not None
    # Backend no longer sets quarantine on its own
    assert db_inc.status == "recovering"
    
    db.refresh(server)
    # Server quarantine state shouldn't be touched by the backend
    assert server.guardian_quarantine_status != "quarantined"


def test_agent_quarantine_state_is_mirrored(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())
    
    # Incident where the agent explicitly sent status quarantined
    inc = {
        "uuid": inc_uuid,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "quarantined",
        "fingerprint": "process-error-quarantine",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Too many failures",
            "attempts": [{"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"}],
        },
    }

    ingest_incidents_and_ack(db, server, client, "srv-42", [inc])

    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).first()
    assert db_inc is not None
    assert db_inc.status == "quarantined"


def test_grouped_incident_uuid_retry_does_not_increment_occurrence(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid1 = str(uuid.uuid4())
    inc_uuid2 = str(uuid.uuid4())
    
    # First incident
    inc1 = {
        "uuid": inc_uuid1,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "open",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline 1",
            "attempts": [{"attempt_number": 1, "started_at": "2026-07-20T12:00:00Z"}],
        },
    }
    
    # Second incident with different UUID but same fingerprint (grouping happens here)
    inc2 = {
        "uuid": inc_uuid2,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "recovering",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline 2",
            "attempts": [{"attempt_number": 2, "started_at": "2026-07-20T12:05:00Z"}],
        },
    }

    ingest_incidents_and_ack(db, server, client, "srv-42", [inc1])
    ingest_incidents_and_ack(db, server, client, "srv-42", [inc2])

    parent = db.query(Incident).filter(Incident.server_id == server.id).first()
    assert parent.occurrences == 2

    # Agent retries the second incident exactly as it was
    ingest_incidents_and_ack(db, server, client, "srv-42", [inc2])
    
    db.refresh(parent)
    # The occurrence should still be 2 because the delivery UUID (inc_uuid2) was already seen
    assert parent.occurrences == 2


def test_duplicate_incident_uuid_is_idempotent(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    inc_uuid = str(uuid.uuid4())
    inc = {
        "uuid": inc_uuid,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "open",
        "fingerprint": "process-error-idempotency",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline",
            "attempts": [{"attempt_number": 1, "timestamp": "2026-07-20T12:00:00Z"}],
        },
    }

    ack1 = ingest_incidents_and_ack(db, server, client, "srv-42", [inc])
    assert ack1 == [inc_uuid]

    # Re-ingest the exact same UUID (retry delivery)
    ack2 = ingest_incidents_and_ack(db, server, client, "srv-42", [inc])
    assert ack2 == [inc_uuid]

    db_inc = db.query(Incident).filter(Incident.uuid == inc_uuid).one()
    assert db_inc.occurrences == 1
    assert len(json.loads(db_inc.attempts)) == 1


def test_ack_failure_preserves_delivery_record(db: Session) -> None:
    from models import GuardianIncidentDelivery
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    client = MagicMock()
    # Mock ACK to raise exception
    client.acknowledge_incidents.side_effect = RuntimeError("Network partition")

    inc_uuid = str(uuid.uuid4())
    inc = {
        "uuid": inc_uuid,
        "server_id": server.id,
        "type": "process_not_running",
        "status": "open",
        "fingerprint": "process-error",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "schema_version": 1,
            "message": "Process went offline",
            "attempts": [{"attempt_number": 1, "timestamp": "2026-07-20T12:00:00Z"}],
        },
    }

    # ACK failure re-raises exception after local delivery commit
    with pytest.raises(RuntimeError, match="Network partition"):
        ingest_incidents_and_ack(db, server, client, "srv-42", [inc])

    delivery = db.query(GuardianIncidentDelivery).filter(GuardianIncidentDelivery.incident_uuid == inc_uuid).first()
    assert delivery is not None
    # Delivery record is preserved, even if network partition prevented ACK callback
    assert delivery.incident_uuid == inc_uuid


def test_notify_guardian_incident_triggers_webhook_and_email(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from models import User, ServerPermission
    from services.guardian_incident_service import _notify_guardian_incident

    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    user1 = User(username="admin1", email="admin1@example.com", password_hash="hash", email_notifications=True)
    user2 = User(username="admin2", email="admin2@example.com", password_hash="hash", email_notifications=False)
    db.add_all([user1, user2])
    db.commit()
    db.refresh(user1)
    db.refresh(user2)

    perm1 = ServerPermission(server_id=server.id, user_id=user1.id, permission_key="server.view")
    perm2 = ServerPermission(server_id=server.id, user_id=user2.id, permission_key="server.view")
    db.add_all([perm1, perm2])
    db.commit()

    dispatched_events = []
    sent_emails = []

    async def mock_dispatch(db, *, server, event_type, payload):
        dispatched_events.append((server.id, event_type, payload))
        return [1]

    async def mock_send_email(to, username, server_name, incident_type, status, details=""):
        sent_emails.append((to, username, server_name, incident_type, status, details))
        return True

    monkeypatch.setattr("services.outbound_webhook_service.dispatch_event", mock_dispatch)
    monkeypatch.setattr("services.email_service.EmailService.is_configured", lambda: True)
    monkeypatch.setattr("services.email_service.EmailService.send_guardian_incident_notification", mock_send_email)

    _notify_guardian_incident(server.id, "CrashLoop", "quarantined", "Process crashed 3 times")

    import time
    time.sleep(0.3)

    assert len(dispatched_events) == 1
    srv_id, evt_type, payload = dispatched_events[0]
    assert srv_id == server.id
    assert evt_type == "guardian_incident"
    assert payload["incident_type"] == "CrashLoop"
    assert payload["status"] == "quarantined"

    assert len(sent_emails) == 1
    to_email, uname, sname, inc_t, st, det = sent_emails[0]
    assert to_email == "admin1@example.com"
    assert uname == "admin1"
    assert sname == server.name
    assert inc_t == "CrashLoop"
    assert st == "quarantined"
    assert "crashed 3 times" in det

