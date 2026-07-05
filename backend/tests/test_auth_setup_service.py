"""Tests fuer auth_setup_service — generischer Auth-Recovery fuer Game-Server-Container."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.auth_setup_service import detect_auth_required, move_credentials


def test_detects_hytale_auth_prompt():
    logs = [
        "Picked up JAVA_TOOL_OPTIONS",
        "ℹ Checking for updates...",
        "Please visit the following URL to authenticate:\nhttps://oauth.accounts.hytale.com/oauth2/device/verify?user_code=ABC123",
    ]
    assert detect_auth_required(logs) is True


def test_detects_oauth_invalid_grant():
    logs = [
        'oauth2: "invalid_grant" "The refresh token expired"',
    ]
    assert detect_auth_required(logs) is True


def test_detects_refresh_token_expired_bare():
    logs = [
        "Some preceeding line",
        "refresh token expired",
        "tail",
    ]
    assert detect_auth_required(logs) is True


def test_detects_signed_url_failure():
    logs = [
        "could not get signed URL for manifest",
    ]
    assert detect_auth_required(logs) is True


def test_detects_authorization_code_line():
    logs = [
        "Authorization code: HrREE3kK",
    ]
    assert detect_auth_required(logs) is True


def test_does_not_match_unrelated_crash():
    logs = [
        "java.lang.OutOfMemoryError",
        "  at sun.misc.Unsafe.allocateMemory(Native Method)",
    ]
    assert detect_auth_required(logs) is False


def test_does_not_match_when_no_url_or_oauth_keyword():
    logs = [
        "Server started on port 25565",
        "Done (5.2s)!",
    ]
    assert detect_auth_required(logs) is False


def test_does_not_match_on_empty_logs():
    assert detect_auth_required([]) is False


def test_case_insensitive():
    logs = ["OAUTH2: \"INVALID_GRANT\""]
    assert detect_auth_required(logs) is True


# ──────────────────────────────────────────────────────────────────
# move_credentials
# ──────────────────────────────────────────────────────────────────


def test_move_credentials_renames_known_files(tmp_path: Path):
    (tmp_path / ".hytale-auth-tokens.json").write_text("{}")
    (tmp_path / ".hytale-downloader-credentials.json").write_text("{}")
    moved = move_credentials(tmp_path)
    assert moved == 2
    assert (tmp_path / ".hytale-auth-tokens.json.bak").exists()
    assert (tmp_path / ".hytale-downloader-credentials.json.bak").exists()
    assert not (tmp_path / ".hytale-auth-tokens.json").exists()
    assert not (tmp_path / ".hytale-downloader-credentials.json").exists()


def test_move_credentials_matches_generic_patterns(tmp_path: Path):
    (tmp_path / "auth_token.json").write_text("{}")
    (tmp_path / "credentials.json").write_text("{}")
    (tmp_path / "server-config.json").write_text("{}")  # NOT a credential file
    moved = move_credentials(tmp_path)
    assert moved == 2
    assert (tmp_path / "auth_token.json.bak").exists()
    assert (tmp_path / "credentials.json.bak").exists()
    assert (tmp_path / "server-config.json").exists()  # untouched


def test_move_credentials_returns_zero_when_no_match(tmp_path: Path):
    (tmp_path / "level.dat").write_text("data")
    (tmp_path / "server.log").write_text("log")
    moved = move_credentials(tmp_path)
    assert moved == 0


def test_move_credentials_idempotent(tmp_path: Path):
    (tmp_path / "credentials.json").write_text("v1")
    assert move_credentials(tmp_path) == 1
    # Second run: the file is now .bak, the live file is gone, so 0 moves.
    assert move_credentials(tmp_path) == 0
    assert (tmp_path / "credentials.json.bak").read_text() == "v1"


def test_move_credentials_overwrites_stale_backup(tmp_path: Path):
    (tmp_path / "credentials.json").write_text("fresh")
    (tmp_path / "credentials.json.bak").write_text("stale")
    assert move_credentials(tmp_path) == 1
    assert (tmp_path / "credentials.json.bak").read_text() == "fresh"


def test_move_credentials_ignores_directories_and_non_json(tmp_path: Path):
    (tmp_path / "subdir").mkdir()
    (sub := tmp_path / "subdir" / "credentials.json").write_text("{}")
    (tmp_path / "credentials.txt").write_text("not json")
    moved = move_credentials(tmp_path)
    assert moved == 0  # dir ignored, .txt ignored
    assert sub.exists()  # still there because we don't recurse
    assert (tmp_path / "credentials.txt").exists()


def test_move_credentials_accepts_string_path(tmp_path: Path):
    (tmp_path / "credentials.json").write_text("{}")
    moved = move_credentials(str(tmp_path))
    assert moved == 1
    assert (tmp_path / "credentials.json.bak").exists()