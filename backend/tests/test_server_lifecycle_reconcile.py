from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from models import Server
from services.server_lifecycle_service import (
    _run_pre_start_backup_if_enabled,
    reconcile_orphaned_lifecycle_statuses,
)


def test_reconcile_orphaned_lifecycle_statuses_maps_stuck_starting_to_docker_stopped():
    from games.base import ServerStatus

    server = Server(id=99, name="Test", game_type="dayz", status="starting")
    fake_db = MagicMock(spec=Session)
    fake_db.query.return_value.filter.return_value.all.return_value = [server]

    plugin = MagicMock()
    plugin.get_status.return_value = ServerStatus(status="stopped", message=None)

    with patch("services.server_lifecycle_service.get_plugin", return_value=plugin):
        changed = reconcile_orphaned_lifecycle_statuses(fake_db)

    assert changed == 1
    assert server.status == "stopped"
    fake_db.commit.assert_called_once()


def test_pre_start_backup_skipped_when_recent_backup_exists():
    from datetime import datetime, timezone

    server = Server(id=5, backup_on_start=True)
    fake_db = MagicMock(spec=Session)
    recent = SimpleNamespace(created_at=datetime.now(timezone.utc))

    with patch(
        "services.server_lifecycle_service._append_console_log"
    ) as log_mock, patch(
        "services.backup_service.run_backup"
    ) as backup_mock:
        fake_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = recent
        _run_pre_start_backup_if_enabled(fake_db, server, context="Start")

    backup_mock.assert_not_called()
    assert any("übersprungen" in str(c.args[1]) for c in log_mock.call_args_list)