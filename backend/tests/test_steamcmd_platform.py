"""Tests for SteamCMD platform support."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from games.base import run_steamcmd_install


class TestSteamcmdPlatform:
    def test_platform_windows_prepended(self, tmp_path: Path) -> None:
        with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
            mock_eph.return_value = {"ok": True, "stdout": "", "stderr": ""}
            run_steamcmd_install(
                server_id=1,
                install_dir=str(tmp_path),
                app_id="123",
                use_authenticated_login=False,
                platform="windows",
            )
            cmd = mock_eph.call_args_list[0].kwargs["command"]
            script = " ".join(cmd)
            assert "+@sSteamCmdForcePlatformType windows" in script
            # Ensure the platform type is set BEFORE the login
            assert script.index("+@sSteamCmdForcePlatformType windows") < script.index("+login")

    def test_platform_not_passed(self, tmp_path: Path) -> None:
        with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
            mock_eph.return_value = {"ok": True, "stdout": "", "stderr": ""}
            run_steamcmd_install(
                server_id=1,
                install_dir=str(tmp_path),
                app_id="123",
                use_authenticated_login=False,
            )
            cmd = mock_eph.call_args_list[0].kwargs["command"]
            script = " ".join(cmd)
            assert "+@sSteamCmdForcePlatformType" not in script
