"""Tests fuer auth_setup_service — generischer Auth-Recovery fuer Game-Server-Container."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.auth_setup_service import (
    detect_auth_required,
    move_credentials,
    run_auth_setup_recovery,
    wait_for_credentials,
)


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


# ──────────────────────────────────────────────────────────────────
# wait_for_credentials
# ──────────────────────────────────────────────────────────────────


def test_wait_returns_when_file_reappears(tmp_path: Path):
    (tmp_path / "credentials.json.bak").write_text("{}")
    # Simulate the in-container auth flow creating the new file after 1.5s.
    def writer():
        time.sleep(1.5)
        (tmp_path / "credentials.json").write_text("new")
    threading.Thread(target=writer, daemon=True).start()
    found = wait_for_credentials(tmp_path, timeout=5.0, poll_interval=0.5)
    assert found is not None
    assert found.name == "credentials.json"


def test_wait_returns_none_on_timeout(tmp_path: Path):
    # No .bak exists, no credential files at all.
    result = wait_for_credentials(tmp_path, timeout=1.0, poll_interval=0.5)
    assert result is None


def test_wait_returns_immediately_when_unbacked_credential_already_exists(tmp_path: Path):
    (tmp_path / "credentials.json").write_text("fresh")
    # Already fresh, no .bak to wait for; returns immediately.
    found = wait_for_credentials(tmp_path, timeout=0.5, poll_interval=0.1)
    assert found is not None
    assert found.name == "credentials.json"


def test_wait_skips_non_credential_files(tmp_path: Path):
    (tmp_path / "server-config.json").write_text("config")  # NOT credential pattern
    (tmp_path / "level.dat").write_text("data")
    # Should wait and time out because no credential files appear.
    result = wait_for_credentials(tmp_path, timeout=0.5, poll_interval=0.2)
    assert result is None


def test_wait_does_not_return_when_only_bak_remains(tmp_path: Path):
    # Only the .bak exists (the live file was moved away). Should wait, not return.
    (tmp_path / "credentials.json.bak").write_text("{}")
    result = wait_for_credentials(tmp_path, timeout=0.5, poll_interval=0.2)
    assert result is None


# ──────────────────────────────────────────────────────────────────
# run_auth_setup_recovery (orchestration)
# ──────────────────────────────────────────────────────────────────


def test_recovery_returns_no_credentials_moved_when_no_files(tmp_path: Path):
    """Wenn keine Credential-Files da sind, muss Recovery frueh abbrechen
    ohne Container zu starten.
    """
    log_calls: list[str] = []
    status_calls: list[tuple[bool, str | None]] = []
    restart_calls: list[bool] = []

    def on_log(text: str) -> None:
        log_calls.append(text)

    def on_status(auth_required: bool, status_message: str | None) -> None:
        status_calls.append((auth_required, status_message))

    def restart_callback() -> None:
        restart_calls.append(True)

    result = run_auth_setup_recovery(
        server_id=99,
        install_dir=tmp_path,
        docker_image="alpine:latest",
        container_command=None,
        container_env=None,
        port_publishes=[],
        volume_binds=[],
        cpu_limit_percent=None,
        ram_limit_mb=None,
        container_user="1000:1000",
        container_workdir=None,
        container_read_only_rootfs=False,
        container_tmpfs_paths=None,
        container_extra_networks=None,
        container_name="test-99",
        on_log=on_log,
        on_status=on_status,
        restart_callback=restart_callback,
        wait_timeout=0.5,
    )
    assert result == "no_credentials_moved"
    assert restart_calls == []
    assert any("keine Credential-Dateien gefunden" in (m or "") for _, m in status_calls)


def test_recovery_returns_container_start_failed(tmp_path: Path):
    """Wenn docker_service.run_container fehlschlaegt, muss Recovery das erkennen."""
    (tmp_path / "credentials.json").write_text("{}")

    log_calls: list[str] = []
    status_calls: list[tuple[bool, str | None]] = []
    restart_calls: list[bool] = []

    fake_run_container = MagicMock(return_value={"ok": False, "error": "image pull failed", "logs": ""})

    with patch("services.docker_service.run_container", fake_run_container):
        result = run_auth_setup_recovery(
            server_id=99,
            install_dir=tmp_path,
            docker_image="alpine:latest",
            container_command=None,
            container_env=None,
            port_publishes=[],
            volume_binds=[],
            cpu_limit_percent=None,
            ram_limit_mb=None,
            container_user="1000:1000",
            container_workdir=None,
            container_read_only_rootfs=False,
            container_tmpfs_paths=None,
            container_extra_networks=None,
            container_name="test-99",
            on_log=lambda t: log_calls.append(t),
            on_status=lambda a, m: status_calls.append((a, m)),
            restart_callback=lambda: restart_calls.append(True),
            wait_timeout=0.5,
        )
    assert result == "container_start_failed"
    assert restart_calls == []
    assert any("konnte nicht starten" in (m or "") for _, m in status_calls)


def test_recovery_returns_timeout_when_no_new_credentials(tmp_path: Path):
    """Wenn wait_for_credentials nach Timeout nichts findet -> 'timeout' Return."""
    (tmp_path / "credentials.json").write_text("{}")  # 1 file to move

    fake_run_container = MagicMock(return_value={"ok": True, "stdout": "abc", "stderr": ""})
    fake_stop = MagicMock(return_value={"ok": True})

    log_calls: list[str] = []
    status_calls: list[tuple[bool, str | None]] = []
    restart_calls: list[bool] = []

    with patch("services.docker_service.run_container", fake_run_container), \
         patch("services.docker_service.stop", fake_stop):
        result = run_auth_setup_recovery(
            server_id=99,
            install_dir=tmp_path,
            docker_image="alpine:latest",
            container_command=None,
            container_env=None,
            port_publishes=[],
            volume_binds=[],
            cpu_limit_percent=None,
            ram_limit_mb=None,
            container_user="1000:1000",
            container_workdir=None,
            container_read_only_rootfs=False,
            container_tmpfs_paths=None,
            container_extra_networks=None,
            container_name="test-99",
            on_log=lambda t: log_calls.append(t),
            on_status=lambda a, m: status_calls.append((a, m)),
            restart_callback=lambda: restart_calls.append(True),
            wait_timeout=0.5,
        )
    assert result == "timeout"
    assert restart_calls == []
    fake_stop.assert_called_once()


def test_recovery_returns_recovered_on_success(tmp_path: Path):
    """Wenn wait_for_credentials eine neue Datei findet -> 'recovered' + restart_callback."""
    (tmp_path / "credentials.json").write_text("{}")

    fake_run_container = MagicMock(return_value={"ok": True, "stdout": "abc", "stderr": ""})
    fake_stop = MagicMock(return_value={"ok": True})

    # Simulate that the in-container flow writes a fresh credentials.json after 0.2s.
    def writer():
        time.sleep(0.2)
        bak = tmp_path / "credentials.json.bak"
        if bak.exists():
            (tmp_path / "credentials.json").write_text("new")
    threading.Thread(target=writer, daemon=True).start()

    restart_calls: list[bool] = []

    with patch("services.docker_service.run_container", fake_run_container), \
         patch("services.docker_service.stop", fake_stop):
        result = run_auth_setup_recovery(
            server_id=99,
            install_dir=tmp_path,
            docker_image="alpine:latest",
            container_command=None,
            container_env=None,
            port_publishes=[],
            volume_binds=[],
            cpu_limit_percent=None,
            ram_limit_mb=None,
            container_user="1000:1000",
            container_workdir=None,
            container_read_only_rootfs=False,
            container_tmpfs_paths=None,
            container_extra_networks=None,
            container_name="test-99",
            on_log=lambda t: None,
            on_status=lambda a, m: None,
            restart_callback=lambda: restart_calls.append(True),
            wait_timeout=5.0,
        )
    assert result == "recovered"
    assert restart_calls == [True]
    fake_run_container.assert_called_once()
    # TTY-Flag muss gesetzt sein
    kwargs = fake_run_container.call_args.kwargs
    assert kwargs.get("tty") is True
    assert kwargs.get("startup_check_seconds") == 0.0