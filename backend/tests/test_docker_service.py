"""Unit-Tests fuer den Rootless-Docker-SDK-Adapter."""

from __future__ import annotations

import asyncio
import logging
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
        assert kwargs["restart_policy"] == {"Name": "no"}
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

    def test_run_container_default_no_tty(self):
        """Default: kein TTY (verhindert Bruit-to-Game-Output-Corruption bei normalen Servern)."""
        client = MagicMock()
        client.images.get.return_value = SimpleNamespace(id="local-image")
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = SimpleNamespace(id="abc")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            docker_service.run_container(
                name="msm-srv-1",
                image="ghcr.io/natroutter/egg-hytale:latest",
            )

        kwargs = client.containers.run.call_args.kwargs
        assert kwargs["tty"] is False

    def test_run_container_tty_true_when_requested(self):
        """Opt-in: tty=True wird durchgereicht fuer interaktive Auth-Recovery-Container."""
        client = MagicMock()
        client.images.get.return_value = SimpleNamespace(id="local-image")
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = SimpleNamespace(id="abc")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            docker_service.run_container(
                name="msm-srv-1",
                image="ghcr.io/natroutter/egg-hytale:latest",
                tty=True,
            )

        kwargs = client.containers.run.call_args.kwargs
        assert kwargs["tty"] is True
        assert kwargs["stdin_open"] is True  # both are needed for interactive flow

    def test_run_container_sets_requested_primary_network(self):
        client = MagicMock()
        client.images.get.return_value = SimpleNamespace(id="local-image")
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = SimpleNamespace(id="abc")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_container(
                name="msm-srv-1",
                image="postgres:17-alpine",
                network="msm-internal",
            )

        assert result["ok"] is True
        kwargs = client.containers.run.call_args.kwargs
        assert kwargs["network"] == "msm-internal"

    def test_run_container_connects_extra_network_after_start(self):
        client = MagicMock()
        created = SimpleNamespace(id="abc")
        network = MagicMock()
        client.images.get.return_value = SimpleNamespace(id="local-image")
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = created
        client.networks.get.return_value = network

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            result = docker_service.run_container(
                name="msm-srv-1",
                image="ghcr.io/ptero-eggs/yolks:wine_staging",
                extra_networks=["msm-internal"],
            )

        assert result["ok"] is True
        kwargs = client.containers.run.call_args.kwargs
        assert "network" not in kwargs
        network.connect.assert_called_once_with(created)

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

    def test_start_continues_with_warning_when_permission_repair_fails(self, tmp_path):
        """Bei Repair-Fehler wird der Start NICHT hart abgebrochen — best-effort.

        Hintergrund: unter Rootless Docker schlägt ``chown`` auf manchen
        Dateien mit EPERM fehl, ohne dass wir das auf Application-Ebene
        sicher beheben können. Der Start läuft weiter und die Warnung landet
        im Server-Console-Log. (Siehe ``references/msm-permission-repair-chmod-eperm-rootless.md``.)
        """
        plugin = _StartPlugin()
        server = SimpleNamespace(
            id=43,
            install_dir=str(tmp_path),
            cpu_limit_percent=None,
            ram_limit_mb=None,
        )
        calls: list[str] = []

        with patch("services.docker_service.is_available", return_value=True), \
             patch("games.base.docker_service.container_runtime_uid_gid", return_value=(1001, 1002)), \
             patch(
                 "games.base.docker_service.repair_bind_mount_permissions",
                 return_value={"ok": False, "error": "repair failed"},
             ), \
             patch.object(plugin, "prepare_runtime") as mock_prepare, \
             patch(
                 "games.base.docker_service.run_container",
                 return_value={"ok": True, "stdout": "", "stderr": ""},
             ) as mock_run:
            mock_prepare.side_effect = lambda srv: calls.append("prepare")
            mock_run.side_effect = lambda **kwargs: calls.append("run") or {"ok": True}
            result = plugin.start(server)

        # Server startet trotzdem (best-effort), nicht mit Fehler abbrechen
        assert result["message"] == "Server gestartet"
        assert "container" in result
        # repair (mit ok=False) + prepare + run sind alle durchgelaufen
        assert calls == ["prepare", "run"]
        mock_prepare.assert_called_once_with(server)
        mock_run.assert_called_once()



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

        kwargs = mock_eph.call_args_list[0].kwargs
        assert kwargs["entrypoint"] == "bash"
        assert kwargs["command"][0] == "-c"
        script = kwargs["command"][1]
        assert STEAMCMD_BIN in script
        assert "+app_update" in script and "223350" in script
        assert "chown -R " in script and "/data" in script
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

        kwargs = mock_eph.call_args_list[0].kwargs
        assert kwargs["entrypoint"] == "bash"
        script = kwargs["command"][1]
        assert "+workshop_download_item" in script and "221100" in script and "12345" in script
        assert "chown -R " in script and "/data" in script
        assert kwargs.get("user") == "0:0"
        assert kwargs.get("cap_adds") == STEAMCMD_CAPS
        assert kwargs["env"].get("HOME") == "/data"

    def test_workshop_batch_download_uses_one_ephemeral_container_for_many_mods(self, tmp_path):
        """Ein Workshop-Batch = genau EIN SteamCMD-Container + ein Repair-Pass.

        Hintergrund: seit dem Rootless-Docker-Bind-Mount-Visibility-Fix
        ruft ``run_steamcmd_workshop_download_batch`` nach dem SteamCMD-Lauf
        zusätzlich ``repair_bind_mount_permissions`` auf, damit das OverlayFS
        die Workshop-Ordner sofort hostseitig sichtbar macht. Der eigentliche
        Workshop-Batch bleibt aber EIN Container — das ist hier die zu
        sichernde Invariante.
        (Siehe ``references/msm-steam-workshop-batch-download-rootless-verification.md``.)
        """
        from games.base import run_steamcmd_workshop_download_batch

        item_ids = [str(1000 + i) for i in range(20)]

        def mark_downloaded(**_kwargs):
            for item_id in item_ids:
                mod_dir = tmp_path / "steamapps" / "workshop" / "content" / "221100" / item_id
                mod_dir.mkdir(parents=True, exist_ok=True)
                (mod_dir / "mod.bin").write_text("synthetic", encoding="utf-8")
            return {"ok": True, "stdout": "ok", "stderr": ""}

        with patch("games.base.docker_service.run_ephemeral", side_effect=mark_downloaded) as mock_eph, \
             patch("games.base.docker_service.container_runtime_uid_gid", return_value=(1001, 1001)), \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
            result = run_steamcmd_workshop_download_batch(
                server_id=1,
                install_dir=str(tmp_path),
                workshop_app_id="221100",
                workshop_item_ids=item_ids,
            )

        assert result["ok"] is True
        assert result["applied"] == 20

        # Der Workshop-Batch-Container darf nur einmal gestartet werden
        workshop_calls = [
            c for c in mock_eph.call_args_list
            if c.kwargs.get("command") and "+workshop_download_item" in c.kwargs["command"][1]
        ]
        assert len(workshop_calls) == 1, (
            f"Workshop-Batch-Container muss genau einmal laufen, "
            f"gefunden: {len(workshop_calls)}"
        )
        script = workshop_calls[0].kwargs["command"][1]
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

        script = mock_eph.call_args_list[0].kwargs["command"][1]
        assert "rm -rf /" in script
        assert script.count("chown -R ") == 1

    def test_steamcmd_install_chowns_runtime_user_in_rootless_docker(self, tmp_path):
        from games.base import run_steamcmd_install

        with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
             patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1002)):
            mock_eph.return_value = {"ok": True, "stdout": "ok", "stderr": ""}
            run_steamcmd_install(server_id=1, install_dir=str(tmp_path), app_id="223350")

        script = mock_eph.call_args_list[0].kwargs["command"][1]
        assert "chown -R " in script and "/data" in script


