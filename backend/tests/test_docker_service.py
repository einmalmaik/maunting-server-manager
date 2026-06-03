"""Unit-Tests fuer den Rootless-Docker-SDK-Adapter."""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services import docker_service
from services.docker_service import PortPublish, VolumeBind
from games.base import GamePlugin, ServerStatus


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def reset_docker_cache():
    docker_service._CLIENT = None
    docker_service._DOCKER_AVAILABLE = None
    yield
    docker_service._CLIENT = None
    docker_service._DOCKER_AVAILABLE = None


class TestDockerHost:
    def test_missing_rootless_socket_returns_safe_error(self):
        with patch("services.docker_service.docker", MagicMock()), \
             patch.object(docker_service.settings, "docker_host", "unix:///run/user/1001/docker.sock"), \
             patch("services.docker_service.os.path.exists", return_value=False):
            result = docker_service.pull("ghcr.io/parkervcp/steamcmd:debian")

        assert result == {
            "ok": False,
            "error": docker_service.ROOTLESS_DOCKER_ERROR,
            "stdout": "",
            "stderr": "",
        }

    def test_msm_docker_host_precedes_docker_host_env(self, monkeypatch):
        monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/9999/docker.sock")
        with patch.object(docker_service.settings, "docker_host", "unix:///run/user/1001/docker.sock"):
            assert docker_service.resolve_docker_host() == "unix:///run/user/1001/docker.sock"

    def test_pull_reports_registry_failure_cause(self):
        client = MagicMock()
        client.api.pull.side_effect = docker_service.DockerException(
            "Get https://ghcr.io/v2/: dial tcp: lookup ghcr.io: no such host"
        )

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.pull("ghcr.io/ptero-eggs/yolks:wine_staging")

        assert result == {
            "ok": False,
            "error": "Docker Pull fehlgeschlagen: Registry/DNS nicht erreichbar",
            "stdout": "",
            "stderr": "",
        }

    def test_pull_reports_platform_manifest_mismatch_before_not_found(self):
        client = MagicMock()
        client.api.pull.side_effect = docker_service.DockerException(
            "no matching manifest for linux/arm64 in the manifest list entries: "
            "no match for platform in manifest: not found"
        )

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.pull("ghcr.io/ptero-eggs/yolks:wine_staging")

        assert result == {
            "ok": False,
            "error": "Docker Pull fehlgeschlagen: Image existiert, aber nicht fuer die Docker-Host-Plattform",
            "stdout": "",
            "stderr": "",
        }

    def test_pull_preserves_short_manifest_not_found_detail(self):
        client = MagicMock()
        client.api.pull.side_effect = docker_service.DockerException(
            "manifest unknown: failed to resolve reference ghcr.io/ptero-eggs/yolks:typo"
        )

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.pull("ghcr.io/ptero-eggs/yolks:typo")

        assert result == {
            "ok": False,
            "error": (
                "Docker Pull fehlgeschlagen: Image oder Tag in der Registry nicht gefunden: "
                "manifest unknown: failed to resolve reference ghcr.io/ptero-eggs/yolks:typo"
            ),
            "stdout": "",
            "stderr": "",
        }

    def test_pull_reports_stream_error(self):
        client = MagicMock()
        client.api.pull.return_value = [
            {"status": "Pulling from ptero-eggs/yolks"},
            {"error": "manifest unknown: failed to resolve reference"},
        ]

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.pull("ghcr.io/ptero-eggs/yolks:typo")

        assert result == {
            "ok": False,
            "error": (
                "Docker Pull fehlgeschlagen: Image oder Tag in der Registry nicht gefunden: "
                "manifest unknown: failed to resolve reference"
            ),
            "stdout": "",
            "stderr": "",
        }

    def test_pull_does_not_inspect_after_successful_stream(self):
        client = MagicMock()
        client.api.pull.return_value = [
            {"status": "Pulling from ptero-eggs/yolks"},
            {"status": "Digest: sha256:abc"},
        ]
        client.images.get.side_effect = docker_service.NotFound("No such image")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.pull("ghcr.io/ptero-eggs/yolks:wine_staging")

        assert result == {"ok": True, "stdout": "", "stderr": ""}
        client.images.get.assert_not_called()


