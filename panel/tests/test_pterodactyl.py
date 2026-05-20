from __future__ import annotations

import shutil
from pathlib import Path
import pytest
from app import pterodactyl
from app.shell import CommandResult


def test_scan_pterodactyl_volumes_empty(tmp_path):
    # Scan an empty directory
    results = pterodactyl.scan_pterodactyl_volumes(str(tmp_path))
    assert results == []


def test_scan_pterodactyl_volumes_valid(tmp_path):
    # Create a mock candidate Pterodactyl volume
    vol_dir = tmp_path / "12345-abcde"
    vol_dir.mkdir()
    
    saved_dir = vol_dir / "ConanSandbox" / "Saved"
    saved_dir.mkdir(parents=True)
    
    db_file = saved_dir / "game_0.db"
    db_file.write_text("dummy-db-content", encoding="utf-8")
    
    config_dir = saved_dir / "Config" / "LinuxServer"
    config_dir.mkdir(parents=True)
    
    server_settings = config_dir / "ServerSettings.ini"
    server_settings.write_text(
        "[ServerSettings]\n"
        "ServerName=My Awesome Conan Server\n"
        "MaxPlayers=70\n"
        "AdminPassword=super-secret-password\n",
        encoding="utf-8"
    )
    
    mods_dir = vol_dir / "ConanSandbox" / "Mods"
    mods_dir.mkdir(parents=True)
    modlist = mods_dir / "modlist.txt"
    modlist.write_text(
        "C:/pterodactyl/volumes/12345-abcde/ConanSandbox/Mods/880603823.pak\n"
        "C:/pterodactyl/volumes/12345-abcde/ConanSandbox/Mods/1113901966.pak\n",
        encoding="utf-8"
    )
    
    results = pterodactyl.scan_pterodactyl_volumes(str(tmp_path))
    
    assert len(results) == 1
    candidate = results[0]
    assert candidate["volume_name"] == "12345-abcde"
    assert candidate["server_name"] == "My Awesome Conan Server"
    assert candidate["max_players"] == 70
    assert candidate["admin_password"] == "super-secret-password"
    assert candidate["mods_count"] == 2
    assert candidate["db_size"] > 0


def test_migrate_pterodactyl_server(tmp_path, monkeypatch):
    # Mock pterodactyl structure
    vol_dir = tmp_path / "12345-abcde"
    vol_dir.mkdir()
    
    saved_dir = vol_dir / "ConanSandbox" / "Saved"
    saved_dir.mkdir(parents=True)
    
    db_file = saved_dir / "game_0.db"
    db_file.write_text("sqlite-data", encoding="utf-8")
    
    config_dir = saved_dir / "Config" / "LinuxServer"
    config_dir.mkdir(parents=True)
    
    server_settings = config_dir / "ServerSettings.ini"
    server_settings.write_text(
        "[ServerSettings]\n"
        "ServerName=Ptero Conan Server\n"
        "AdminPassword=ptero-pwd\n"
        "MaxPlayers=32\n",
        encoding="utf-8"
    )
    
    mods_dir = vol_dir / "ConanSandbox" / "Mods"
    mods_dir.mkdir(parents=True)
    modlist = mods_dir / "modlist.txt"
    modlist.write_text(
        "C:/somepath/ConanSandbox/Mods/880603823.pak\n",
        encoding="utf-8"
    )
    
    # Mocking target server layout
    target_server_root = tmp_path / "target_server"
    target_server_root.mkdir()
    config_ini = target_server_root / "config.ini"
    config_ini.write_text(
        "servername=\"Default Name\"\n"
        "adminpassword=\"Default Pwd\"\n"
        "maxplayers=40\n",
        encoding="utf-8"
    )
    
    # Mock app.shell functions
    monkeypatch.setattr(pterodactyl, "get_server_dir", lambda name: target_server_root)
    
    core_actions = []
    def mock_invoke(*args, **kwargs):
        core_actions.append(args)
        return CommandResult(args=list(args), returncode=0, stdout="", stderr="")
        
    monkeypatch.setattr(pterodactyl, "invoke_core_action", mock_invoke)
    
    # Run migration
    res = pterodactyl.migrate_pterodactyl_server(
        pterodactyl_path=str(vol_dir),
        target_server_name="newconanserver",
        create_target=True,
        db_session=None
    )
    
    assert res["ok"] is True
    assert res["name"] == "newconanserver"
    
    # Verify core action was called to create the target
    assert core_actions == [("server", "create", "newconanserver")]
    
    # Verify DB file copy
    target_db = target_server_root / "serverfiles" / "ConanSandbox" / "Saved" / "game_0.db"
    assert target_db.is_file()
    assert target_db.read_text(encoding="utf-8") == "sqlite-data"
    
    # Verify ServerSettings copy
    target_settings = target_server_root / "serverfiles" / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "ServerSettings.ini"
    assert target_settings.is_file()
    
    # Verify config.ini was updated
    updated_config = config_ini.read_text(encoding="utf-8")
    assert 'servername="Ptero Conan Server"' in updated_config
    assert 'adminpassword="ptero-pwd"' in updated_config
    assert 'maxplayers=32' in updated_config
    
    # Verify workshop.cfg was created
    workshop_cfg = target_server_root / "workshop.cfg"
    assert workshop_cfg.is_file()
    workshop_content = workshop_cfg.read_text(encoding="utf-8")
    assert "workshop_mod_ids=(880603823)" in workshop_content
