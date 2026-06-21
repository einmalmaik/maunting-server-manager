"""Steam dedicated-server app update detection (buildid compare)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from blueprints.schema import load_blueprint_dict
from games import updater
from games.updater import check_server_file_update


def _minimal_steam_blueprint_dict(app_id: str = "223350") -> dict:
    return {
        "version": 1,
        "meta": {"id": "t", "name": "T", "category": "steam_game", "author": "x", "description": "d"},
        "runtime": {
            "image": "ghcr.io/parkervcp/steamcmd:debian",
            "workdir": "/data",
            "user": "1000:1000",
            "env": {},
            "startup": "/bin/true",
        },
        "ports": [{"name": "game", "protocol": "udp"}],
        "source": {
            "type": "steam",
            "steam": {"appId": app_id, "platform": "linux"},
            "updateStrategy": "checkBased",
        },
        "mods": {"supportsMods": False},
    }


def test_parse_appmanifest_build_id_reads_vdf(tmp_path):
    path = tmp_path / "appmanifest_1.acf"
    path.write_text('"AppState"\n{\n\t"buildid"\t\t"12345"\n}\n', encoding="utf-8")
    assert updater._parse_appmanifest_build_id(path) == "12345"


def test_steam_check_reports_update_when_build_differs(tmp_path):
    bp = load_blueprint_dict(_minimal_steam_blueprint_dict("443030"))
    install = tmp_path / "srv"
    install.mkdir()
    (install / "game.bin").write_bytes(b"x")
    manifest = install / "steamapps" / "appmanifest_443030.acf"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('"buildid"\t\t"111"\n', encoding="utf-8")

    server = type("S", (), {"id": 1, "install_dir": str(install)})()

    with patch(
        "games.updater._fetch_steam_public_branch_build",
        return_value=("999", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ):
        res = check_server_file_update(server, bp)

    assert res["action"] == "update"
    assert res["reason"] == "new_version_available"
    assert "999" in res["details"]


def test_steam_check_none_when_build_matches(tmp_path):
    bp = load_blueprint_dict(_minimal_steam_blueprint_dict("443030"))
    install = tmp_path / "srv"
    install.mkdir()
    (install / "game.bin").write_bytes(b"x")
    manifest = install / "steamapps" / "appmanifest_443030.acf"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('"buildid"\t\t"555"\n', encoding="utf-8")

    server = type("S", (), {"id": 2, "install_dir": str(install)})()

    with patch(
        "games.updater._fetch_steam_public_branch_build",
        return_value=("555", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ):
        res = check_server_file_update(server, bp)

    assert res["action"] == "none"
    assert res["reason"] == "up_to_date"