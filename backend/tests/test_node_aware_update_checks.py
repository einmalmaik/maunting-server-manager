import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from games.updater import check_workshop_mod_updates, check_server_file_update
from blueprints.schema import (
    Blueprint,
    BlueprintMods,
    BlueprintSource,
    BlueprintSourceType,
    BlueprintSteamSource,
    BlueprintHttpSource,
    BlueprintModListContent,
)
from services.node_client import NodeClientError


def _steam_blueprint() -> SimpleNamespace:
    mods = BlueprintMods(
        supportsMods=True,
        supportsSteamWorkshop=True,
        workshopAppId="67890",
        modInjection="none",
        modListFilePath=None,
        modListContent="workshopIds",
        postInstall=[],
    )
    return SimpleNamespace(
        effective_mods=lambda: mods,
        source=BlueprintSource(
            type=BlueprintSourceType.STEAM,
            steam=BlueprintSteamSource(appId="440900", platform="linux", validate_=True),
        ),
    )


def _http_blueprint() -> SimpleNamespace:
    mods = BlueprintMods(
        supportsMods=False,
        supportsSteamWorkshop=False,
        workshopAppId=None,
        modInjection="none",
        modListFilePath=None,
        modListContent="workshopIds",
        postInstall=[],
    )
    return SimpleNamespace(
        effective_mods=lambda: mods,
        source=BlueprintSource(
            type=BlueprintSourceType.HTTP,
            http=BlueprintHttpSource(url="https://example.com/file.zip", archiveType="zip"),
        ),
    )


def test_check_workshop_mod_updates_remote_online() -> None:
    node = SimpleNamespace(id=2, is_local=False, status="online")
    server = SimpleNamespace(
        id=77,
        game_type="synthetic_steam",
        install_dir="/opt/msm/servers/77",
        node=node,
    )
    bp = _steam_blueprint()

    mod = SimpleNamespace(
        workshop_id=12345,
        name="Test Mod",
        last_updated=datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc),
        enabled=True,
    )

    client = MagicMock()
    client.files_list.return_value = [{"name": "mod_file.pak"}]

    with patch("games.updater._query_active_mods", return_value=[mod]), \
         patch("games.updater._has_steam_api_key", return_value=True), \
         patch("games.updater._fetch_steam_mod_updated", return_value=datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)), \
         patch("services.node_client.NodeClient.from_node", return_value=client):

        result = check_workshop_mod_updates(server, bp)
        assert result == []  # Mod is up to date, so no actions needed
        client.files_list.assert_called_once_with(77, "steamapps/workshop/content/67890/12345")


def test_check_workshop_mod_updates_remote_offline() -> None:
    node = SimpleNamespace(id=2, is_local=False, status="offline")
    server = SimpleNamespace(
        id=77,
        game_type="synthetic_steam",
        install_dir="/opt/msm/servers/77",
        node=node,
    )
    bp = _steam_blueprint()

    mod = SimpleNamespace(
        workshop_id=12345,
        name="Test Mod",
        last_updated=datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc),
        enabled=True,
    )

    with patch("games.updater._query_active_mods", return_value=[mod]):
        result = check_workshop_mod_updates(server, bp)
        assert result == []  # Offline node should return empty list gracefully without exceptions


def test_check_server_file_update_remote_online_steam() -> None:
    node = SimpleNamespace(id=2, is_local=False, status="online")
    server = SimpleNamespace(
        id=77,
        game_type="synthetic_steam",
        install_dir="/opt/msm/servers/77",
        node=node,
    )
    bp = _steam_blueprint()

    client = MagicMock()
    client.files_list.return_value = [{"name": "steamapps", "is_dir": True}]
    client.files_read.return_value = '"buildid" "55555"'

    with patch("services.node_client.NodeClient.from_node", return_value=client), \
         patch("games.updater._fetch_steam_branch_build", return_value=("55555", datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc))):

        result = check_server_file_update(server, bp)
        assert result["action"] == "none"
        assert result["reason"] == "up_to_date"
        client.files_list.assert_called_once_with(77, "")
        client.files_read.assert_called_once_with(77, "steamapps/appmanifest_440900.acf")


def test_check_server_file_update_remote_online_http() -> None:
    node = SimpleNamespace(id=2, is_local=False, status="online")
    server = SimpleNamespace(
        id=77,
        game_type="synthetic_http",
        install_dir="/opt/msm/servers/77",
        node=node,
    )
    bp = _http_blueprint()

    client = MagicMock()
    # Mock files with mtime
    client.files_list.return_value = [
        {"name": "file.zip", "is_dir": False, "size": 1000, "mtime": 1780000000}
    ]

    with patch("services.node_client.NodeClient.from_node", return_value=client), \
         patch("games.updater._fetch_http_last_modified", return_value=datetime.fromtimestamp(1780000000, tz=timezone.utc)):

        result = check_server_file_update(server, bp)
        assert result["action"] == "none"
        assert result["reason"] == "up_to_date"
        client.files_list.assert_called_once_with(77, "")
