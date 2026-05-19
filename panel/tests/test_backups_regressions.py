from __future__ import annotations

import os
import stat
import tarfile
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import backups
from app.api.backups import BackupFileBody, RestoreBody, _clean_cli_detail, create_backup
from app.models import User
from app.shell import CommandResult, PanelCommandError


def _user() -> User:
    return User(id=1, username="owner", password_hash="x", role="owner", is_active=True)


def test_restore_body_accepts_minute_precision_timestamp():
    body = RestoreBody(timestamp="2026-03-17_22-45")

    assert body.timestamp == "2026-03-17_22-45"


def test_restore_body_accepts_second_suffix_timestamp():
    body = RestoreBody(timestamp="2026-03-17_22-45-01")

    assert body.timestamp == "2026-03-17_22-45-01"


def test_clean_cli_detail_strips_ansi_sequences():
    detail = "\x1b[31m[ Error ]\x1b[0m backup_nothing_to_backup\r\n"

    assert _clean_cli_detail(detail, "fallback") == "[ Error ] backup_nothing_to_backup"


def test_clean_cli_detail_prefers_meaningful_error_line_over_trailing_usage():
    detail = (
        "backup failed: destination unavailable\n"
        "Usage: conanserver.sh backup [restore <timestamp>]\n"
    )

    assert _clean_cli_detail(detail, "fallback") == "backup failed: destination unavailable"


def test_clean_cli_detail_prefers_last_german_error_line():
    detail = (
        "[ Erfolg ] Konfigurationsdatei gefunden. Werte werden geladen...\n"
        "[ Fehler ] Konnte Backup nicht erstellen.\n"
    )

    assert _clean_cli_detail(detail, "fallback") == "[ Fehler ] Konnte Backup nicht erstellen."


def test_create_backup_returns_clean_cli_error_detail(monkeypatch: pytest.MonkeyPatch):
    error = PanelCommandError(
        CommandResult(
            args=["backup"],
            returncode=1,
            stdout="",
            stderr="\x1b[31m[ Error ]\x1b[0m Could not create backup\r\n",
        )
    )

    monkeypatch.setattr("app.api.backups.invoke_core_action", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr("app.api.backups._record_audit", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        create_backup(db=SimpleNamespace(), user=_user(), server="alpha")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "[ Error ] Could not create backup"


def test_get_backup_file_content_reads_mission_file_from_tar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    run_dir = server_root / "backup" / "2026-03-19_12-30"
    run_dir.mkdir(parents=True)
    archive_path = run_dir / "mission-empty.deerisle.tar"

    with tarfile.open(archive_path, "w") as archive:
        source = tmp_path / "types.xml"
        source.write_text("<types />\n", encoding="utf-8")
        archive.add(source, arcname="empty.deerisle/db/types.xml")

    monkeypatch.setattr(backups, "get_server_base_dir", lambda server: server_root)

    response = backups.get_backup_file_content(
        timestamp="2026-03-19_12-30",
        path="serverfiles/mpmissions/empty.deerisle/db/types.xml",
        user=_user(),
        server="alpha",
    )

    assert response["archive"] == "mission-empty.deerisle.tar"
    assert response["content"] == "<types />\n"


def test_restore_backup_file_writes_selected_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    live_file = server_root / "serverprofile" / "cfg" / "players.json"
    live_file.parent.mkdir(parents=True)
    live_file.write_text('{"old": true}\n', encoding="utf-8")

    run_dir = server_root / "backup" / "2026-03-19_12-30"
    run_dir.mkdir(parents=True)
    archive_path = run_dir / "serverprofile.tar"
    backup_source = tmp_path / "players.json"
    backup_source.write_text('{"restored": true}\n', encoding="utf-8")
    with tarfile.open(archive_path, "w") as archive:
        archive.add(backup_source, arcname="serverprofile/cfg/players.json")

    monkeypatch.setattr(backups, "get_server_base_dir", lambda server: server_root)
    monkeypatch.setattr(backups, "_record_audit", lambda *args, **kwargs: None)

    response = backups.restore_backup_file(
        body=BackupFileBody(timestamp="2026-03-19_12-30", path="serverprofile/cfg/players.json"),
        db=SimpleNamespace(),
        user=_user(),
        server="alpha",
    )

    assert response["ok"] is True
    assert live_file.read_text(encoding="utf-8") == '{"restored": true}\n'


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode preservation is only meaningful on Linux hosts.")
def test_restore_backup_file_preserves_existing_file_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    live_file = server_root / "serverprofile" / "cfg" / "players.json"
    live_file.parent.mkdir(parents=True)
    live_file.write_text('{"old": true}\n', encoding="utf-8")
    live_file.chmod(0o640)

    run_dir = server_root / "backup" / "2026-03-19_12-30"
    run_dir.mkdir(parents=True)
    archive_path = run_dir / "serverprofile.tar"
    backup_source = tmp_path / "players.json"
    backup_source.write_text('{"restored": true}\n', encoding="utf-8")
    with tarfile.open(archive_path, "w") as archive:
        archive.add(backup_source, arcname="serverprofile/cfg/players.json")

    monkeypatch.setattr(backups, "get_server_base_dir", lambda server: server_root)
    monkeypatch.setattr(backups, "_record_audit", lambda *args, **kwargs: None)

    backups.restore_backup_file(
        body=BackupFileBody(timestamp="2026-03-19_12-30", path="serverprofile/cfg/players.json"),
        db=SimpleNamespace(),
        user=_user(),
        server="alpha",
    )

    assert stat.S_IMODE(live_file.stat().st_mode) == 0o640


def test_restore_backup_file_returns_permission_hint_when_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_root = tmp_path / "servers" / "alpha"
    live_file = server_root / "serverprofile" / "cfg" / "players.json"
    live_file.parent.mkdir(parents=True)
    live_file.write_text('{"old": true}\n', encoding="utf-8")

    run_dir = server_root / "backup" / "2026-03-19_12-30"
    run_dir.mkdir(parents=True)
    archive_path = run_dir / "serverprofile.tar"
    backup_source = tmp_path / "players.json"
    backup_source.write_text('{"restored": true}\n', encoding="utf-8")
    with tarfile.open(archive_path, "w") as archive:
        archive.add(backup_source, arcname="serverprofile/cfg/players.json")

    monkeypatch.setattr(backups, "get_server_base_dir", lambda server: server_root)
    monkeypatch.setattr(backups, "_record_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(backups, "_write_bytes_atomic", lambda target, data, base_dir: (_ for _ in ()).throw(PermissionError("denied")))

    with pytest.raises(HTTPException) as exc:
        backups.restore_backup_file(
            body=BackupFileBody(timestamp="2026-03-19_12-30", path="serverprofile/cfg/players.json"),
            db=SimpleNamespace(),
            user=_user(),
            server="alpha",
        )

    assert exc.value.status_code == 403
    assert "panel repair" in str(exc.value.detail)
