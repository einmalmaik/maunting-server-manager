from datetime import datetime, timedelta, timezone
from pathlib import Path

from blueprints.schema import load_blueprint_dict
from games.blueprint_plugin import BlueprintPlugin
from games import updater
from models import Mod
from services.mod_install_status_service import (
    mark_mod_failed,
    mark_mod_installed,
    mark_mod_installing,
    mark_mod_pending,
    parse_steamcmd_progress,
    record_mod_download_output,
)


def test_parse_steamcmd_progress_extracts_percent_and_bytes():
    progress, current_bytes, total_bytes = parse_steamcmd_progress(
        "Update state (0x61) downloading, progress: 42.4 (424 / 1000)"
    )

    assert progress == 42
    assert current_bytes == 424
    assert total_bytes == 1000


def test_mod_install_status_lifecycle_persists_progress(db, test_server):
    mod = Mod(
        server_id=test_server.id,
        workshop_id="123456",
        name="Synthetic Test Mod",
        load_order=0,
        auto_update=True,
        enabled=True,
    )
    db.add(mod)
    db.commit()

    mark_mod_pending(test_server.id, "123456", "install")
    db.refresh(mod)
    assert mod.install_status == "pending"
    assert mod.install_action == "install"
    assert mod.install_progress == 0

    mark_mod_installing(test_server.id, "123456", "install")
    db.refresh(mod)
    assert mod.install_status == "installing"
    assert mod.install_started_at is not None

    mod.install_started_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    db.commit()

    record_mod_download_output(
        test_server.id,
        "123456",
        "Update state (0x61) downloading, progress: 50.0 (500 / 1000)",
    )
    db.refresh(mod)
    assert mod.install_progress == 50
    assert mod.install_eta_seconds is not None
    assert mod.install_eta_seconds > 0

    mark_mod_installed(test_server.id, "123456")
    db.refresh(mod)
    assert mod.install_status == "installed"
    assert mod.install_progress == 100
    assert mod.install_eta_seconds == 0
    assert mod.install_error is None
    assert mod.update_status == "up_to_date"


def test_mod_install_failure_uses_generic_ui_error(db, test_server):
    mod = Mod(
        server_id=test_server.id,
        workshop_id="987654",
        name="Synthetic Failing Mod",
        load_order=0,
        auto_update=True,
        enabled=True,
    )
    db.add(mod)
    db.commit()

    mark_mod_failed(test_server.id, "987654")
    db.refresh(mod)

    assert mod.install_status == "error"
    assert mod.install_error == "Installation fehlgeschlagen"
    assert mod.install_eta_seconds is None
    assert mod.update_status == "failed"


def test_installed_mod_without_metadata_is_marked_unknown_not_updated(db, test_server, tmp_path, monkeypatch):
    workshop_app_id = "221100"
    workshop_id = "3720904511"
    install_dir = Path(tmp_path)
    local_mod_dir = install_dir / "steamapps" / "workshop" / "content" / workshop_app_id / workshop_id
    local_mod_dir.mkdir(parents=True)
    (local_mod_dir / "mod.bin").write_text("synthetic", encoding="utf-8")

    test_server.install_dir = str(install_dir)
    mod = Mod(
        server_id=test_server.id,
        workshop_id=workshop_id,
        name="Synthetic Installed Mod",
        load_order=0,
        auto_update=True,
        enabled=True,
        last_updated=None,
    )
    db.add(mod)
    db.commit()

    monkeypatch.setattr(updater, "_has_steam_api_key", lambda: False)
    monkeypatch.setattr(updater, "_fetch_steam_mod_updated", lambda _app_id, _workshop_id: None)

    blueprint = load_blueprint_dict(
        {
            "version": 1,
            "meta": {
                "id": "test_bp",
                "name": "Test",
                "category": "steam_game",
                "author": "MSM",
                "description": "",
            },
            "runtime": {
                "image": "ghcr.io/parkervcp/steamcmd:debian",
                "workdir": "/data",
                "env": {},
                "startup": "/data/server -port={GAME_PORT}",
            },
            "ports": [{"name": "game", "protocol": "udp"}],
            "source": {
                "type": "steam",
                "steam": {"appId": "12345", "platform": "linux", "compatibility": "native"},
            },
            "mods": {
                "supportsMods": True,
                "supportsSteamWorkshop": True,
                "workshopAppId": workshop_app_id,
                "modInjection": "startupArg",
                "modStartupArgumentFormat": "-mod={mods}",
            },
        }
    )

    updates = updater.check_workshop_mod_updates(test_server, blueprint)
    db.refresh(mod)

    assert updates == []
    assert mod.last_updated is None
    assert mod.installed_version is None
    assert mod.update_status == "unknown"
    assert mod.update_reason == "steam_api_key_missing"


