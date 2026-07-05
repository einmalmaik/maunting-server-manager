"""Tests für blueprint-gesteuerte Backup-Pfade."""

import tarfile
from pathlib import Path

import pytest

from services.backup_paths import (
    BACKUP_MANIFEST_ARCNAME,
    BackupPlan,
    backup_plan_for_server,
    create_full_backup_tar,
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


def test_create_full_backup_tar_excludes_caches_and_logs(tmp_path: Path):
    """Vollbackup schliesst node_modules, .git, __pycache__, .log und .pyc aus."""
    install = tmp_path / "srv"
    # Normale Dateien, die enthalten sein sollen
    (install / "config").mkdir(parents=True)
    (install / "config" / "server.cfg").write_text("k=v\n", encoding="utf-8")
    (install / "world.sav").write_bytes(b"save")

    # Ausgeschlossene Verzeichnisse und Dateien
    (install / "node_modules" / "pkg").mkdir(parents=True)
    (install / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    (install / ".git").mkdir(parents=True)
    (install / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (install / "__pycache__").mkdir(parents=True)
    (install / "__pycache__" / "mod.pyc").write_bytes(b"pyc")
    (install / "server.log").write_text("log line\n", encoding="utf-8")
    (install / "app.pyc").write_bytes(b"pyc")

    out = tmp_path / "full.tar.gz"
    create_full_backup_tar(str(out), str(install))

    with tarfile.open(str(out), "r:gz") as tar:
        names = tar.getnames()

    # Erwartete Dateien enthalten
    assert "config/server.cfg" in names
    assert "world.sav" in names

    # Ausgeschlossene Verzeichnisse und Dateien NICHT enthalten
    assert not any(n.startswith("node_modules/") for n in names)
    assert not any(n.startswith(".git/") for n in names)
    assert not any(n.startswith("__pycache__/") for n in names)
    assert "server.log" not in names
    assert "app.pyc" not in names


def test_create_selective_backup_tar_excludes_logs_in_included_dir(tmp_path: Path):
    """Selective Backup schliesst .log-Dateien auch in enthaltenen Verzeichnissen aus."""
    install = tmp_path / "srv"
    cfg = install / "ConanSandbox" / "Saved" / "Config"
    cfg.mkdir(parents=True)
    (cfg / "Game.ini").write_text("[x]\n", encoding="utf-8")
    (cfg / "debug.log").write_text("log\n", encoding="utf-8")

    out = tmp_path / "sel.tar.gz"
    create_selective_backup_tar(
        str(out),
        str(install),
        ["ConanSandbox/Saved/Config"],
    )

    with tarfile.open(str(out), "r:gz") as tar:
        names = tar.getnames()

    assert any("Game.ini" in n for n in names)
    assert not any(n.endswith(".log") for n in names)