class TestUpdateContainerResources:
    """Unit-Tests fuer ``docker_service.update_container_resources``.

    Verifiziert die Docker-SDK-Update-Payloads (VAL-DOCKER-001..009):
    - CPU-Prozent -> cpu_period/cpu_quota Mapping
    - RAM-MB -> mem_limit/memswap_limit Mapping
    - None (unlimitiert) -> Limits loeschen
    - Warnungen und Exceptions werden als Fehler behandelt
    - Keine stop/remove/run/restart-Aufrufe
    """

    @pytest.mark.parametrize("cpu_percent,expected_quota", [
        (10, 10_000),
        (50, 50_000),
        (100, 100_000),
        (200, 200_000),
        (3200, 3_200_000),
    ])
    def test_cpu_percent_maps_to_docker_quota(self, cpu_percent, expected_quota):
        """VAL-DOCKER-001, VAL-DOCKER-007: CPU percent -> cpu_period=100000, cpu_quota=percent*1000."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": cpu_percent},
            )

        assert result == {"ok": True}
        kwargs = container.update.call_args.kwargs
        assert kwargs["cpu_period"] == 100000
        assert kwargs["cpu_quota"] == expected_quota
        # Kein Restart/Recreate: nur update() wurde aufgerufen
        container.stop.assert_not_called()
        container.remove.assert_not_called()
        container.start.assert_not_called()

    @pytest.mark.parametrize("ram_mb,expected", [
        (512, "512m"),
        (4096, "4096m"),
        (8192, "8192m"),
    ])
    def test_ram_mb_maps_to_docker_memory_limits(self, ram_mb, expected):
        """VAL-DOCKER-002, VAL-DOCKER-007: RAM MB -> mem_limit + memswap_limit, keine CPU-Aenderung."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"ram_limit_mb": ram_mb},
            )

        assert result == {"ok": True}
        kwargs = container.update.call_args.kwargs
        assert kwargs["mem_limit"] == expected
        assert kwargs["memswap_limit"] == expected
        # Keine CPU-Felder beim reinen RAM-Update
        assert "cpu_period" not in kwargs
        assert "cpu_quota" not in kwargs
        container.stop.assert_not_called()

    def test_clearing_cpu_applies_unlimited_quota(self):
        """VAL-DOCKER-003: CPU None -> cpu_quota=0 (unlimitiert), ohne Restart."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": None},
            )

        assert result == {"ok": True}
        kwargs = container.update.call_args.kwargs
        assert kwargs["cpu_period"] == 100000
        assert kwargs["cpu_quota"] == 0
        container.stop.assert_not_called()

    def test_clearing_ram_clears_memory_and_memswap(self):
        """VAL-DOCKER-008: RAM None -> mem_limit=0, memswap_limit=-1 (beide Limiters geloescht)."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"ram_limit_mb": None},
            )

        assert result == {"ok": True}
        kwargs = container.update.call_args.kwargs
        assert kwargs["mem_limit"] == 0
        assert kwargs["memswap_limit"] == -1
        container.stop.assert_not_called()

    def test_combined_cpu_ram_update_sends_both(self):
        """VAL-DOCKER-004: CPU+RAM zusammen werden in einem Update-Aufruf gesendet."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1",
                {"cpu_limit_percent": 200, "ram_limit_mb": 4096},
            )

        assert result == {"ok": True}
        kwargs = container.update.call_args.kwargs
        assert kwargs["cpu_period"] == 100000
        assert kwargs["cpu_quota"] == 200_000
        assert kwargs["mem_limit"] == "4096m"
        assert kwargs["memswap_limit"] == "4096m"
        # Nur ein Update-Aufruf (atomar)
        assert container.update.call_count == 1

    def test_empty_updates_returns_ok_without_docker_call(self):
        """Keine Aenderungen -> kein Docker-Aufruf."""
        container = MagicMock()

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources("msm-srv-1", {})

        assert result == {"ok": True}
        container.update.assert_not_called()

    def test_container_not_found_returns_error(self):
        """Container existiert nicht -> sanitierter Fehler."""
        with patch.object(docker_service, "_container", return_value=None):
            result = docker_service.update_container_resources(
                "msm-srv-missing", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        assert "Container" in result["error"]
        assert "msm-srv-missing" not in result["error"]

    def test_docker_exception_returns_sanitized_error(self):
        """VAL-DOCKER-005: Docker-Ausnahme -> sanitierter Fehler, keine Secrets."""
        container = MagicMock()
        sentinel = "ZZLEAKSENTINEL_docker.sock_/var/run/docker.sock"
        container.update.side_effect = docker_service.DockerException(sentinel)

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        assert sentinel not in result["error"]
        assert "docker.sock" not in result["error"]

    def test_docker_warnings_treated_as_failure(self):
        """VAL-DOCKER-009: Docker-Warnings -> Fehler, keine Persistenz-Drift."""
        container = MagicMock()
        container.update.return_value = {"Warnings": ["unsupported cgroup controller"]}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        assert "Ressourcen" in result["error"]
        # Warning-Inhalt darf nicht durchsickern
        assert "cgroup" not in result["error"]

    def test_docker_warnings_empty_list_is_success(self):
        """Leere Warnings-Liste ist Erfolg (Docker liefert immer Warnings-Key)."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200, "ram_limit_mb": 4096},
            )

        assert result == {"ok": True}

    def test_no_stop_remove_or_restart_calls(self):
        """VAL-DOCKER-001: Live-Update ruft nie stop/remove/restart auf."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            docker_service.update_container_resources(
                "msm-srv-1",
                {"cpu_limit_percent": 200, "ram_limit_mb": 4096},
            )

        container.update.assert_called_once()
        container.stop.assert_not_called()
        container.remove.assert_not_called()
        container.start.assert_not_called()
        container.restart.assert_not_called()

    def test_only_changed_fields_sent_to_docker(self):
        """VAL-DOCKER-002: Unverwandte Limits werden nicht ungewollt geaendert."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result == {"ok": True}
        kwargs = container.update.call_args.kwargs
        assert "mem_limit" not in kwargs
        assert "memswap_limit" not in kwargs


