"""Tests fuer SteamCMD mit authentifiziertem Login."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from games.base import _redact, run_steamcmd_install
from services.steam_account_service import SteamAccountService


class TestRedact:
    def test_basic(self) -> None:
        assert _redact("hello secret world", ["secret"]) == "hello *** world"

    def test_no_match(self) -> None:
        assert _redact("hello world", ["secret"]) == "hello world"

    def test_empty_secret(self) -> None:
        assert _redact("hello world", [""]) == "hello world"


class TestSteamcmdAuthenticatedLogin:
    def test_authenticated_login_only_when_required(self, tmp_path: Path) -> None:
        SteamAccountService.set("u", "p")
        try:
            with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
                 patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
                mock_eph.return_value = {"ok": True, "stdout": "", "stderr": ""}
                run_steamcmd_install(
                    server_id=1,
                    install_dir=str(tmp_path),
                    app_id="123",
                    use_authenticated_login=True,
                )
                cmd = mock_eph.call_args_list[0].kwargs["command"]
                # command ist eine Liste: ["-c", "bash script ..."]
                script = " ".join(cmd)
                assert "+login" in script
                assert "u" in script
                assert "p" in script
        finally:
            SteamAccountService.clear()

    def test_blocks_install_without_account(self, tmp_path: Path) -> None:
        SteamAccountService.clear()
        result = run_steamcmd_install(
            server_id=1,
            install_dir=str(tmp_path),
            app_id="123",
            use_authenticated_login=True,
        )
        assert result["ok"] is False
        assert "Steam-Account" in result["error"]

    def test_password_never_in_console_log(self, tmp_path: Path) -> None:
        secret = "my_steam_password_42"
        SteamAccountService.set("user", secret)
        try:
            with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
                 patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)), \
                 patch("games.base._append_console_log") as mock_log:
                mock_eph.return_value = {
                    "ok": True,
                    "stdout": f"Logged in as user with password {secret}",
                    "stderr": "",
                }
                run_steamcmd_install(
                    server_id=1,
                    install_dir=str(tmp_path),
                    app_id="123",
                    use_authenticated_login=True,
                )
                logged = "".join(str(c[0][1]) for c in mock_log.call_args_list)
                assert secret not in logged
                assert "***" in logged
        finally:
            SteamAccountService.clear()