class TestRunContainer:
    def test_builds_hardened_sdk_call(self):
        client = MagicMock()
        created = SimpleNamespace(id="abc123")
        client.images.get.side_effect = docker_service.NotFound("missing")
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = created

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_container(
                name="msm-srv-7",
                image="ghcr.io/parkervcp/steamcmd:debian",
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
        kwargs = client.containers.run.call_args.kwargs
        assert kwargs["image"] == "ghcr.io/parkervcp/steamcmd:debian"
        assert kwargs["command"] == ["/data/DayZServer", "-port=27015"]
        assert kwargs["name"] == "msm-srv-7"
        assert kwargs["stdin_open"] is True
        assert kwargs["restart_policy"] == {"Name": "on-failure", "MaximumRetryCount": 5}
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges"]
        assert kwargs["read_only"] is True
        assert kwargs["environment"] == {"FOO": "bar"}
        assert kwargs["ports"] == {"27015/udp": 27015}
        assert kwargs["volumes"] == {"/opt/msm/servers/7": {"bind": "/data", "mode": "rw"}}
        assert kwargs["nano_cpus"] == 2_000_000_000
        assert kwargs["mem_limit"] == "4096m"
        assert kwargs["memswap_limit"] == "4096m"
        assert kwargs["user"] == "1000:1000"
        assert kwargs["working_dir"] == "/data"
        client.images.get.assert_called_once_with("ghcr.io/parkervcp/steamcmd:debian")
        client.api.pull.assert_called_once_with(
            "ghcr.io/parkervcp/steamcmd", tag="debian", stream=True, decode=True, auth_config={}
        )
        calls = [call[0] for call in client.mock_calls]
        assert calls.index("images.get") < calls.index("api.pull") < calls.index("containers.run")

    def test_run_container_skips_pull_when_image_present_locally(self):
        """Fast-Path: Image ist bereits im lokalen Content-Store -> kein Registry-Roundtrip.

        Spart 10-60s Wartezeit pro Restart bei grossen Images (Wine/Proton, parkervcp).
        """
        client = MagicMock()
        client.images.get.return_value = SimpleNamespace(id="local-image")
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = SimpleNamespace(id="abc")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_container(
                name="msm-srv-1",
                image="ghcr.io/ptero-eggs/yolks:wine_staging",
                command=["x"],
                env={},
                volumes=[],
            )

        assert result["ok"] is True
        client.images.get.assert_called_once_with("ghcr.io/ptero-eggs/yolks:wine_staging")
        client.api.pull.assert_not_called()
        client.containers.run.assert_called_once()

    def test_run_container_uses_local_image_on_pull_failure_fallback(self):
        """Fallback: Image fehlt lokal, Pull schlaegt fehl, aber Image ist zwischenzeitlich
        verfuegbar (z. B. ein paralleler Job hat es gepullt). Dann darf der Container starten.
        """
        client = MagicMock()
        client.images.get.side_effect = [
            docker_service.NotFound("missing first"),
            SimpleNamespace(id="raced-image"),
        ]
        client.api.pull.side_effect = docker_service.DockerException("registry offline")
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = SimpleNamespace(id="abc")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_container(
                name="msm-srv-1",
                image="ghcr.io/ptero-eggs/yolks:wine_staging",
                command=["x"],
                env={},
                volumes=[],
            )

        assert result["ok"] is True
        assert client.images.get.call_count == 2
        client.images.get.assert_any_call("ghcr.io/ptero-eggs/yolks:wine_staging")
        client.api.pull.assert_called_once_with(
            "ghcr.io/ptero-eggs/yolks", tag="wine_staging", stream=True, decode=True, auth_config={}
        )
        client.containers.run.assert_called_once()

    def test_run_container_reports_immediate_exit_with_code_and_logs(self):
        client = MagicMock()
        container = MagicMock()
        container.id = "abc"
        container.attrs = {"State": {"Status": "exited", "ExitCode": 127}}
        container.logs.return_value = b"./DayZServer: error while loading shared libraries\n"
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = container

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)), \
             patch("services.docker_service.time.sleep"):
            result = docker_service.run_container(
                name="msm-srv-1",
                image="ghcr.io/parkervcp/steamcmd:debian",
                command=["./DayZServer"],
                startup_check_seconds=2.0,
            )

        assert result["ok"] is False
        assert result["exit_code"] == 127
        assert "Exit-Code 127" in result["error"]
        assert "loading shared libraries" in result["error"]
        container.reload.assert_called_once()

    def test_run_container_fails_clearly_when_remote_and_local_image_missing(self):
        client = MagicMock()
        existing = MagicMock()
        image = "ghcr.io/ptero-eggs/yolks:wine_staging"
        client.api.pull.side_effect = docker_service.DockerException("registry offline")
        client.images.get.side_effect = docker_service.NotFound("missing image")
        client.containers.get.return_value = existing

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_container(
                name="msm-srv-1",
                image=image,
                command=["x"],
                env={},
                volumes=[],
            )

        assert result == {
            "ok": False,
            "error": f"Docker-Image nicht verfügbar: {image} (Pull fehlgeschlagen: registry offline)",
            "stdout": "",
            "stderr": "",
        }
        client.containers.get.assert_not_called()
        client.containers.run.assert_not_called()
        existing.remove.assert_not_called()

    def test_bind_ip_in_port_publish(self):
        client = MagicMock()
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = SimpleNamespace(id="abc")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
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

        assert client.containers.run.call_args.kwargs["ports"] == {"27015/tcp": ("192.0.2.5", 27015)}

    def test_duplicate_port_publish(self):
        client = MagicMock()
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = SimpleNamespace(id="abc")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            docker_service.run_container(
                name="msm-srv-1",
                image="img",
                command=["x"],
                env={},
                ports=[
                    PortPublish(8080, 80, "tcp", None),
                    PortPublish(443, 80, "tcp", None)
                ],
                volumes=[],
                cpu_limit_percent=None,
                ram_limit_mb=None,
                user="1000:1000",
                workdir="/data",
            )

        assert client.containers.run.call_args.kwargs["ports"] == {"80/tcp": [8080, 443]}