class TestUpdateContainerResourcesWarningRestore:
    """VAL-DOCKER-009: Docker warning/partial-success restore old limits to prevent drift.

    Regression tests for the scrutiny blocker where Docker warnings or
    SDK-shaped partial-success responses can leave Docker runtime limits
    changed while the DB rolls back. The fix captures old Docker limits
    before the update and restores them when warnings occur (compensation).
    """

    def test_warnings_trigger_restore_of_old_cpu_limits(self):
        """When Docker returns warnings, old CPU limits are restored via a
        second update() call with the pre-update values."""
        container = MagicMock()
        container.attrs = {
            "HostConfig": {
                "CpuPeriod": 100000,
                "CpuQuota": 100000,  # old: 100%
                "Memory": 0,
                "MemorySwap": 0,
            }
        }
        container.update.return_value = {"Warnings": ["unsupported cgroup controller"]}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        # Two update calls: first with new values, second with old values (restore)
        assert container.update.call_count == 2
        # First call: new values
        first_kwargs = container.update.call_args_list[0].kwargs
        assert first_kwargs["cpu_period"] == 100000
        assert first_kwargs["cpu_quota"] == 200_000
        # Second call (restore): old values from HostConfig
        restore_kwargs = container.update.call_args_list[1].kwargs
        assert restore_kwargs["cpu_period"] == 100000
        assert restore_kwargs["cpu_quota"] == 100000

    def test_warnings_trigger_restore_of_old_ram_limits(self):
        """When Docker returns warnings, old RAM limits are restored in bytes."""
        container = MagicMock()
        old_mem_bytes = 4096 * 1024 * 1024  # 4096 MB in bytes
        container.attrs = {
            "HostConfig": {
                "CpuPeriod": 0,
                "CpuQuota": 0,
                "Memory": old_mem_bytes,
                "MemorySwap": old_mem_bytes,
            }
        }
        container.update.return_value = {"Warnings": ["memory cgroup not available"]}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"ram_limit_mb": 8192},
            )

        assert result["ok"] is False
        assert container.update.call_count == 2
        # Restore: old RAM values (raw bytes from HostConfig)
        restore_kwargs = container.update.call_args_list[1].kwargs
        assert restore_kwargs["mem_limit"] == old_mem_bytes
        assert restore_kwargs["memswap_limit"] == old_mem_bytes

    def test_combined_cpu_ram_warnings_restore_both(self):
        """VAL-DOCKER-004: Combined CPU+RAM warning restores both old limits."""
        container = MagicMock()
        old_mem_bytes = 2048 * 1024 * 1024  # 2048 MB in bytes
        container.attrs = {
            "HostConfig": {
                "CpuPeriod": 100000,
                "CpuQuota": 100000,  # old: 100%
                "Memory": old_mem_bytes,
                "MemorySwap": old_mem_bytes,
            }
        }
        container.update.return_value = {"Warnings": ["cgroup controller not available"]}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200, "ram_limit_mb": 4096},
            )

        assert result["ok"] is False
        assert container.update.call_count == 2
        restore_kwargs = container.update.call_args_list[1].kwargs
        assert restore_kwargs["cpu_period"] == 100000
        assert restore_kwargs["cpu_quota"] == 100000
        assert restore_kwargs["mem_limit"] == old_mem_bytes
        assert restore_kwargs["memswap_limit"] == old_mem_bytes

    def test_successful_update_does_not_restore(self):
        """Successful update (no warnings) does not trigger a restore call."""
        container = MagicMock()
        container.attrs = {"HostConfig": {"CpuPeriod": 100000, "CpuQuota": 100000}}
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result == {"ok": True}
        assert container.update.call_count == 1

    def test_exception_does_not_trigger_restore(self):
        """Docker exception does not trigger restore (limits likely not applied)."""
        container = MagicMock()
        container.attrs = {"HostConfig": {"CpuPeriod": 100000, "CpuQuota": 100000}}
        container.update.side_effect = docker_service.DockerException("connection refused")

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        assert container.update.call_count == 1

    def test_restore_failure_still_returns_sanitized_failure(self):
        """If restore also raises an exception, still return sanitized failure."""
        container = MagicMock()
        container.attrs = {"HostConfig": {"CpuPeriod": 100000, "CpuQuota": 100000}}
        container.update.side_effect = [
            {"Warnings": ["cgroup error"]},
            docker_service.DockerException("restore connection refused"),
        ]

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        assert "Ressourcen" in result["error"]
        # No raw exception content leaks into the error
        assert "restore" not in result["error"].lower()
        assert "connection" not in result["error"].lower()

    def test_restore_with_warnings_still_returns_sanitized_failure(self):
        """If restore also returns warnings, still return sanitized failure."""
        container = MagicMock()
        container.attrs = {"HostConfig": {"CpuPeriod": 100000, "CpuQuota": 100000}}
        # Both the update and the restore return warnings
        container.update.return_value = {"Warnings": ["still broken"]}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        assert "Ressourcen" in result["error"]
        assert "broken" not in result["error"]
        assert "cgroup" not in result["error"]

    def test_warning_error_does_not_leak_warning_content(self):
        """VAL-DOCKER-009: Error response does not leak raw warning internals."""
        container = MagicMock()
        container.attrs = {"HostConfig": {"CpuPeriod": 100000, "CpuQuota": 100000}}
        container.update.return_value = {
            "Warnings": ["cgroup v2 controller not delegated, /sys/fs/cgroup path missing"]
        }

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        assert "cgroup" not in result["error"]
        assert "/sys/fs" not in result["error"]
        assert "controller" not in result["error"]

    def test_warning_log_does_not_leak_warning_content(self, caplog):
        """VAL-DOCKER-009: Logs do not leak raw warning internals."""
        container = MagicMock()
        container.attrs = {"HostConfig": {"CpuPeriod": 100000, "CpuQuota": 100000}}
        leak_sentinel = "ZZLEAKSENTINEL_/sys/fs/cgroup/controller_missing"
        container.update.return_value = {"Warnings": [leak_sentinel]}

        with patch.object(docker_service, "_container", return_value=container):
            with caplog.at_level(logging.WARNING):
                result = docker_service.update_container_resources(
                    "msm-srv-1", {"cpu_limit_percent": 200},
                )

        assert result["ok"] is False
        log_text = caplog.text
        assert leak_sentinel not in log_text
        assert "ZZLEAKSENTINEL" not in log_text
        assert "/sys/fs" not in log_text

    def test_reload_failure_skips_restore_gracefully(self):
        """If container.reload() fails, skip restore (best-effort, no crash)."""
        container = MagicMock()
        container.reload.side_effect = docker_service.DockerException("reload failed")
        container.update.return_value = {"Warnings": ["cgroup error"]}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},
            )

        assert result["ok"] is False
        # Only one update call (no restore because reload failed)
        assert container.update.call_count == 1

    def test_clearing_cpu_warning_restores_old_limited_value(self):
        """Warning when clearing CPU restores old CPU limit (not the cleared value)."""
        container = MagicMock()
        container.attrs = {
            "HostConfig": {
                "CpuPeriod": 100000,
                "CpuQuota": 200000,  # old: 200%
            }
        }
        container.update.return_value = {"Warnings": ["cgroup error"]}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": None},  # clearing to unlimited
            )

        assert result["ok"] is False
        assert container.update.call_count == 2
        # Restore: old values (200% = 200000 quota), not the cleared value (0)
        restore_kwargs = container.update.call_args_list[1].kwargs
        assert restore_kwargs["cpu_period"] == 100000
        assert restore_kwargs["cpu_quota"] == 200000

    def test_clearing_ram_warning_restores_old_limited_value(self):
        """Warning when clearing RAM restores old RAM limit (not the cleared value)."""
        container = MagicMock()
        old_mem_bytes = 2048 * 1024 * 1024
        container.attrs = {
            "HostConfig": {
                "Memory": old_mem_bytes,
                "MemorySwap": old_mem_bytes,
            }
        }
        container.update.return_value = {"Warnings": ["cgroup error"]}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1", {"ram_limit_mb": None},  # clearing to unlimited
            )

        assert result["ok"] is False
        assert container.update.call_count == 2
        restore_kwargs = container.update.call_args_list[1].kwargs
        assert restore_kwargs["mem_limit"] == old_mem_bytes
        assert restore_kwargs["memswap_limit"] == old_mem_bytes

    def test_restore_only_touches_changed_fields(self):
        """Restore only includes fields that were in the original update (VAL-DOCKER-002)."""
        container = MagicMock()
        container.attrs = {
            "HostConfig": {
                "CpuPeriod": 100000,
                "CpuQuota": 100000,
                "Memory": 2147483648,
                "MemorySwap": 2147483648,
            }
        }
        container.update.return_value = {"Warnings": ["cgroup error"]}

        with patch.object(docker_service, "_container", return_value=container):
            docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200},  # only CPU, not RAM
            )

        assert container.update.call_count == 2
        restore_kwargs = container.update.call_args_list[1].kwargs
        # CPU fields restored
        assert "cpu_period" in restore_kwargs
        assert "cpu_quota" in restore_kwargs
        # RAM fields NOT in restore (only CPU was changed)
        assert "mem_limit" not in restore_kwargs
        assert "memswap_limit" not in restore_kwargs

    def test_no_stop_remove_or_restart_during_restore(self):
        """VAL-DOCKER-005: Restore never calls stop/remove/restart."""
        container = MagicMock()
        container.attrs = {"HostConfig": {"CpuPeriod": 100000, "CpuQuota": 100000}}
        container.update.return_value = {"Warnings": ["cgroup error"]}

        with patch.object(docker_service, "_container", return_value=container):
            docker_service.update_container_resources(
                "msm-srv-1", {"cpu_limit_percent": 200, "ram_limit_mb": 4096},
            )

        container.stop.assert_not_called()
        container.remove.assert_not_called()
        container.start.assert_not_called()
        container.restart.assert_not_called()


