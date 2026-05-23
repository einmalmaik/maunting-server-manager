"""Unit-Tests für docker_service (KISS subprocess-Wrapper).

Diese Tests mocken `subprocess.run`, damit sie ohne Docker-Daemon laufen.
Wir prüfen primär, dass die zusammengebauten `docker`-CLI-Args korrekt sind
(Cap-Drop, no-new-privileges, Port-Publish, Limits, etc.).
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from services import docker_service
from services.docker_service import PortPublish, VolumeBind


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "boom", returncode: int = 1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


@pytest.fixture(autouse=True)
def reset_docker_cache():
    """Avoid leakage between tests of `_check_docker` cache."""
    docker_service._docker_available_cache = None
    yield
    docker_service._docker_available_cache = None


class TestRunContainer:
    def test_builds_hardened_command(self):
        with patch.object(docker_service, "_run_docker") as mock_run:
            mock_run.side_effect = [
                {"ok": True, "stdout": "", "stderr": ""},  # rm (idempotent)
                {"ok": True, "stdout": "abc123\n", "stderr": ""},  # run
            ]
            with patch.object(docker_service, "_check_docker", return_value=True):
                result = docker_service.run_container(
                    name="msm-srv-7",
                    image="cm2network/steamcmd:root",
                    command=["/data/DayZServer", "-port=27015"],
                    env={"FOO": "bar"},
                    ports=[PortPublish(27015, 27015, "udp", None)],
                    volumes=[VolumeBind("/opt/msm/servers/7", "/data", read_only=False)],
                    cpu_limit_percent=200,
                    ram_limit_mb=4096,
                    user="1000:1000",
                    workdir="/data",
                )
        assert result["ok"] is True
        # Letzter Aufruf war das `run`
        run_args = mock_run.call_args_list[-1].args[0]
        assert run_args[0] == "run"
        # Sicherheits-Härtungen
        assert "--cap-drop=ALL" in run_args
        assert "--security-opt=no-new-privileges" in run_args
        # Limits
        assert "--cpus=2.0" in run_args
        assert "--memory=4096m" in run_args
        assert "--memory-swap=4096m" in run_args
        # Bind-Mount + User
        # Docker-CLI nimmt --user als separates Argument
        user_idx = run_args.index("--user")
        assert run_args[user_idx + 1] == "1000:1000"
        # --volume <src>:<dst>
        assert any("/opt/msm/servers/7:/data" in a for a in run_args)
        # Port-Publish UDP
        assert any("27015:27015/udp" in a for a in run_args)
        # Image + CMD am Ende
        assert run_args[-3] == "cm2network/steamcmd:root"
        assert run_args[-2:] == ["/data/DayZServer", "-port=27015"]

    def test_bind_ip_in_port_publish(self):
        with patch.object(docker_service, "_run_docker") as mock_run, \
             patch.object(docker_service, "_check_docker", return_value=True):
            mock_run.side_effect = [
                {"ok": True, "stdout": "", "stderr": ""},
                {"ok": True, "stdout": "abc\n", "stderr": ""},
            ]
            docker_service.run_container(
                name="msm-srv-1",
                image="img",
                command=["x"],
                env={},
                ports=[PortPublish(27015, 27015, "tcp", "192.0.2.5")],
                volumes=[],
                cpu_limit_percent=None,
                ram_limit_mb=None,
                user="1000:1000",
                workdir="/data",
            )
        run_args = mock_run.call_args_list[-1].args[0]
        assert any("192.0.2.5:27015:27015/tcp" in a for a in run_args)


class TestLifecycle:
    def test_stop_returns_ok_when_not_exists(self):
        with patch.object(docker_service, "_check_docker", return_value=True), \
             patch.object(docker_service, "_run_docker", return_value={"ok": False, "error": "No such container", "stdout": "", "stderr": ""}):
            result = docker_service.stop("missing")
        # Stop bei nicht-existentem Container ist idempotent: ok=True
        assert result["ok"] is True

    def test_remove_returns_ok_when_not_exists(self):
        with patch.object(docker_service, "_check_docker", return_value=True), \
             patch.object(docker_service, "_run_docker", return_value={"ok": False, "error": "No such container", "stdout": "", "stderr": ""}):
            result = docker_service.remove("missing")
        assert result["ok"] is True


class TestDiskUsage:
    def test_du_returns_mb(self, tmp_path):
        # du -sb gibt Bytes als erste Spalte zurück. Pfad muss existieren.
        with patch("services.docker_service.subprocess.run",
                   return_value=_ok(stdout=f"104857600\t{tmp_path}\n")):
            mb = docker_service.disk_usage_mb(str(tmp_path))
        assert mb == 100

    def test_du_failure_returns_none(self, tmp_path):
        with patch("services.docker_service.subprocess.run", side_effect=FileNotFoundError):
            mb = docker_service.disk_usage_mb(str(tmp_path))
        assert mb is None

    def test_du_nonexistent_path_returns_none(self):
        # Pfad existiert nicht → sofort None ohne subprocess-Aufruf
        assert docker_service.disk_usage_mb("/nonexistent-xyz-msm-test") is None


class TestHostUidGid:
    def test_returns_tuple(self):
        uid, gid = docker_service.host_uid_gid()
        assert isinstance(uid, int)
        assert isinstance(gid, int)


class TestEphemeralRun:
    def test_steamcmd_args_passed_through(self):
        with patch.object(docker_service, "_check_docker", return_value=True), \
             patch.object(docker_service, "_run_docker") as mock_run:
            mock_run.return_value = {"ok": True, "stdout": "done", "stderr": ""}
            result = docker_service.run_ephemeral(
                image="cm2network/steamcmd:root",
                command=["+force_install_dir", "/data", "+login", "anonymous", "+app_update", "223350", "+quit"],
                volumes=[VolumeBind("/opt/msm/servers/1", "/data", read_only=False)],
                env={},
                user="1000:1000",
                workdir="/data",
                timeout=600,
            )
        assert result["ok"] is True
        args = mock_run.call_args.args[0]
        assert args[0] == "run"
        assert "--rm" in args
        assert "--cap-drop=ALL" in args
        assert "+app_update" in args[-1] or any("+app_update" in a for a in args)

    def test_entrypoint_override_lands_before_image(self):
        """--entrypoint MUSS vor dem Image stehen, sonst override't Docker den CMD."""
        with patch.object(docker_service, "_check_docker", return_value=True), \
             patch.object(docker_service, "_run_docker") as mock_run:
            mock_run.return_value = {"ok": True, "stdout": "", "stderr": ""}
            docker_service.run_ephemeral(
                image="cm2network/steamcmd:root",
                command=["+quit"],
                volumes=[],
                env={"HOME": "/data"},
                user="1000:1000",
                workdir="/home/steam/steamcmd",
                entrypoint="/home/steam/steamcmd/steamcmd.sh",
            )
        args = mock_run.call_args.args[0]
        # --entrypoint <path> als getrennte Argumente (Docker-Syntax)
        entry_idx = args.index("--entrypoint")
        assert args[entry_idx + 1] == "/home/steam/steamcmd/steamcmd.sh"
        # MUSS vor dem Image stehen
        image_idx = args.index("cm2network/steamcmd:root")
        assert entry_idx < image_idx
        # HOME-Env ist drin
        assert any(a == "HOME=/data" for a in args)


