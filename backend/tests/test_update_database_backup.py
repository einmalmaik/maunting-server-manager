from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import update_database_backup as backup_helper


def _write_env(path: Path, url: str) -> None:
    path.write_text(f'MSM_DATABASE_URL="{url}"\n', encoding="utf-8")


def test_pg_dump_receives_password_via_environment_not_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    output = tmp_path / "panel.dump"
    _write_env(
        env_file,
        "postgresql+psycopg2://msm:synthetic%21secret@127.0.0.1:5432/msm",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(backup_helper.shutil, "which", lambda _name: "/usr/bin/pg_dump")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        output.write_bytes(b"synthetic-pg-dump")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(backup_helper.subprocess, "run", fake_run)
    backup_helper.create_dump(env_file, output)

    command_text = " ".join(captured["command"])
    assert "synthetic!secret" not in command_text
    assert captured["env"]["PGPASSWORD"] == "synthetic!secret"
    assert output.read_bytes() == b"synthetic-pg-dump"


def test_sqlite_backup_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _write_env(env_file, "sqlite:///./msm.db")
    with pytest.raises(RuntimeError, match="keine PostgreSQL"):
        backup_helper.create_dump(env_file, tmp_path / "panel.dump")


def test_failed_pg_dump_removes_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    output = tmp_path / "panel.dump"
    _write_env(env_file, "postgresql://msm:synthetic@127.0.0.1/msm")
    monkeypatch.setattr(backup_helper.shutil, "which", lambda _name: "/usr/bin/pg_dump")

    def fake_run(_command, **_kwargs):
        output.write_bytes(b"partial")
        return SimpleNamespace(returncode=2, stderr="synthetic failure")

    monkeypatch.setattr(backup_helper.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Exit 2"):
        backup_helper.create_dump(env_file, output)
    assert not output.exists()