# ── VAL-DOCKER-010: Disk limit is never sent as Docker hard quota ──────


class TestDiskLimitNeverDockerHardQuota:
    """Verifies that disk_limit_gb is never passed as a Docker storage quota,
    overlay size, device-mapper size, or equivalent hard-quota argument during
    create, live update, or recreate paths (VAL-DOCKER-010, VAL-DISK-004).
    """

    def test_run_container_no_storage_opt_in_kwargs(self):
        """run_container never passes storage_opt, disk_quota, or size to Docker SDK."""
        client = MagicMock()
        client.images.get.return_value = SimpleNamespace(id="local-image")
        client.containers.get.side_effect = docker_service.NotFound("missing")
        client.containers.run.return_value = SimpleNamespace(id="abc")

        with patch.object(docker_service, "_client_or_error", return_value=(client, None)):
            docker_service.run_container(
                name="msm-srv-1",
                image="ghcr.io/parkervcp/steamcmd:debian",
                command=["x"],
                env={},
                volumes=[],
                cpu_limit_percent=100,
                ram_limit_mb=2048,
            )

        kwargs = client.containers.run.call_args.kwargs
        # No storage quota keys whatsoever
        assert "storage_opt" not in kwargs
        assert "storage_opts" not in kwargs
        assert "disk_quota" not in kwargs
        assert "disk_limit_gb" not in kwargs
        assert "size" not in kwargs
        assert "shm_size" not in kwargs or kwargs.get("shm_size") is None

    def test_run_container_signature_has_no_disk_limit_param(self):
        """run_container does not accept a disk_limit_gb parameter at all."""
        import inspect
        sig = inspect.signature(docker_service.run_container)
        assert "disk_limit_gb" not in sig.parameters
        assert "storage_opt" not in sig.parameters
        assert "storage_quota" not in sig.parameters

    def test_update_container_resources_ignores_disk_limit(self):
        """update_container_resources does not handle disk_limit_gb at all.
        If disk_limit_gb is passed, it is silently ignored (no storage_opt
        in update kwargs)."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1",
                {"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
            )

        assert result == {"ok": True}
        kwargs = container.update.call_args.kwargs
        # No storage quota keys in update payload
        assert "storage_opt" not in kwargs
        assert "storage_opts" not in kwargs
        assert "disk_quota" not in kwargs
        assert "size" not in kwargs
        # Only CPU and RAM fields are present
        assert "cpu_period" in kwargs
        assert "mem_limit" in kwargs

    def test_update_container_resources_disk_only_is_noop(self):
        """Passing only disk_limit_gb to update_container_resources is a no-op
        (no Docker call at all, since disk is a soft limit)."""
        container = MagicMock()
        container.update.return_value = {"Warnings": []}

        with patch.object(docker_service, "_container", return_value=container):
            result = docker_service.update_container_resources(
                "msm-srv-1",
                {"disk_limit_gb": 50},
            )

        # No CPU/RAM fields -> no update_kwargs -> no Docker call
        assert result == {"ok": True}
        container.update.assert_not_called()
