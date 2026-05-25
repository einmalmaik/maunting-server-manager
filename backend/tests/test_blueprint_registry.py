"""Tests fuer die Blueprint-Registry — native+community merge, conflict-handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blueprints import get_registry, reload_registry
from blueprints.registry import BlueprintSourceOrigin


def _write_blueprint(directory: Path, blueprint_id: str, **overrides) -> None:
    data = {
        "version": 1,
        "meta": {
            "id": blueprint_id,
            "name": overrides.get("name", "Community"),
            "category": "non_steam_game",
            "author": "Tester",
            "description": "",
        },
        "runtime": {
            "image": "alpine",
            "workdir": "/data",
            "env": {},
            "startup": "/data/server -port={GAME_PORT}",
        },
        "ports": [{"name": "game", "protocol": "udp"}],
        "source": {
            "type": "dockerOnly",
        },
        "mods": None,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{blueprint_id}.blueprint.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def test_native_blueprints_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings
    monkeypatch.setattr(settings, "blueprints_dir", str(tmp_path))
    reload_registry()
    registry = get_registry()
    ids = {e.blueprint.meta.id for e in registry.list()}
    assert "dayz" in ids
    assert "conan_exiles_ue5" in ids
    assert all(
        e.origin == BlueprintSourceOrigin.NATIVE
        for e in registry.list()
        if e.blueprint.meta.id in ("dayz", "conan_exiles_ue5")
    )


def test_community_blueprint_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings
    monkeypatch.setattr(settings, "blueprints_dir", str(tmp_path))
    _write_blueprint(tmp_path, "communityone", name="Community One")
    reload_registry()

    entry = get_registry().get("communityone")
    assert entry is not None
    assert entry.origin == BlueprintSourceOrigin.COMMUNITY
    assert entry.blueprint.meta.name == "Community One"


def test_native_wins_on_id_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings
    monkeypatch.setattr(settings, "blueprints_dir", str(tmp_path))
    _write_blueprint(tmp_path, "dayz", name="Fake DayZ")
    reload_registry()

    entry = get_registry().get("dayz")
    assert entry is not None
    assert entry.origin == BlueprintSourceOrigin.NATIVE
    assert entry.blueprint.meta.name == "DayZ"  # native, nicht "Fake"


def test_missing_community_dir_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings
    monkeypatch.setattr(settings, "blueprints_dir", str(tmp_path / "does-not-exist"))
    reload_registry()
    # Native muss trotzdem da sein.
    assert get_registry().exists("dayz")


def test_invalid_filename_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings
    monkeypatch.setattr(settings, "blueprints_dir", str(tmp_path))
    # Falscher Dateiname — wird ignoriert (kein Match auf <id>.blueprint.json)
    (tmp_path / "evil.json").write_text("{}", encoding="utf-8")
    _write_blueprint(tmp_path, "okone", name="OK")
    reload_registry()

    assert get_registry().get("okone") is not None
    # `evil.json` darf NICHT als Blueprint geladen sein.
    ids = {e.blueprint.meta.id for e in get_registry().list()}
    assert "evil" not in ids


def test_mismatched_id_in_file_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings
    monkeypatch.setattr(settings, "blueprints_dir", str(tmp_path))
    data = {
        "version": 1,
        "meta": {"id": "different_id", "name": "X", "category": "non_steam_game", "author": "", "description": ""},
        "runtime": {"image": "alpine", "workdir": "/data", "env": {}, "startup": "/data/x"},
        "ports": [],
        "source": {"type": "dockerOnly"},
        "mods": None,
    }
    (tmp_path / "filename_id.blueprint.json").write_text(json.dumps(data), encoding="utf-8")
    reload_registry()
    # ID-Mismatch -> Blueprint wird verworfen.
    assert get_registry().get("filename_id") is None
    assert get_registry().get("different_id") is None
