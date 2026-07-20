from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from models import Server
from services.guardian_state_service import (
    clear_recovery_suspension,
    request_quarantine_clear,
    set_desired_power_state,
    set_recovery_suspension,
)


def _server() -> Server:
    return Server(
        id=42,
        name="TestServer",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="stopped",
        desired_state_generation=1,
        guardian_observed_state="unknown",
    )


def test_set_desired_power_state_stopped_to_running(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    changed = set_desired_power_state(db, server, "running")
    assert changed is True
    assert server.desired_power_state == "running"
    assert server.desired_state_generation == 2


def test_set_desired_power_state_running_to_stopped(db: Session) -> None:
    server = _server()
    server.desired_power_state = "running"
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    changed = set_desired_power_state(db, server, "stopped")
    assert changed is True
    assert server.desired_power_state == "stopped"
    assert server.desired_state_generation == 2


def test_set_desired_power_state_identical_noop(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    changed = set_desired_power_state(db, server, "stopped")
    assert changed is False
    assert server.desired_state_generation == 1


def test_set_desired_power_state_invalid_rejected(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    with pytest.raises(ValueError, match="invalid desired power state"):
        set_desired_power_state(db, server, "invalid")


def test_set_recovery_suspension_valid(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    op_id = str(uuid.uuid4())
    suspend_until = datetime.now(timezone.utc) + timedelta(hours=2)

    set_recovery_suspension(
        db,
        server,
        operation_id=op_id,
        reason="maintenance",
        suspend_until=suspend_until,
    )

    assert server.desired_state_generation == 2
    assert server.guardian_recovery_suspension is not None
    data = json.loads(server.guardian_recovery_suspension)
    assert data["operation_id"] == op_id
    assert data["reason"] == "maintenance"
    # should be close to suspend_until ISO representation
    assert "Z" in data["suspend_until"]


def test_set_recovery_suspension_naive_datetime(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    op_id = str(uuid.uuid4())
    suspend_until = datetime.now() + timedelta(hours=2)  # naive

    set_recovery_suspension(
        db,
        server,
        operation_id=op_id,
        reason="maintenance",
        suspend_until=suspend_until,
    )
    assert server.guardian_recovery_suspension is not None
    data = json.loads(server.guardian_recovery_suspension)
    assert "Z" in data["suspend_until"]


def test_set_recovery_suspension_over_four_hours_rejected(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    op_id = str(uuid.uuid4())
    suspend_until = datetime.now(timezone.utc) + timedelta(hours=5)

    with pytest.raises(ValueError, match="recovery suspension duration exceeds 4 hours"):
        set_recovery_suspension(
            db,
            server,
            operation_id=op_id,
            reason="maintenance",
            suspend_until=suspend_until,
        )


def test_clear_recovery_suspension_valid(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    op_id = str(uuid.uuid4())
    suspend_until = datetime.now(timezone.utc) + timedelta(hours=2)

    set_recovery_suspension(
        db,
        server,
        operation_id=op_id,
        reason="maintenance",
        suspend_until=suspend_until,
    )

    cleared = clear_recovery_suspension(db, server, operation_id=op_id)
    assert cleared is True
    assert server.guardian_recovery_suspension is None
    assert server.desired_state_generation == 3


def test_clear_recovery_suspension_invalid_op_id_rejected(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    op_id = str(uuid.uuid4())
    suspend_until = datetime.now(timezone.utc) + timedelta(hours=2)

    set_recovery_suspension(
        db,
        server,
        operation_id=op_id,
        reason="maintenance",
        suspend_until=suspend_until,
    )

    other_op_id = str(uuid.uuid4())
    cleared = clear_recovery_suspension(db, server, operation_id=other_op_id)
    assert cleared is False
    assert server.guardian_recovery_suspension is not None


def test_request_quarantine_clear_idempotent(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    op_id = str(uuid.uuid4())
    request_quarantine_clear(db, server, operation_id=op_id)
    assert server.desired_state_generation == 2
    assert server.guardian_quarantine_control is not None

    # repeating is idempotent, does not increment generation
    request_quarantine_clear(db, server, operation_id=op_id)
    assert server.desired_state_generation == 2


def test_database_rollback_on_failure(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    # force database commit to fail
    with patch.object(db, "commit", side_effect=Exception("forced failure")):
        with pytest.raises(Exception, match="forced failure"):
            set_desired_power_state(db, server, "running")
        # rollback should revert to previous state
        assert server.desired_power_state == "stopped"


def test_identical_recovery_suspension_does_not_increment_generation(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)

    op_id = str(uuid.uuid4())
    suspend_until = datetime.now(timezone.utc) + timedelta(hours=2)

    set_recovery_suspension(
        db,
        server,
        operation_id=op_id,
        reason="maintenance",
        suspend_until=suspend_until,
    )
    assert server.desired_state_generation == 2

    # Second identical call should not increment generation
    set_recovery_suspension(
        db,
        server,
        operation_id=op_id,
        reason="maintenance",
        suspend_until=suspend_until,
    )
    assert server.desired_state_generation == 2
