from unittest.mock import MagicMock, patch
import pytest
from services.update_service import get_update_status, trigger_panel_update, trigger_node_updates
from models import Node


@patch("subprocess.run")
def test_get_update_status_no_update(mock_run):
    # Mocking git fetch, local SHA, remote SHA
    mock_run.side_effect = [
        MagicMock(returncode=0),
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
    mock_backup.return_value = MagicMock(local_path="backups/panel/backup.tar.gz")
    
    res = trigger_panel_update(db)
    assert res["ok"] is True
    mock_backup.assert_called_once_with(db, name="Pre-Update Auto-Backup")
    assert mock_popen.call_args[0][0][:2] == ["sudo", "-n"]
    assert mock_popen.call_args[0][0][-1] == "--force"


def test_trigger_node_updates_uses_host_and_isolates_each_node_failure(db):
    first = Node(
        name="Broken",
        host="https://198.51.100.50:9000",
        auth_token_enc="enc-one",
        tls_fingerprint="1" * 64,
        is_local=False,
    )
    second = Node(
        name="Healthy",
        host="https://198.51.100.51:9000",
        auth_token_enc="enc-two",
        tls_fingerprint="2" * 64,
        is_local=False,
    )
    db.add_all([first, second])
    db.commit()

    failing = MagicMock()
    failing.update_agent.side_effect = RuntimeError("synthetic failure")
    healthy = MagicMock()
    healthy.update_agent.return_value = {"message": "updated"}
    with patch("services.update_service.generate_agent_package", return_value=b"archive"), patch(
        "services.update_service.NodeClient.from_node", side_effect=[failing, healthy]
    ) as from_node:
        result = trigger_node_updates(db)

    assert result["ok"] is False
    assert [item["ok"] for item in result["results"]] == [False, True]
    assert [call.args[0].host for call in from_node.call_args_list] == [
        first.host,
        second.host,
    ]
