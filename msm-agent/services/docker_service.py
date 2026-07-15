"""Rootless Docker SDK wrapper for the MSM Agent.

Hardening (mirrors panel backend/services/docker_service.py):
- privileged=False always
- cap_drop=["ALL"]
- security_opt=["no-new-privileges"]
- no host networking
- resource limits applied when provided

Secrets, env values, and stdin payloads are never logged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

try:
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound, NotFound
    from docker.types import LogConfig
except ImportError:  # pragma: no cover
    docker = None  # type: ignore[assignment]
    APIError = DockerException = ImageNotFound = NotFound = Exception  # type: ignore[misc,assignment]
    LogConfig = None  # type: ignore[assignment]

_HARDENING_CAP_DROP = ["ALL"]
_HARDENING_SECURITY_OPT = ["no-new-privileges"]
_LOG_CONFIG = {"max-size": "10m", "max-file": "3"}

_CLIENT: Any | None = None


class DockerUnavailableError(Exception):
    def __init__(self, message: str = "Docker daemon not available") -> None:
        super().__init__(message)
        self.message = message


class ContainerNameError(Exception):
    def __init__(self, message: str = "Invalid container name") -> None:
        super().__init__(message)
        self.message = message


class HardeningError(Exception):
    """Raised when a create request tries to weaken container security."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _rootless_socket_path() -> str:
    if hasattr(os, "getuid"):
        return f"/run/user/{os.getuid()}/docker.sock"
    return "/run/user/0/docker.sock"


def resolve_docker_host() -> str:
    configured = (settings.docker_host or "").strip()
    if configured:
        return configured
    env_host = (os.environ.get("DOCKER_HOST") or "").strip()
    if env_host:
        return env_host
    return f"unix://{_rootless_socket_path()}"


def _safe_error(exc: BaseException) -> str:
    text = str(exc).strip().lower()
    if "no such container" in text or "not found" in text:
        return "Container not found"
    if "permission denied" in text:
        return "Permission denied"
    if "timeout" in text:
        return "Docker operation timed out"
    return "Docker operation failed"


def _get_client(force: bool = False) -> Any:
    global _CLIENT
    if docker is None:
        raise DockerUnavailableError("Docker SDK is not installed")
    if _CLIENT is not None and not force:
        return _CLIENT
    host = resolve_docker_host()
    if host.startswith("unix://"):
        sock = host.removeprefix("unix://")
        if not os.path.exists(sock):
            raise DockerUnavailableError("Rootless Docker socket not found")
    try:
        _CLIENT = docker.DockerClient(base_url=host)
        return _CLIENT
    except (DockerException, OSError) as exc:
        logger.warning("Docker client creation failed")
        raise DockerUnavailableError("Docker daemon not available") from exc


def ping() -> bool:
    try:
        client = _get_client()
        client.ping()
        return True
    except (DockerUnavailableError, DockerException, OSError):
        return False


def assert_msm_container_name(name: str) -> str:
    """Only msm-srv-* containers may be managed by the agent."""
    if not name or not isinstance(name, str):
        raise ContainerNameError("Container name required")
    prefix = settings.container_name_prefix
    if not name.startswith(prefix):
        raise ContainerNameError(f"Container name must start with {prefix}")
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise ContainerNameError("Invalid container name characters")
    return name


def list_containers() -> list[dict[str, Any]]:
    client = _get_client()
    prefix = settings.container_name_prefix
    result: list[dict[str, Any]] = []
    try:
        for c in client.containers.list(all=True):
            cname = (c.name or "").lstrip("/")
            if not cname.startswith(prefix):
                continue
            result.append(
                {
                    "name": cname,
                    "id": (c.id or "")[:12],
                    "status": c.status,
                    "image": (
                        c.image.tags[0]
                        if getattr(c, "image", None) and getattr(c.image, "tags", None)
                        else str(getattr(c.image, "id", "") or "")[:20]
                    ),
                }
            )
    except (DockerException, OSError) as exc:
        logger.warning("docker list failed")
        raise DockerUnavailableError(_safe_error(exc)) from exc
    return result