class _StartPlugin(GamePlugin):
    game_id = "start-test"
    game_name = "Start Test"
    docker_image = "img"

    def install(self, server) -> dict:
        return {"ok": True}

    def build_container_command(self, server) -> list[str]:
        return ["./start.sh"]

    def build_port_publishes(self, server) -> list[PortPublish]:
        return []

    def build_volume_binds(self, server) -> list[VolumeBind]:
        return [VolumeBind(server.install_dir, "/home/container", read_only=False)]

    def container_workdir(self, server) -> str:
        return "/home/container"

    def get_status(self, server) -> ServerStatus:
        return ServerStatus(status="stopped")

    def get_logs(self, server, lines: int = 100) -> str:
        return ""

    def get_config_schema(self) -> list:
        return []

    def get_config_files(self) -> list[dict]:
        return []


class TestGamePluginStartPermissions:
    def test_start_repairs_bind_mount_before_running_container(self, tmp_path):
        plugin = _StartPlugin()
        server = SimpleNamespace(
            id=42,
            install_dir=str(tmp_path),
            cpu_limit_percent=None,
            ram_limit_mb=None,
        )
        calls: list[str] = []

        with patch("services.docker_service.is_available", return_value=True), \
             patch("games.base.docker_service.container_runtime_uid_gid", return_value=(1001, 1002)), \
             patch("games.base.docker_service.repair_bind_mount_permissions") as mock_repair, \
             patch.object(plugin, "prepare_runtime") as mock_prepare, \
             patch("games.base.docker_service.run_container", return_value={"ok": True, "stdout": "", "stderr": ""}) as mock_run:
            mock_repair.side_effect = lambda *args, **kwargs: calls.append("repair") or {"ok": True}
            mock_prepare.side_effect = lambda srv: calls.append("prepare")
            result = plugin.start(server)

        assert result["message"] == "Server gestartet"
        mock_repair.assert_called_once_with(
            str(tmp_path),
            container_path="/home/container",
            owner_uid_gid=(1001, 1002),
        )
        kwargs = mock_run.call_args.kwargs
        assert kwargs["user"] == "1001:1002"
        assert kwargs["volumes"] == [VolumeBind(str(tmp_path), "/home/container", read_only=False)]
        assert calls == ["repair", "prepare"]

    def test_start_stops_before_container_run_when_permission_repair_fails(self, tmp_path):
        plugin = _StartPlugin()
        server = SimpleNamespace(
            id=43,
            install_dir=str(tmp_path),
            cpu_limit_percent=None,
            ram_limit_mb=None,
        )

        with patch("services.docker_service.is_available", return_value=True), \
             patch("games.base.docker_service.container_runtime_uid_gid", return_value=(1001, 1002)), \
             patch(
                 "games.base.docker_service.repair_bind_mount_permissions",
                 return_value={"ok": False, "error": "repair failed"},
             ), \
             patch("games.base.docker_service.run_container") as mock_run:
            result = plugin.start(server)

        assert result == {"error": "repair failed"}
        mock_run.assert_not_called()