def test_installed_mod_without_metadata_stays_unknown_when_remote_metadata_exists(db, test_server, tmp_path, monkeypatch):
    workshop_app_id = "221100"
    workshop_id = "3720904512"
    install_dir = Path(tmp_path)
    local_mod_dir = install_dir / "steamapps" / "workshop" / "content" / workshop_app_id / workshop_id
    local_mod_dir.mkdir(parents=True)
    (local_mod_dir / "mod.bin").write_text("synthetic", encoding="utf-8")

    test_server.install_dir = str(install_dir)
    mod = Mod(
        server_id=test_server.id,
        workshop_id=workshop_id,
        name="Synthetic Installed Mod",
        load_order=0,
        auto_update=True,
        enabled=True,
        last_updated=None,
    )
    db.add(mod)
    db.commit()

    monkeypatch.setattr(updater, "_has_steam_api_key", lambda: True)
    monkeypatch.setattr(
        updater,
        "_fetch_steam_mod_updated",
        lambda _app_id, _workshop_id: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    blueprint = load_blueprint_dict(
        {
            "version": 1,
            "meta": {
                "id": "test_bp",
                "name": "Test",
                "category": "steam_game",
                "author": "MSM",
                "description": "",
            },
            "runtime": {
                "image": "ghcr.io/parkervcp/steamcmd:debian",
                "workdir": "/data",
                "env": {},
                "startup": "/data/server -port={GAME_PORT}",
            },
            "ports": [{"name": "game", "protocol": "udp"}],
            "source": {
                "type": "steam",
                "steam": {"appId": "12345", "platform": "linux", "compatibility": "native"},
            },
            "mods": {
                "supportsMods": True,
                "supportsSteamWorkshop": True,
                "workshopAppId": workshop_app_id,
                "modInjection": "startupArg",
                "modStartupArgumentFormat": "-mod={mods}",
            },
        }
    )

    updates = updater.check_workshop_mod_updates(test_server, blueprint)
    db.refresh(mod)

    assert updates == []
    assert mod.last_updated is None
    assert mod.installed_version is None
    assert mod.update_status == "unknown"
    assert mod.update_reason == "missing_local_metadata"


def test_perform_workshop_mod_updates_only_applies_auto_update_mods(db, test_server, monkeypatch):
    auto_mod = Mod(
        server_id=test_server.id,
        workshop_id="111",
        name="Auto Mod",
        load_order=0,
        auto_update=True,
        enabled=True,
    )
    manual_mod = Mod(
        server_id=test_server.id,
        workshop_id="222",
        name="Manual Mod",
        load_order=1,
        auto_update=False,
        enabled=True,
    )
    db.add_all([auto_mod, manual_mod])
    db.commit()

    blueprint = load_blueprint_dict(
        {
            "version": 1,
            "meta": {"id": "test_bp", "name": "Test", "category": "steam_game"},
            "runtime": {"image": "ghcr.io/parkervcp/steamcmd:debian", "workdir": "/data", "env": {}, "startup": "/data/server"},
            "ports": [{"name": "game", "protocol": "udp"}],
            "source": {"type": "steam", "steam": {"appId": "12345", "platform": "linux"}},
            "mods": {
                "supportsMods": True,
                "supportsSteamWorkshop": True,
                "workshopAppId": "440900",
                "modInjection": "startupArg",
                "modStartupArgumentFormat": "-mod={mods}",
            },
        }
    )
    plugin = BlueprintPlugin(blueprint)
    needed = [
        {"workshop_id": "111", "name": "Auto Mod", "action": "update", "remote_updated": None},
        {"workshop_id": "222", "name": "Manual Mod", "action": "update", "remote_updated": None},
    ]
    installed: list[str] = []
    monkeypatch.setattr(plugin, "check_for_mod_updates", lambda _server: needed)
    monkeypatch.setattr(
        plugin,
        "install_mods",
        lambda _server, workshop_ids: installed.extend(workshop_ids) or {
            "ok": True,
            "items": {workshop_id: {"ok": True} for workshop_id in workshop_ids},
        },
    )
    monkeypatch.setattr(plugin, "update_modlist", lambda _server: None)

    result = plugin.perform_workshop_mod_updates(test_server, only_auto_update=True)

    assert result["ok"] is True
    assert result["applied"] == 1
    assert installed == ["111"]