def _get_container(name: str) -> Any:
    assert_msm_container_name(name)
    client = _get_client()
    try:
        return client.containers.get(name)
    except NotFound as exc:
        raise FileNotFoundError("Container not found") from exc
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


def create_container(
    *,
    name: str,
    image: str,
    command: list[str] | str | None = None,
    env: dict[str, str] | None = None,
    ports: dict[str, Any] | None = None,
    volumes: dict[str, dict[str, str]] | None = None,
    cpu_limit_percent: float | None = None,
    ram_limit_mb: int | None = None,
    user: str | None = None,
    workdir: str | None = None,
    network: str | None = None,
    privileged: bool | None = None,
    cap_add: list[str] | None = None,
    network_mode: str | None = None,
) -> dict[str, Any]:
    """Create (and start) a hardened container.

    Rejects privileged=True, network_mode=host, and arbitrary capability adds
    that would defeat cap_drop=ALL hardening.
    """
    assert_msm_container_name(name)
    if not image or not str(image).strip():
        raise ValueError("image is required")

    # Hardening gate — never allow callers to weaken the security model
    if privileged is True:
        raise HardeningError("privileged containers are not allowed")
    if network_mode and str(network_mode).lower() == "host":
        raise HardeningError("host networking is not allowed")
    if cap_add:
        raise HardeningError("custom cap_add is not allowed (cap_drop=ALL enforced)")

    client = _get_client()

    # Remove existing with same name (idempotent recreate)
    try:
        existing = client.containers.get(name)
        existing.remove(force=True)
    except NotFound:
        pass
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc

    kwargs: dict[str, Any] = {
        "image": image,
        "command": command,
        "name": name,
        "detach": True,
        "stdin_open": True,
        "tty": False,
        "privileged": False,
        "restart_policy": {"Name": "no"},
        "log_config": (
            LogConfig(type=LogConfig.types.JSON, config=_LOG_CONFIG) if LogConfig else None
        ),
        "cap_drop": list(_HARDENING_CAP_DROP),
        "security_opt": list(_HARDENING_SECURITY_OPT),
        "environment": env or None,
        "ports": ports or None,
        "volumes": volumes or None,
        "user": user,
        "working_dir": workdir,
        "network": network,
    }
    if cpu_limit_percent is not None and cpu_limit_percent > 0:
        kwargs["nano_cpus"] = int(round(cpu_limit_percent / 100.0, 2) * 1_000_000_000)
    if ram_limit_mb is not None and ram_limit_mb > 0:
        kwargs["mem_limit"] = f"{int(ram_limit_mb)}m"
        kwargs["memswap_limit"] = f"{int(ram_limit_mb)}m"

    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    try:
        container = client.containers.run(**kwargs)
        return {
            "ok": True,
            "name": name,
            "id": (getattr(container, "id", "") or "")[:12],
        }
    except ImageNotFound as exc:
        raise ValueError(f"Image not found: {image}") from exc
    except (DockerException, OSError) as exc:
        logger.warning("docker create/run failed")
        raise DockerUnavailableError(_safe_error(exc)) from exc


def start_container(name: str) -> dict[str, Any]:
    container = _get_container(name)
    try:
        container.start()
        return {"ok": True, "name": name}
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


def stop_container(name: str, timeout: int | None = None) -> dict[str, Any]:
    container = _get_container(name)
    grace = timeout if timeout is not None else settings.default_stop_timeout
    try:
        container.stop(timeout=max(0, int(grace)))
        return {"ok": True, "name": name}
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


def restart_container(name: str, timeout: int | None = None) -> dict[str, Any]:
    container = _get_container(name)
    grace = timeout if timeout is not None else settings.default_stop_timeout
    try:
        container.restart(timeout=max(0, int(grace)))
        return {"ok": True, "name": name}
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


def remove_container(name: str) -> dict[str, Any]:
    container = _get_container(name)
    try:
        container.remove(force=True)
        return {"ok": True, "name": name}
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