class TestLifecycle:
    def test_stop_returns_ok_when_not_exists(self):
        with patch.object(docker_service, "_container", return_value=None):
            result = docker_service.stop("missing")
        assert result["ok"] is True

    def test_remove_returns_ok_when_not_exists(self):
        with patch.object(docker_service, "_container", return_value=None):
            result = docker_service.remove("missing")
        assert result["ok"] is True


class TestDiskUsage:
    def test_du_returns_mb(self, tmp_path):
        with patch("services.docker_service.subprocess.run", return_value=_ok(stdout=f"104857600\t{tmp_path}\n")):
            mb = docker_service.disk_usage_mb(str(tmp_path))
        assert mb == 100

    def test_du_failure_returns_none(self, tmp_path):
        with patch("services.docker_service.subprocess.run", side_effect=FileNotFoundError):
            mb = docker_service.disk_usage_mb(str(tmp_path))
        assert mb is None

    def test_du_nonexistent_path_returns_none(self):
        assert docker_service.disk_usage_mb("/nonexistent-xyz-msm-test") is None


class TestHostUidGid:
    def test_returns_tuple(self):
        uid, gid = docker_service.host_uid_gid()
        assert isinstance(uid, int)
        assert isinstance(gid, int)


class TestBindMountPermissionRepair:
    def test_repair_chowns_only_when_runtime_owner_is_explicit(self, tmp_path):
        with patch("services.docker_service.run_ephemeral", return_value={"ok": True}) as mock_run:
            result = docker_service.repair_bind_mount_permissions(
                str(tmp_path),
                container_path="/home/container",
                owner_uid_gid=(1000, 1000),
            )

        assert result == {"ok": True}
        kwargs = mock_run.call_args.kwargs
        assert kwargs["volumes"] == [VolumeBind(str(tmp_path), "/home/container", read_only=False)]
        script = kwargs["command"][1]
        assert "chmod a+rwX" in script
        assert "chown 1000:1000" in script
        assert "chown -h 1000:1000" in script

    def test_repair_without_runtime_owner_preserves_existing_owner(self, tmp_path):
        with patch("services.docker_service.run_ephemeral", return_value={"ok": True}) as mock_run:
            result = docker_service.repair_bind_mount_permissions(str(tmp_path))

        assert result == {"ok": True}
        script = mock_run.call_args.kwargs["command"][1]
        assert "chmod a+rwX" in script
        assert "chown" not in script