class TestSteamCMDHelpers:
    """Stellt sicher, dass run_steamcmd_install/workshop_download den Entrypoint
    explizit setzen — sonst krachen die Calls beim Bug, den der User gesehen hat
    (`exec: \"+force_install_dir\": executable file not found`)."""

    def test_steamcmd_install_uses_entrypoint_and_home(self, tmp_path):
        from games.base import STEAMCMD_ENTRYPOINT, STEAMCMD_WORKDIR, run_steamcmd_install

        with patch("games.base.docker_service.run_ephemeral") as mock_eph:
            mock_eph.return_value = {"ok": True, "stdout": "ok", "stderr": ""}
            run_steamcmd_install(server_id=1, install_dir=str(tmp_path), app_id="223350")

        kwargs = mock_eph.call_args.kwargs
        assert kwargs["entrypoint"] == STEAMCMD_ENTRYPOINT
        assert kwargs["workdir"] == STEAMCMD_WORKDIR
        assert kwargs["env"].get("HOME") == "/data"
        # Command beginnt mit den Steam-Args, nicht mit dem Entrypoint
        assert kwargs["command"][0] == "+force_install_dir"

    def test_workshop_download_uses_entrypoint_and_home(self, tmp_path):
        from games.base import STEAMCMD_ENTRYPOINT, STEAMCMD_WORKDIR, run_steamcmd_workshop_download

        with patch("games.base.docker_service.run_ephemeral") as mock_eph:
            mock_eph.return_value = {"ok": True, "stdout": "ok", "stderr": ""}
            run_steamcmd_workshop_download(
                server_id=1, install_dir=str(tmp_path), workshop_app_id="221100", workshop_item_id="12345"
            )

        kwargs = mock_eph.call_args.kwargs
        assert kwargs["entrypoint"] == STEAMCMD_ENTRYPOINT
        assert kwargs["workdir"] == STEAMCMD_WORKDIR
        assert kwargs["env"].get("HOME") == "/data"
        assert "+workshop_download_item" in kwargs["command"]
