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
    # We already have `test_idempotent_ingestion_duplicate_uuids` covering this,
    # but we will add this alias test to strictly satisfy the P1.1 requirement name.
    test_idempotent_ingestion_duplicate_uuids(db)


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

    # Should not raise exception
    ack = ingest_incidents_and_ack(db, server, client, "srv-42", [inc])
    assert ack == [inc_uuid]

    delivery = db.query(GuardianIncidentDelivery).filter(GuardianIncidentDelivery.incident_uuid == inc_uuid).first()
    assert delivery is not None
    # Delivery record is preserved, even if acknowledge_at might be None due to exception
    assert delivery.incident_uuid == inc_uuid