def container_stats(name: str) -> dict[str, Any]:
    container = _get_container(name)
    try:
        container.reload()
        if container.status != "running":
            return {
                "name": name,
                "status": container.status,
                "cpu_percent": None,
                "ram_mb": None,
                "network_rx_bytes": None,
                "network_tx_bytes": None,
            }
        raw = container.stats(stream=False)
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc

    cpu_percent = _cpu_percent(raw)
    ram_mb = None
    try:
        ram_mb = int(raw.get("memory_stats", {}).get("usage", 0)) // (1024 * 1024)
    except (TypeError, ValueError):
        ram_mb = None

    net_rx = net_tx = 0
    try:
        networks = raw.get("networks") or {}
        for iface in networks.values():
            net_rx += int(iface.get("rx_bytes") or 0)
            net_tx += int(iface.get("tx_bytes") or 0)
    except (TypeError, ValueError):
        net_rx = net_tx = 0

    return {
        "name": name,
        "status": "running",
        "cpu_percent": cpu_percent,
        "ram_mb": ram_mb,
        "network_rx_bytes": net_rx,
        "network_tx_bytes": net_tx,
    }


def _cpu_percent(raw: dict) -> float | None:
    try:
        cpu_delta = (
            raw["cpu_stats"]["cpu_usage"]["total_usage"]
            - raw["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            raw["cpu_stats"]["system_cpu_usage"] - raw["precpu_stats"]["system_cpu_usage"]
        )
        online_cpus = (
            raw["cpu_stats"].get("online_cpus")
            or len(raw["cpu_stats"]["cpu_usage"].get("percpu_usage") or [])
            or 1
        )
        if system_delta <= 0:
            return None
        return round((cpu_delta / system_delta) * online_cpus * 100.0, 2)
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def exec_in_container(name: str, command: list[str]) -> dict[str, Any]:
    if not command:
        raise ValueError("command is required")
    container = _get_container(name)
    try:
        container.reload()
        if container.status != "running":
            return {"ok": False, "error": "Container is not running", "stdout": "", "stderr": ""}
        result = container.exec_run(command, stdout=True, stderr=True, demux=True)
        exit_code = int(getattr(result, "exit_code", 1))
        output = getattr(result, "output", (b"", b""))
        stdout_b, stderr_b = output if isinstance(output, tuple) else (output, b"")
        stdout = _decode(stdout_b)
        stderr = _decode(stderr_b)
        return {
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "error": "" if exit_code == 0 else (stderr or stdout or f"exit {exit_code}")[:500],
        }
    except (DockerException, OSError) as exc:
        logger.warning("docker exec failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def send_stdin(name: str, data: str) -> dict[str, Any]:
    """Inject text into container PID 1 stdin (console input)."""
    container = _get_container(name)
    client = _get_client()
    try:
        container.reload()
        if container.status != "running":
            return {"ok": False, "error": "Container is not running"}
        exec_info = client.api.exec_create(
            container.id,
            ["sh", "-c", "cat > /proc/1/fd/0"],
            stdin=True,
            stdout=True,
            stderr=True,
        )
        exec_socket = client.api.exec_start(exec_info["Id"], socket=True)
        raw_socket = getattr(exec_socket, "_sock", exec_socket)
        try:
            raw_socket.settimeout(10.0)
        except OSError:
            pass
        raw_socket.sendall(data.encode("utf-8"))
        try:
            import socket as _socket

            raw_socket.shutdown(_socket.SHUT_WR)
        except OSError:
            pass
        return {"ok": True}
    except (DockerException, OSError) as exc:
        logger.warning("docker stdin send failed")
        return {"ok": False, "error": _safe_error(exc)}


def stream_logs_sync(name: str, tail: int = 200):
    """Generator yielding log lines (blocking). Used from WS background thread."""
    container = _get_container(name)
    try:
        for chunk in container.logs(stream=True, follow=True, tail=tail, stdout=True, stderr=True):
            line = _decode(chunk).rstrip("\r\n")
            if line:
                yield line
    except (DockerException, OSError):
        logger.warning("docker log stream ended")
        return


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")