class TestEphemeralRun:
    def test_ephemeral_run_captures_output_and_removes_container(self):
        client = MagicMock()
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.side_effect = [b"done\n", b""]
        client.images.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = container

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_ephemeral(
                image="ghcr.io/parkervcp/steamcmd:debian",
                command=["+force_install_dir", "/data", "+login", "anonymous", "+app_update", "223350", "+quit"],
                volumes=[VolumeBind("/opt/msm/servers/1", "/data", read_only=False)],
                env={},
                user="1000:1000",
                workdir="/data",
                timeout=600,
            )

        assert result == {"ok": True, "stdout": "done\n", "stderr": ""}
        kwargs = client.containers.run.call_args.kwargs
        assert kwargs["detach"] is True
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges"]
        assert kwargs["volumes"] == {"/opt/msm/servers/1": {"bind": "/data", "mode": "rw"}}
        container.remove.assert_called_once_with(force=True)
        client.images.get.assert_called_once_with("ghcr.io/parkervcp/steamcmd:debian")
        client.api.pull.assert_called_once_with(
            "ghcr.io/parkervcp/steamcmd", tag="debian", stream=True, decode=True, auth_config={}
        )

    def test_ephemeral_run_skips_pull_when_image_present_locally(self):
        """Fast-Path fuer SteamCMD-Install-Container: kein Registry-Roundtrip wenn lokal vorhanden."""
        client = MagicMock()
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.side_effect = [b"done\n", b""]
        client.images.get.return_value = SimpleNamespace(id="local-image")
        client.containers.run.return_value = container

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_ephemeral(
                image="ghcr.io/parkervcp/steamcmd:debian",
                command=["true"],
                volumes=[],
                env={},
            )

        assert result["ok"] is True
        client.images.get.assert_called_once_with("ghcr.io/parkervcp/steamcmd:debian")
        client.api.pull.assert_not_called()
        client.containers.run.assert_called_once()

    def test_ephemeral_run_fails_clearly_when_remote_and_local_image_missing(self):
        client = MagicMock()
        image = "ghcr.io/parkervcp/steamcmd:debian"
        client.api.pull.side_effect = docker_service.DockerException("registry offline")
        client.images.get.side_effect = docker_service.NotFound("missing image")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_ephemeral(
                image=image,
                command=["true"],
                volumes=[],
                env={},
            )

        assert result == {
            "ok": False,
            "error": f"Docker-Image nicht verfügbar: {image} (Pull fehlgeschlagen: registry offline)",
            "stdout": "",
            "stderr": "",
        }
        client.containers.run.assert_not_called()

    def test_unavailable_image_classifies_pull_auth_failure(self):
        client = MagicMock()
        image = "ghcr.io/ptero-eggs/yolks:wine_staging"
        client.api.pull.side_effect = docker_service.DockerException("unauthorized: authentication required")
        client.images.get.side_effect = docker_service.NotFound("missing image")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_container(
                name="msm-srv-1",
                image=image,
                command=["x"],
                env={},
                volumes=[],
            )

        assert result == {
            "ok": False,
            "error": f"Docker-Image nicht verfügbar: {image} (Pull fehlgeschlagen: Registry-Authentifizierung erforderlich)",
            "stdout": "",
            "stderr": "",
        }

    def test_ephemeral_run_failure_preserves_stdout_stderr(self):
        client = MagicMock()
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 2}
        container.logs.side_effect = [b"stdout details\n", b"stderr details\n"]
        client.containers.run.return_value = container

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_ephemeral(
                image="img",
                command=["false"],
                volumes=[],
                env={},
            )

        assert result["ok"] is False
        assert result["error"] == "stderr details"
        assert result["stdout"] == "stdout details\n"
        assert result["stderr"] == "stderr details\n"
        container.remove.assert_called_once_with(force=True)

    def test_ephemeral_run_streams_live_logs_to_callback(self):
        client = MagicMock()
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.return_value = iter([
            b"Update state (0x61) downloading, progress: 68.94\n",
            b"Update state (0x81) verifying update, progress: 10.24\n",
        ])
        client.containers.run.return_value = container
        lines: list[str] = []

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_ephemeral(
                image="ghcr.io/parkervcp/steamcmd:debian",
                command=["true"],
                volumes=[],
                env={},
                log_callback=lines.append,
            )

        assert result == {"ok": True, "stdout": "", "stderr": ""}
        assert lines == [
            "Update state (0x61) downloading, progress: 68.94\n",
            "Update state (0x81) verifying update, progress: 10.24\n",
        ]
        container.logs.assert_called_once_with(stream=True, follow=True, stdout=True, stderr=True)
        container.remove.assert_called_once_with(force=True)

    def test_cap_adds_are_passed_to_sdk(self):
        client = MagicMock()
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.side_effect = [b"", b""]
        client.containers.run.return_value = container

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            docker_service.run_ephemeral(
                image="ghcr.io/parkervcp/steamcmd:debian",
                command=["-c", "true"],
                volumes=[],
                env={},
                user="0:0",
                entrypoint="bash",
                cap_adds=["DAC_OVERRIDE", "CHOWN", "FOWNER"],
            )

        kwargs = client.containers.run.call_args.kwargs
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["cap_add"] == ["DAC_OVERRIDE", "CHOWN", "FOWNER"]


