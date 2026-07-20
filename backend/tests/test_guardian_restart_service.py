from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy.orm import Session
from models import Server
from services.guardian_restart_service import _trigger_guardian_auto_restart

def _server_for_restart() -> Server:
    return Server(
        name="RestartTest",
        game_type="minecraft",
        install_dir="/tmp/test",
        status="stopped",
        desired_power_state="running",
        guardian_observed_state="failed",
        desired_state_generation=2,
        guardian_accepted_generation=2,
        public_bind_ip="127.0.0.1",
        auto_restart=True,
    )

def test_auto_restart_triggers_on_failed_observed_state(db: Session) -> None:
    server = _server_for_restart()
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_restart_service.queue_lifecycle_operation") as mock_queue:
        _trigger_guardian_auto_restart(db, server.id)
        mock_queue.assert_called_once_with(db, server, "start")


def test_auto_restart_blocked_if_generation_mismatch(db: Session) -> None:
    server = _server_for_restart()
    server.guardian_accepted_generation = 1 # Mismatch with desired=2
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_restart_service.queue_lifecycle_operation") as mock_queue:
        _trigger_guardian_auto_restart(db, server.id)
        mock_queue.assert_not_called()


def test_auto_restart_blocked_if_desired_state_stopped(db: Session) -> None:
    server = _server_for_restart()
    server.desired_power_state = "stopped"
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_restart_service.queue_lifecycle_operation") as mock_queue:
        _trigger_guardian_auto_restart(db, server.id)
        mock_queue.assert_not_called()

def test_auto_restart_blocked_if_guardian_unknown(db: Session) -> None:
    server = _server_for_restart()
    server.guardian_observed_state = "unknown"
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.guardian_restart_service.queue_lifecycle_operation") as mock_queue:
        _trigger_guardian_auto_restart(db, server.id)
        mock_queue.assert_not_called()

