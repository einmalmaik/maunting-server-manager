"""Rootless-Docker Adapter fuer Game-Server-Container.

KISS: kleine Fassade um das Docker SDK. Die restliche Codebasis bleibt bei der
bestehenden ``docker_service``-API und sieht keine SDK-Typen.

Sicherheitsinvarianten:
- MSM spricht nur mit dem Rootless-Docker-Socket des Panel-Users.
- Keine Secrets, Env-Werte oder stdin-Daten werden geloggt.
- Kein ``--privileged`` und kein ``--network host``.
- Container starten mit Cap-Drop, no-new-privileges, Log-Limits und Resource-
  Limits wie bisher.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
from dataclasses import dataclass
from typing import Any, AsyncIterator

try:
    import docker
    from docker.errors import APIError, DockerException, NotFound
    from docker.models.containers import Container
    from docker.types import LogConfig
except ImportError:  # pragma: no cover - exercised on systems before deps install
    docker = None  # type: ignore[assignment]
    APIError = DockerException = NotFound = Exception  # type: ignore[misc,assignment]
    Container = Any  # type: ignore[misc,assignment]
    LogConfig = None  # type: ignore[assignment]

from config import settings

logger = logging.getLogger(__name__)

ROOTLESS_DOCKER_ERROR = "Rootless Docker Daemon not running for user msm"

_SYSTEM_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
}

_LOG_CONFIG = {"max-size": "10m", "max-file": "3"}
_HARDENING_CAP_DROP = ["ALL"]
_HARDENING_SECURITY_OPT = ["no-new-privileges"]
_CLIENT: Any | None = None
_DOCKER_AVAILABLE: bool | None = None


@dataclass(frozen=True)
class PortPublish:
    """Eine einzelne Port-Veroeffentlichung fuer Docker."""

    host_port: int
    container_port: int
    protocol: str = "udp"
    host_ip: str | None = None

    def key(self) -> str:
        proto = self.protocol.lower()
        if proto not in ("tcp", "udp"):
            raise ValueError(f"Ungueltiges Protokoll: {self.protocol}")
        return f"{self.container_port}/{proto}"

    def binding(self) -> int | tuple[str, int]:
        if self.host_ip:
            return (self.host_ip, self.host_port)
        return self.host_port


@dataclass(frozen=True)
class VolumeBind:
    """Bind-Mount fuer Docker."""

    host_path: str
    container_path: str
    read_only: bool = False

    def binding(self) -> dict[str, str]:
        return {"bind": self.container_path, "mode": "ro" if self.read_only else "rw"}


def _rootless_socket_path() -> str:
    if hasattr(os, "getuid"):
        return f"/run/user/{os.getuid()}/docker.sock"
    return "/run/user/0/docker.sock"


def _default_docker_host() -> str:
    return f"unix://{_rootless_socket_path()}"


def resolve_docker_host() -> str:
    """DOCKER_HOST-Prioritaet: MSM_DOCKER_HOST, DOCKER_HOST, Rootless-Default."""

    configured = (settings.docker_host or "").strip()
    if configured:
        return configured
    env_host = (os.environ.get("DOCKER_HOST") or "").strip()
    if env_host:
        return env_host
    return _default_docker_host()


def _socket_path_from_host(host: str) -> str | None:
    if not host.startswith("unix://"):
        return None
    return host.removeprefix("unix://")


def _safe_error(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return "Docker-Befehl konnte nicht ausgefuehrt werden"
    
    text_lower = text.lower()
    if "no such container" in text_lower or "not found" in text_lower:
        return "Container nicht gefunden"
    if "permission denied" in text_lower:
        return "Zugriff verweigert (Systemrichtlinie oder Berechtigung)"
    if "timeout" in text_lower:
        return "Zeitueberschreitung bei der Kommunikation mit dem Docker Daemon"
        
    return "Systemfehler bei Container-Operation"


def _get_client(force: bool = False) -> tuple[Any | None, str | None]:
    global _CLIENT
    if docker is None:
        return None, "Docker SDK ist nicht installiert"
    host = resolve_docker_host()
    socket_path = _socket_path_from_host(host)
    if socket_path and not os.path.exists(socket_path):
        return None, ROOTLESS_DOCKER_ERROR
    if _CLIENT is not None and not force:
        return _CLIENT, None
    try:
        _CLIENT = docker.DockerClient(base_url=host)
        return _CLIENT, None
    except (DockerException, OSError) as exc:
        logger.warning("Docker SDK client creation failed: %s", exc)
        return None, ROOTLESS_DOCKER_ERROR


def _check_docker(force: bool = False) -> bool:
    global _DOCKER_AVAILABLE
    if _DOCKER_AVAILABLE is not None and not force:
        return _DOCKER_AVAILABLE
    client, error = _get_client(force=force)
    if client is None:
        logger.info("Docker unavailable: %s", error)
        _DOCKER_AVAILABLE = False
        return False
    try:
        client.ping()
        _DOCKER_AVAILABLE = True
    except (DockerException, OSError):
        logger.warning("Docker ping failed")
        _DOCKER_AVAILABLE = False
    return _DOCKER_AVAILABLE


def _client_or_error() -> tuple[Any | None, dict | None]:
    client, error = _get_client()
    if client is None:
        return None, {"ok": False, "error": error or ROOTLESS_DOCKER_ERROR, "stdout": "", "stderr": ""}
    return client, None


def _container(name: str) -> Container | None:
    client, error = _get_client()
    if client is None:
        logger.info("Docker unavailable while resolving container: %s", error)
        return None
    try:
        return client.containers.get(name)
    except NotFound:
        return None
    except (DockerException, OSError):
        logger.warning("Docker container lookup failed")
        return None


def _ports_dict(ports: list[PortPublish] | None) -> dict[str, Any] | None:
    if not ports:
        return None
    mapped: dict[str, Any] = {}
    for port in ports:
        key = port.key()
        binding = port.binding()
        if key in mapped:
            existing = mapped[key]
            if isinstance(existing, list):
                existing.append(binding)
            else:
                mapped[key] = [existing, binding]
        else:
            mapped[key] = binding
    return mapped


def _volumes_dict(volumes: list[VolumeBind] | None) -> dict[str, dict[str, str]] | None:
    if not volumes:
        return None
    return {volume.host_path: volume.binding() for volume in volumes}


def _tmpfs_dict(tmpfs_paths: list[str] | None) -> dict[str, str] | None:
    if not tmpfs_paths:
        return None
    return {path: "rw,size=64m,mode=1777" for path in tmpfs_paths}


def is_available() -> bool:
    """Public-API: ist Rootless Docker nutzbar?"""
    return _check_docker(force=True)


def pull(image: str) -> dict:
    client, error = _client_or_error()
    if error:
        return error
    try:
        client.images.pull(image)
        return {"ok": True, "stdout": "", "stderr": ""}
    except (DockerException, OSError) as exc:
        logger.warning("docker pull failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def exists(name: str) -> bool:
    return _container(name) is not None


def is_running(name: str) -> bool:
    container = _container(name)
    return bool(container is not None and getattr(container, "status", None) == "running")


def remove(name: str, force: bool = True) -> dict:
    container = _container(name)
    if container is None:
        return {"ok": True, "stdout": "", "stderr": "", "note": "container did not exist"}
    try:
        container.remove(force=force)
        return {"ok": True, "stdout": "", "stderr": ""}
    except (DockerException, OSError) as exc:
        logger.warning("docker remove failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def stop(name: str, timeout: int = 30) -> dict:
    container = _container(name)
    if container is None or getattr(container, "status", None) != "running":
        return {"ok": True, "stdout": "", "stderr": "", "note": "container was not running"}
    try:
        container.stop(timeout=timeout)
        return {"ok": True, "stdout": "", "stderr": ""}
    except (DockerException, OSError) as exc:
        logger.warning("docker stop failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def start(name: str) -> dict:
    container = _container(name)
    if container is None:
        return {"ok": False, "error": "Container nicht gefunden", "stdout": "", "stderr": ""}
    try:
        container.start()
        return {"ok": True, "stdout": "", "stderr": ""}
    except (DockerException, OSError) as exc:
        logger.warning("docker start failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def run_container(
    *,
    name: str,
    image: str,
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
    ports: list[PortPublish] | None = None,
    volumes: list[VolumeBind] | None = None,
    cpu_limit_percent: int | None = None,
    ram_limit_mb: int | None = None,
    user: str | None = None,
    workdir: str | None = None,
    read_only_rootfs: bool = True,
    tmpfs_paths: list[str] | None = None,
    extra_args: list[str] | None = None,
    detach: bool = True,
) -> dict:
    """Startet einen langlebigen Game-Server-Container."""

    if extra_args:
        return {"ok": False, "error": "extra_args werden vom Docker SDK Adapter nicht unterstuetzt", "stdout": "", "stderr": ""}

    client, error = _client_or_error()
    if error:
        return error

    try:
        existing = client.containers.get(name)
    except NotFound:
        existing = None
    except (DockerException, OSError) as exc:
        logger.warning("docker existing-container lookup failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}
    if existing is not None:
        try:
            existing.remove(force=True)
        except (DockerException, OSError) as exc:
            logger.warning("docker existing-container remove failed")
            return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}

    kwargs: dict[str, Any] = {
        "image": image,
        "command": command,
        "name": name,
        "detach": detach,
        "stdin_open": True,
        "restart_policy": {"Name": "on-failure", "MaximumRetryCount": 5},
        "log_config": LogConfig(type=LogConfig.types.JSON, config=_LOG_CONFIG) if LogConfig else None,
        "cap_drop": _HARDENING_CAP_DROP,
        "security_opt": _HARDENING_SECURITY_OPT,
        "read_only": read_only_rootfs,
        "environment": env or None,
        "ports": _ports_dict(ports),
        "volumes": _volumes_dict(volumes),
        "tmpfs": _tmpfs_dict(tmpfs_paths),
        "user": user,
        "working_dir": workdir,
    }
    if cpu_limit_percent is not None and cpu_limit_percent > 0:
        kwargs["nano_cpus"] = int(round(cpu_limit_percent / 100.0, 2) * 1_000_000_000)
    if ram_limit_mb is not None and ram_limit_mb > 0:
        kwargs["mem_limit"] = f"{ram_limit_mb}m"
        kwargs["memswap_limit"] = f"{ram_limit_mb}m"

    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    try:
        container = client.containers.run(**kwargs)
        container_id = getattr(container, "id", "") if detach else ""
        return {"ok": True, "stdout": f"{container_id}\n" if container_id else "", "stderr": ""}
    except (DockerException, OSError) as exc:
        logger.warning("docker container run failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def run_ephemeral(
    *,
    image: str,
    command: list[str],
    volumes: list[VolumeBind] | None = None,
    env: dict[str, str] | None = None,
    user: str | None = None,
    workdir: str | None = None,
    entrypoint: str | None = None,
    cap_adds: list[str] | None = None,
    timeout: int = 1800,
) -> dict:
    """Fuehrt einen einmaligen Containerlauf aus und entfernt den Container danach."""

    client, error = _client_or_error()
    if error:
        return error
    kwargs: dict[str, Any] = {
        "image": image,
        "command": command,
        "detach": True,
        "environment": env or None,
        "volumes": _volumes_dict(volumes),
        "user": user,
        "working_dir": workdir,
        "entrypoint": entrypoint,
        "cap_drop": _HARDENING_CAP_DROP,
        "cap_add": cap_adds or None,
        "security_opt": _HARDENING_SECURITY_OPT,
    }
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    container = None
    try:
        container = client.containers.run(**kwargs)
        wait_result = container.wait(timeout=timeout)
        stdout = _decode(container.logs(stdout=True, stderr=False))
        stderr = _decode(container.logs(stdout=False, stderr=True))
        status_code = int(wait_result.get("StatusCode", 1))
        if status_code != 0:
            return {
                "ok": False,
                "error": (stderr.strip() or stdout.strip() or f"exit {status_code}")[:500],
                "stdout": stdout,
                "stderr": stderr,
            }
        return {"ok": True, "stdout": stdout, "stderr": stderr}
    except (DockerException, OSError) as exc:
        logger.warning("docker ephemeral run failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except (DockerException, OSError):
                logger.warning("docker ephemeral cleanup failed")


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def inspect_state(name: str) -> dict | None:
    container = _container(name)
    if container is None:
        return None
    try:
        container.reload()
        state = container.attrs.get("State", {})
        return {
            "status": state.get("Status"),
            "started_at": state.get("StartedAt"),
            "exit_code": state.get("ExitCode"),
            "oom_killed": bool(state.get("OOMKilled")),
        }
    except (DockerException, OSError):
        logger.warning("docker inspect failed")
        return None


def stats(name: str) -> dict | None:
    container = _container(name)
    if container is None or getattr(container, "status", None) != "running":
        return None
    try:
        raw = container.stats(stream=False)
    except (DockerException, OSError):
        logger.warning("docker stats failed")
        return None
    cpu_percent = _cpu_percent(raw)
    ram_mb = None
    try:
        ram_mb = int(raw.get("memory_stats", {}).get("usage", 0)) // (1024 * 1024)
    except (TypeError, ValueError):
        ram_mb = None
    return {"cpu_percent": cpu_percent, "ram_mb": ram_mb}


def _cpu_percent(raw: dict) -> float | None:
    try:
        cpu_delta = raw["cpu_stats"]["cpu_usage"]["total_usage"] - raw["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = raw["cpu_stats"]["system_cpu_usage"] - raw["precpu_stats"]["system_cpu_usage"]
        online_cpus = raw["cpu_stats"].get("online_cpus") or len(raw["cpu_stats"]["cpu_usage"].get("percpu_usage") or []) or 1
        if system_delta <= 0:
            return None
        return round((cpu_delta / system_delta) * online_cpus * 100.0, 2)
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def logs(name: str, lines: int = 200) -> str:
    container = _container(name)
    if container is None:
        return ""
    try:
        return _decode(container.logs(tail=lines, stdout=True, stderr=True))
    except (DockerException, OSError):
        logger.warning("docker logs failed")
        return ""


def exec_in(name: str, command: list[str], timeout: int = 30) -> dict:
    container = _container(name)
    if container is None or getattr(container, "status", None) != "running":
        return {"ok": False, "error": "Container laeuft nicht", "stdout": "", "stderr": ""}
    try:
        result = container.exec_run(command, stdout=True, stderr=True, demux=True)
        exit_code = int(getattr(result, "exit_code", 1))
        output = getattr(result, "output", ("", ""))
        stdout, stderr = output if isinstance(output, tuple) else (output, b"")
        stdout_text = _decode(stdout)
        stderr_text = _decode(stderr)
        if exit_code != 0:
            return {"ok": False, "error": (stderr_text or stdout_text or f"exit {exit_code}")[:500], "stdout": stdout_text, "stderr": stderr_text}
        return {"ok": True, "stdout": stdout_text, "stderr": stderr_text}
    except (DockerException, OSError) as exc:
        logger.warning("docker exec failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def _demux_socket_stream(raw_socket: Any) -> tuple[bytes, bytes]:
    """Liest den gemultiplexten Docker-Stream aus dem Socket und demultiplext ihn."""
    stdout_chunks = []
    stderr_chunks = []

    header_buf = b""
    while True:
        while len(header_buf) < 8:
            chunk = raw_socket.recv(8 - len(header_buf))
            if not chunk:
                break
            header_buf += chunk

        if len(header_buf) < 8:
            break

        stream_type = header_buf[0]
        length = int.from_bytes(header_buf[4:8], byteorder="big")
        header_buf = b""

        payload_buf = b""
        while len(payload_buf) < length:
            chunk = raw_socket.recv(length - len(payload_buf))
            if not chunk:
                break
            payload_buf += chunk

        if stream_type == 1:
            stdout_chunks.append(payload_buf)
        elif stream_type == 2:
            stderr_chunks.append(payload_buf)

    return b"".join(stdout_chunks), b"".join(stderr_chunks)


def send_stdin(name: str, data: str) -> dict:
    container = _container(name)
    if container is None or getattr(container, "status", None) != "running":
        return {"ok": False, "error": "Container laeuft nicht", "stdout": "", "stderr": ""}
    client, error = _client_or_error()
    if error:
        return error
    try:
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
            raw_socket.shutdown(socket.SHUT_WR)
        except OSError:
            pass

        stdout, stderr = _demux_socket_stream(raw_socket)
        stdout_text = _decode(stdout)
        stderr_text = _decode(stderr)

        inspect = client.api.exec_inspect(exec_info["Id"])
        exit_code = int(inspect.get("ExitCode") or 0)
        if exit_code != 0:
            err_msg = stderr_text.strip() or stdout_text.strip() or f"exit {exit_code}"
            return {"ok": False, "error": err_msg[:500], "stdout": stdout_text, "stderr": stderr_text}
        return {"ok": True, "stdout": stdout_text, "stderr": stderr_text}
    except (DockerException, OSError) as exc:
        logger.warning("docker stdin send failed: %s", exc)
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


async def stream_logs(name: str, tail: int = 200) -> AsyncIterator[str]:
    """Streame Live-Container-Stdout/Stderr via Subprocess (verhindert Thread-Leaks)."""
    if not is_available() or not exists(name):
        return

    host = resolve_docker_host()
    env = {**os.environ, "DOCKER_HOST": host}
    cmd = ["docker", "logs", "--follow", "--tail", str(tail), name]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("Error response from daemon:"):
                continue
            yield line
    except (FileNotFoundError, OSError) as e:
        logger.warning("Failed to start docker logs subprocess: %s", e)
        return
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            except Exception:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass


def disk_usage_mb(path: str) -> int | None:
    """Liefert die Bytes-Groesse eines Pfads in MB (gerundet)."""
    if not os.path.isdir(path):
        return None
    try:
        result = subprocess.run(
            ["du", "-sb", "--", path],
            capture_output=True,
            text=True,
            timeout=60,
            env=_SYSTEM_ENV,
        )
        if result.returncode != 0:
            return None
        first = result.stdout.split()
        if not first:
            return None
        return int(first[0]) // (1024 * 1024)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        return None


def host_uid_gid() -> tuple[int, int]:
    """Aktuelles UID:GID des Panel-Prozesses."""
    if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        return 0, 0
    return os.getuid(), os.getgid()
