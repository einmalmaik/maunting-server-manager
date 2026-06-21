"""Reinstall detection for Steam HTTP install paths."""
from pathlib import Path

from games.blueprint_plugin import _steam_install_is_reinstall


def test_steam_reinstall_detects_manifest(tmp_path):
    root = tmp_path / "srv"
    (root / "steamapps").mkdir(parents=True)
    (root / "steamapps" / "appmanifest_443030.acf").write_text('"AppState"\n{\n}\n')
    assert _steam_install_is_reinstall(str(root), "443030") is True


def test_steam_reinstall_empty_dir_false(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    assert _steam_install_is_reinstall(str(root), "443030") is False