from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import pytest

from services.guardian_incident_store import GuardianIncidentStore
from services.guardian_state_store import GuardianStateStore


def _payload(attempt: int = 1) -> dict:
    return {
        "schema_version": 1,
        "message": "synthetic incident",
        "attempts": [{"attempt": attempt, "result": "failed"}],
    }


def test_incident_is_disk_first_and_survives_store_restart(tmp_path: Path) -> None:
    state_store = GuardianStateStore(tmp_path / "guardian")
    incident_store = GuardianIncidentStore(state_store, 42)
    incident_uuid = str(uuid.uuid4())
    incident_store.upsert(
        incident_uuid=incident_uuid,
        incident_type="probe_failed",
        status="open",
        fingerprint="probe:tcp",
        payload=_payload(),
    )

    reopened = GuardianIncidentStore(state_store, 42)
    rows = reopened.list_unacknowledged()
    assert [row["uuid"] for row in rows] == [incident_uuid]
    if os.name != "nt":
        assert stat.S_IMODE(reopened.path.stat().st_mode) == 0o600


def test_duplicate_uuid_updates_one_delivery_record(tmp_path: Path) -> None:
    store = GuardianIncidentStore(GuardianStateStore(tmp_path / "guardian"), 1)
    incident_uuid = str(uuid.uuid4())
    for attempt in (1, 2):
        store.upsert(
            incident_uuid=incident_uuid,
            incident_type="startup_timeout",
            status="verifying" if attempt == 2 else "open",
            fingerprint="startup",
            payload=_payload(attempt),
        )
    rows = store.list_unacknowledged()
    assert len(rows) == 1
    assert rows[0]["status"] == "verifying"
    assert rows[0]["payload"]["attempts"][0]["attempt"] == 2


def test_partial_and_duplicate_acknowledgement_are_idempotent(tmp_path: Path) -> None:
    store = GuardianIncidentStore(GuardianStateStore(tmp_path / "guardian"), 1)
    first, second, unknown = (str(uuid.uuid4()) for _ in range(3))
    for value in (first, second):
        store.upsert(
            incident_uuid=value,
            incident_type="unhealthy",
            status="open",
            fingerprint=value,
            payload=_payload(),
        )

    assert store.acknowledge([first, unknown, first]) == [first]
    assert [row["uuid"] for row in store.list_unacknowledged()] == [second]
    assert store.acknowledge([first]) == [first]
    assert store.acknowledge([second]) == [second]
    assert store.list_unacknowledged() == []


def test_retention_never_removes_unacknowledged_incidents(tmp_path: Path) -> None:
    store = GuardianIncidentStore(GuardianStateStore(tmp_path / "guardian"), 1)
    queued = str(uuid.uuid4())
    acknowledged = str(uuid.uuid4())
    for value in (queued, acknowledged):
        store.upsert(
            incident_uuid=value,
            incident_type="unhealthy",
            status="open",
            fingerprint=value,
            payload=_payload(),
        )
    store.acknowledge([acknowledged])
    store.prune_acknowledged(keep_latest=0)
    assert [row["uuid"] for row in store.list_unacknowledged()] == [queued]
    assert store.get(acknowledged) is None


def test_uuid_conflicts_and_invalid_payloads_are_rejected(tmp_path: Path) -> None:
    store = GuardianIncidentStore(GuardianStateStore(tmp_path / "guardian"), 1)
    incident_uuid = str(uuid.uuid4())
    store.upsert(
        incident_uuid=incident_uuid,
        incident_type="unhealthy",
        status="open",
        fingerprint="same",
        payload=_payload(),
    )
    with pytest.raises(ValueError):
        store.upsert(
            incident_uuid=incident_uuid,
            incident_type="other",
            status="open",
            fingerprint="same",
            payload=_payload(),
        )
    with pytest.raises(ValueError):
        store.upsert(
            incident_uuid=str(uuid.uuid4()),
            incident_type="unhealthy",
            status="open",
            fingerprint="invalid",
            payload={"schema_version": 2},
        )

