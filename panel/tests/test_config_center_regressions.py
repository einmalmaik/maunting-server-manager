from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.config_center import ServerDzBody, get_config_overview, put_serverdz
from app.server_layout import collect_recent_files, get_server_cfg_path, get_server_cfg_relative_path, get_servers_root, resolve_quick_directories
from app.serverdz import parse_serverdz, render_serverdz


def test_server_settings_roundtrip_preserves_unknown_conan_lines():
    raw = """
[ServerSettings]
ServerName=Operation Echo
AdminPassword=secret
PVPEnabled=False
HarvestAmountMultiplier=1.500000
CustomModToggle=1

[/script/engine.gamesession]
MaxPlayers=40
""".strip()

    parsed = parse_serverdz(raw)

    assert parsed["known"]["ServerName"] == "Operation Echo"
    assert parsed["known"]["AdminPassword"] == "secret"
    assert parsed["known"]["PVPEnabled"] is False
    assert parsed["known"]["HarvestAmountMultiplier"] == 1.5
    assert "CustomModToggle=1" in parsed["custom_raw"]
    assert "[/script/engine.gamesession]" in parsed["custom_raw"]

    rendered = render_serverdz(parsed["known"], parsed["custom_raw"])

    assert "[ServerSettings]" in rendered
    assert "ServerName=Operation Echo" in rendered
    assert "PVPEnabled=False" in rendered
    assert "HarvestAmountMultiplier=1.5" in rendered
    assert "CustomModToggle=1" in rendered
    assert "[/script/engine.gamesession]" in rendered


def test_server_settings_does_not_render_missing_values_as_zero_defaults():
    parsed = parse_serverdz("[ServerSettings]\nServerName=Only Name\n")
    rendered = render_serverdz(parsed["known"], parsed["custom_raw"])

    assert "ServerName=Only Name" in rendered
    assert "ClanMaxSize=" not in rendered
    assert "PVPEnabled=" not in rendered


def test_server_settings_parses_common_conan_fields_with_inline_comments():
    raw = """
[ServerSettings]
ServerName=Operation Echo ; Server browser name
AdminPassword=secret
ServerPassword=
ServerCommunity=0
MaxNudity=2
PVPEnabled=True
IsBattlEyeEnabled=False
ClanMaxSize=22
MaxPlayers=40
LogoutCharactersRemainInTheWorld=False
AvatarsDisabled=True
EnableSandStorm=True
HarvestAmountMultiplier=2.0
ResourceRespawnSpeedMultiplier=1.5
NPCRespawnMultiplier=0.8
DayCycleSpeedScale=1.0
PlayerXPRateMultiplier=3.0
PlayerDamageMultiplier=1.1
NPCDamageTakenMultiplier=0.9
StructureDamageMultiplier=0.5
CanDamagePlayerOwnedStructures=False
BuildingPreloadRadius=80
ServerVoiceChat=True
""".strip()

    parsed = parse_serverdz(raw)

    assert parsed["known"]["ServerName"] == "Operation Echo"
    assert parsed["known"]["AdminPassword"] == "secret"
    assert parsed["known"]["ServerPassword"] == ""
    assert parsed["known"]["ServerCommunity"] == 0
    assert parsed["known"]["MaxNudity"] == 2
    assert parsed["known"]["PVPEnabled"] is True
    assert parsed["known"]["IsBattlEyeEnabled"] is False
    assert parsed["known"]["ClanMaxSize"] == 22
    assert parsed["known"]["MaxPlayers"] == 40
    assert parsed["known"]["LogoutCharactersRemainInTheWorld"] is False
    assert parsed["known"]["AvatarsDisabled"] is True
    assert parsed["known"]["EnableSandStorm"] is True
    assert parsed["known"]["HarvestAmountMultiplier"] == 2.0
    assert parsed["known"]["ResourceRespawnSpeedMultiplier"] == 1.5
    assert parsed["known"]["NPCRespawnMultiplier"] == 0.8
    assert parsed["known"]["DayCycleSpeedScale"] == 1.0
    assert parsed["known"]["PlayerXPRateMultiplier"] == 3.0
    assert parsed["known"]["PlayerDamageMultiplier"] == 1.1
    assert parsed["known"]["NPCDamageTakenMultiplier"] == 0.9
    assert parsed["known"]["StructureDamageMultiplier"] == 0.5
    assert parsed["known"]["CanDamagePlayerOwnedStructures"] is False
    assert parsed["known"]["BuildingPreloadRadius"] == 80.0
    assert parsed["known"]["ServerVoiceChat"] is True


def test_server_cfg_relative_path_uses_safe_conan_config_dir(tmp_path: Path):
    base_dir = tmp_path / "alpha"
    base_dir.mkdir()
    (base_dir / "config.ini").write_text('server_config_dir="Custom/Config"\n', encoding="utf-8")

    assert get_server_cfg_relative_path(base_dir) == "serverfiles/Custom/Config/ServerSettings.ini"


def test_server_cfg_path_rejects_parent_traversal_from_config_ini(tmp_path: Path):
    base_dir = tmp_path / "alpha"
    serverfiles_dir = base_dir / "serverfiles"
    serverfiles_dir.mkdir(parents=True)
    (base_dir / "config.ini").write_text('server_config_dir="../outside"\n', encoding="utf-8")

    assert get_server_cfg_path(base_dir) == serverfiles_dir / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "ServerSettings.ini"


