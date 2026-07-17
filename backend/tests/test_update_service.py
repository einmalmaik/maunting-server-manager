from unittest.mock import MagicMock, patch
import pytest
from services.update_service import get_update_status, trigger_panel_update, trigger_node_updates


@patch("subprocess.run")
def test_get_update_status_no_update(mock_run):
    # Mocking git fetch, branch, local SHA, remote SHA
    mock_run.side_effect = [
        MagicMock(returncode=0),
        MagicMock(returncode=0, stdout="main\n"),
        MagicMock(returncode=0, stdout="abc12345\n"),
        MagicMock(returncode=0, stdout="abc12345\n"),
    ]
    
    res = get_update_status()
    assert res["update_available"] is False
    assert res["local_sha"] == "abc12345"
    assert res["remote_sha"] == "abc12345"
    assert res["branch"] == "main"


@patch("subprocess.run")
def test_get_update_status_update_available(mock_run):
    mock_run.side_effect = [
        MagicMock(returncode=0),
        MagicMock(returncode=0, stdout="main\n"),
        MagicMock(returncode=0, stdout="localsha\n"),
        MagicMock(returncode=0, stdout="remotesh\n"),
    ]
    
    res = get_update_status()
    assert res["update_available"] is True
    assert res["local_sha"] == "localsha"
    assert res["remote_sha"] == "remotesh"
    assert res["branch"] == "main"


@patch("services.update_service.create_panel_backup")
@patch("subprocess.Popen")
def test_trigger_panel_update(mock_popen, mock_backup, db):
    mock_backup.return_value = MagicMock(filename="backup.tar.gz")
    
    res = trigger_panel_update(db)
    assert res["ok"] is True
    mock_backup.assert_called_once_with(db, name="Pre-Update Auto-Backup")
    mock_popen.assert_called_once()
