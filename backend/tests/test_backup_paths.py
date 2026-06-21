"""Tests für blueprint-gesteuerte Backup-Pfade."""

from pathlib import Path

import pytest

from services.backup_paths import (
    BACKUP_MANIFEST_ARCNAME,
    BackupPlan,
    backup_plan_for_server,
    create_selective_backup_tar,
    read_backup_scope_from_archive,
    resolve_backup_members,
)


def test_resolve_backup_members_conan_paths(tmp_path: Path):
    install = tmp_path / "srv"
    (install / "ConanSandbox/Saved/Config/LinuxServer").mkdir(parents=True)
    (install / "ConanSandbox/Saved/Config/LinuxServer/Game.ini").write_text("[x]\n", encoding="utf-8")
    (install / "ConanSandbox/Saved/SaveGames").mkdir(parents=True)
    (install / "ConanSandbox/Saved/SaveGames/world.sav").write_bytes(b"save")
    (install / "steamapps").mkdir()

    members = resolve_backup_members(
        str(install),
        [
            "ConanSandbox/Saved/Config",
            "ConanSandbox/Saved/SaveGames",
            "ConanSandbox/Saved/game.db",
        ],
    )
    assert "ConanSandbox/Saved/Config" in members or any(m.startswith("ConanSandbox/Saved/Config") for m in members)
    assert any("SaveGames" in m for m in members)
    assert not any(m.startswith("steamapps") for m in members)


def test_create_selective_backup_tar_has_manifest(tmp_path: Path):
    install = tmp_path / "srv"
    cfg = install / "ConanSandbox/Saved/Config"
    cfg.mkdir(parents=True)
    (cfg / "a.ini").write_text("k=v", encoding="utf-8")

    out = tmp_path / "b.tar.gz"
    create_selective_backup_tar(
        str(out),
        str(install),
        ["ConanSandbox/Saved/Config"],
    )
    scope, manifest = read_backup_scope_from_archive(str(out))
    assert scope == "selective"
    assert manifest is not None
    assert manifest.get("includePaths")


def test_backup_plan_conan_is_selective(db, test_server, tmp_path: Path):
    from models import Server

    install = tmp_path / "conan"
    install.mkdir()
    test_server.game_type = "conan_exiles_ue5"
    test_server.install_dir = str(install)
    db.commit()

    plan = backup_plan_for_server(test_server)
    assert plan.scope == "selective"
    assert "ConanSandbox/Saved/Config" in plan.include_paths


def test_create_selective_raises_when_nothing_to_backup(tmp_path: Path):
    install = tmp_path / "empty"
    install.mkdir()
    with pytest.raises(FileNotFoundError):
        create_selective_backup_tar(str(tmp_path / "x.tar.gz"), str(install), ["missing/path"])