def test_collect_recent_files_skips_workshop_and_backup_trees(tmp_path: Path):
    base_dir = tmp_path / "alpha"
    config_file = base_dir / "serverfiles" / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "ServerSettings.ini"
    workshop_file = base_dir / "serverfiles" / "steamapps" / "workshop" / "content" / "440900" / "123" / "Mod.pak"
    backup_file = base_dir / "backup" / "2026-03-19_10-00" / "ServerSettings.ini"
    config_file.parent.mkdir(parents=True)
    workshop_file.parent.mkdir(parents=True)
    backup_file.parent.mkdir(parents=True)
    config_file.write_text("[ServerSettings]\nServerName=Echo\n", encoding="utf-8")
    workshop_file.write_text("binary placeholder\n", encoding="utf-8")
    backup_file.write_text("backup copy\n", encoding="utf-8")

    recent = collect_recent_files(base_dir, limit=10)
    paths = {entry["path"] for entry in recent}

    assert "serverfiles/ConanSandbox/Saved/Config/LinuxServer/ServerSettings.ini" in paths
    assert "serverfiles/steamapps/workshop/content/440900/123/Mod.pak" not in paths
    assert "backup/2026-03-19_10-00/ServerSettings.ini" not in paths


def test_servers_root_accepts_runtime_home_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    runtime_home = tmp_path / "conan-home"
    runtime_home.mkdir()
    monkeypatch.setenv("CONAN_DATA_ROOT", str(runtime_home))

    assert get_servers_root() == runtime_home / "servers"


def test_servers_root_accepts_explicit_servers_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    servers_root = tmp_path / "servers"
    servers_root.mkdir()
    monkeypatch.setenv("CONAN_DATA_ROOT", str(servers_root))

    assert get_servers_root() == servers_root


def test_put_serverdz_drops_empty_numeric_and_password_values(tmp_path: Path, monkeypatch):
    base_dir = tmp_path / "alpha"
    config_dir = base_dir / "serverfiles" / "ConanSandbox" / "Saved" / "Config" / "LinuxServer"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr("app.api.config_center.get_server_base_dir", lambda server: base_dir)

    response = put_serverdz(
        body=ServerDzBody(
            known={
                "ServerName": "Operation Echo",
                "MaxPlayers": "",
                "ServerPassword": "",
            },
            custom_raw="",
        ),
        server="alpha",
        user=object(),
    )

    written = (config_dir / "ServerSettings.ini").read_text(encoding="utf-8")
    assert response["known"]["ServerName"] == "Operation Echo"
    assert response["known"]["MaxPlayers"] is None
    assert response["known"]["ServerPassword"] is None
    assert "ServerName=Operation Echo" in written
    assert "MaxPlayers=" not in written
    assert "ServerPassword=" not in written


def test_put_serverdz_rejects_invalid_numeric_value(tmp_path: Path, monkeypatch):
    base_dir = tmp_path / "alpha"
    (base_dir / "serverfiles").mkdir(parents=True)
    monkeypatch.setattr("app.api.config_center.get_server_base_dir", lambda server: base_dir)

    with pytest.raises(HTTPException) as exc:
        put_serverdz(
            body=ServerDzBody(
                known={"ClanMaxSize": "many"},
                custom_raw="",
            ),
            server="alpha",
            user=object(),
        )

    assert exc.value.status_code == 422


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode preservation is only meaningful on Linux hosts.")
def test_put_serverdz_preserves_existing_file_mode(tmp_path: Path, monkeypatch):
    base_dir = tmp_path / "alpha"
    config_dir = base_dir / "serverfiles" / "ConanSandbox" / "Saved" / "Config" / "LinuxServer"
    config_dir.mkdir(parents=True)
    target = config_dir / "ServerSettings.ini"
    target.write_text("[ServerSettings]\nServerName=Old\n", encoding="utf-8")
    target.chmod(0o640)
    monkeypatch.setattr("app.api.config_center.get_server_base_dir", lambda server: base_dir)

    put_serverdz(
        body=ServerDzBody(
            known={"ServerName": "New Host"},
            custom_raw="",
        ),
        server="alpha",
        user=object(),
    )

    written = target.read_text(encoding="utf-8")
    assert "ServerName=New Host" in written
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_get_config_overview_exposes_conan_quick_directories(tmp_path: Path, monkeypatch):
    base_dir = tmp_path / "alpha"
    (base_dir / "serverfiles" / "ConanSandbox" / "Saved" / "Config" / "LinuxServer").mkdir(parents=True)
    (base_dir / "serverfiles" / "ConanSandbox" / "Saved" / "game_0.db").write_text("sqlite placeholder\n", encoding="utf-8")
    (base_dir / "serverfiles" / "ConanSandbox" / "Mods").mkdir(parents=True)
    monkeypatch.setattr("app.api.config_center.get_server_base_dir", lambda server: base_dir)

    overview = get_config_overview(server="alpha", user=object())
    quick_directories = {entry["key"]: entry for entry in overview["quick_directories"]}

    assert overview["mission_folder"] is None
    assert quick_directories["server_root"]["path"] == ""
    assert quick_directories["serverfiles"]["path"] == "serverfiles"
    assert quick_directories["saved"]["path"] == "serverfiles/ConanSandbox/Saved"
    assert quick_directories["mods"]["path"] == "serverfiles/ConanSandbox/Mods"


def test_resolve_quick_directories_returns_conan_shortcuts(tmp_path: Path):
    base_dir = tmp_path / "alpha"
    (base_dir / "serverfiles" / "ConanSandbox" / "Saved").mkdir(parents=True)
    (base_dir / "serverfiles" / "ConanSandbox" / "Mods").mkdir(parents=True)

    quick_directories = {entry["key"]: entry for entry in resolve_quick_directories(base_dir)}

    assert quick_directories["saved"]["exists"] is True
    assert quick_directories["mods"]["exists"] is True