class TestExecAndLogs:
    def test_send_stdin_uses_sdk_exec_without_logging_data(self):
        raw_socket = MagicMock()
        raw_socket.recv.side_effect = [b"", b""]
        exec_socket = SimpleNamespace(_sock=raw_socket)
        container = SimpleNamespace(id="container-id", status="running")
        client = MagicMock()
        client.api.exec_create.return_value = {"Id": "exec-id"}
        client.api.exec_start.return_value = exec_socket
        client.api.exec_inspect.return_value = {"ExitCode": 0}

        with patch.object(docker_service, "_container", return_value=container), \
             patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.send_stdin("msm-srv-1", "secret input\n")

        assert result["ok"] is True
        client.api.exec_create.assert_called_once()
        assert client.api.exec_create.call_args.args[1] == ["sh", "-c", "cat > /proc/1/fd/0"]
        raw_socket.sendall.assert_called_once_with(b"secret input\n")

    def test_stream_logs_yields_subprocess_lines(self):
        mock_proc = MagicMock()
        mock_stdout = MagicMock()
        lines_iterator = iter([b"line 1\n", b"line 2\n", b""])
        async def mock_readline():
            try:
                return next(lines_iterator)
            except StopIteration:
                return b""
        mock_stdout.readline = mock_readline
        mock_proc.stdout = mock_stdout
        mock_proc.returncode = 0

        async def mock_create_subprocess(*args, **kwargs):
            return mock_proc

        async def run_test():
            lines = []
            async for line in docker_service.stream_logs("msm-srv-1", tail=200):
                lines.append(line)
            return lines

        with patch("services.docker_service.is_available", return_value=True), \
             patch("services.docker_service.exists", return_value=True), \
             patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess) as mock_exec:
            lines = asyncio.run(run_test())

        assert lines == ["line 1", "line 2"]
        mock_exec.assert_called_once()
        args = mock_exec.call_args.args
        assert args[0] == "docker"
        assert args[1] == "logs"
        assert "--follow" in args
        assert "--tail" in args
        assert "msm-srv-1" in args




class TestSteamCMDHelpers:
    """SteamCMD bleibt ein Security-Pfad: Shell-Quoting und Host-UID-Chown muessen halten."""

    def test_steamcmd_install_runs_as_root_and_chowns(self, tmp_path):
        from games.base import STEAMCMD_BIN, STEAMCMD_CAPS, run_steamcmd_install

        with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
             patch("games.base.docker_service.is_rootless", return_value=False), \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
            mock_eph.return_value = {"ok": True, "stdout": "ok", "stderr": ""}
            run_steamcmd_install(server_id=1, install_dir=str(tmp_path), app_id="223350")

        kwargs = mock_eph.call_args.kwargs
        assert kwargs["entrypoint"] == "bash"
        assert kwargs["command"][0] == "-c"
        script = kwargs["command"][1]
        assert STEAMCMD_BIN in script
        assert "+app_update" in script and "223350" in script
        assert "chown -R 1001:1001 /data" in script
        assert "exit $rc" in script
        assert kwargs.get("user") == "0:0"
        assert kwargs.get("cap_adds") == STEAMCMD_CAPS
        assert kwargs["env"].get("HOME") == "/data"

    def test_workshop_download_runs_as_root_and_chowns(self, tmp_path):
        from games.base import STEAMCMD_CAPS, run_steamcmd_workshop_download

        with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
             patch("games.base.docker_service.is_rootless", return_value=False), \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
            mock_eph.return_value = {"ok": True, "stdout": "ok", "stderr": ""}
            run_steamcmd_workshop_download(
                server_id=1, install_dir=str(tmp_path), workshop_app_id="221100", workshop_item_id="12345"
            )

        kwargs = mock_eph.call_args.kwargs
        assert kwargs["entrypoint"] == "bash"
        script = kwargs["command"][1]
        assert "+workshop_download_item" in script and "221100" in script and "12345" in script
        assert "chown -R 1001:1001 /data" in script
        assert kwargs.get("user") == "0:0"
        assert kwargs.get("cap_adds") == STEAMCMD_CAPS
        assert kwargs["env"].get("HOME") == "/data"

    def test_workshop_batch_download_uses_one_ephemeral_container_for_many_mods(self, tmp_path):
        from games.base import run_steamcmd_workshop_download_batch

        item_ids = [str(1000 + i) for i in range(20)]

        def mark_downloaded(**_kwargs):
            for item_id in item_ids:
                mod_dir = tmp_path / "steamapps" / "workshop" / "content" / "221100" / item_id
                mod_dir.mkdir(parents=True, exist_ok=True)
                (mod_dir / "mod.bin").write_text("synthetic", encoding="utf-8")
            return {"ok": True, "stdout": "ok", "stderr": ""}

        with patch("games.base.docker_service.run_ephemeral", side_effect=mark_downloaded) as mock_eph, \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
            result = run_steamcmd_workshop_download_batch(
                server_id=1,
                install_dir=str(tmp_path),
                workshop_app_id="221100",
                workshop_item_ids=item_ids,
            )

        assert result["ok"] is True
        assert result["applied"] == 20
        mock_eph.assert_called_once()
        script = mock_eph.call_args.kwargs["command"][1]
        assert script.count("+workshop_download_item") == 20

    def test_single_workshop_download_surfaces_item_error(self, tmp_path):
        from games.base import run_steamcmd_workshop_download

        with patch(
            "games.base.run_steamcmd_workshop_download_batch",
            return_value={
                "ok": False,
                "error": "batch_error",
                "items": {"12345": {"ok": False, "error": "item_error"}},
            },
        ):
            result = run_steamcmd_workshop_download(
                server_id=1,
                install_dir=str(tmp_path),
                workshop_app_id="221100",
                workshop_item_id="12345",
            )

        assert result["ok"] is False
        assert result["error"] == "item_error"

    def test_bash_script_is_safely_quoted(self, tmp_path):
        from games.base import run_steamcmd_install

        with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
             patch("games.base.docker_service.is_rootless", return_value=False), \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
            mock_eph.return_value = {"ok": True, "stdout": "", "stderr": ""}
            run_steamcmd_install(
                server_id=1,
                install_dir=str(tmp_path),
                app_id="223350",
                extra_args=["+app_set_config", "value with spaces; rm -rf /"],
            )

        script = mock_eph.call_args.kwargs["command"][1]
        assert "rm -rf /" in script
        assert script.count("chown -R 1001:1001 /data") == 1

    def test_steamcmd_install_chowns_runtime_user_in_rootless_docker(self, tmp_path):
        from games.base import run_steamcmd_install

        with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1002)):
            mock_eph.return_value = {"ok": True, "stdout": "ok", "stderr": ""}
            run_steamcmd_install(server_id=1, install_dir=str(tmp_path), app_id="223350")

        script = mock_eph.call_args.kwargs["command"][1]
        assert "chown -R 1001:1002 /data" in script
