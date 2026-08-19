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
import select
import subprocess
from functools import wraps
from pathlib import Path
from typing import Any

from config import settings
from services.agent_operation_coordinator import (
    operation,
    server_id_from_container_name,
)

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


def _coordinated_container_mutation(function):
    """Apply the shared per-server lock at the lowest Docker mutation layer."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any):
        name = kwargs.get("name")
        if name is None and args:
            name = args[0]
        assert_msm_container_name(name)
        server_id = server_id_from_container_name(name, settings.container_name_prefix)
        with operation(server_id):
            return function(*args, **kwargs)

    return wrapped


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


def _validated_volumes(volumes: dict[str, dict[str, str]] | None) -> dict[str, dict[str, str]] | None:
    """Allow bind mounts only below the agent's managed server directory."""
    if not volumes:
        return None
    root = settings.servers_path()
    validated: dict[str, dict[str, str]] = {}
    for raw_path, binding in volumes.items():
        host_path = Path(raw_path).resolve(strict=False)
        try:
            host_path.relative_to(root)
        except ValueError as exc:
            raise HardeningError("bind mount is outside the managed servers directory") from exc
        validated[str(host_path)] = binding
    return validated


@_coordinated_container_mutation
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
    extra_networks: list[str] | None = None,
    read_only_rootfs: bool = True,
    tmpfs_paths: list[str] | None = None,
    tty: bool = False,
    restart_policy_name: str = "no",
    startup_check_seconds: float = 0.0,
    allow_unprivileged_user_namespaces: bool = False,
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
    allowed_caps = {"DAC_OVERRIDE", "DAC_READ_SEARCH", "CHOWN", "FOWNER", "SETUID", "SETGID"}
    requested_caps = {str(cap).upper() for cap in (cap_add or [])}
    if not requested_caps.issubset(allowed_caps):
        raise HardeningError("requested capability is not allowed")
    if restart_policy_name not in {"no", "on-failure", "unless-stopped"}:
        raise HardeningError("restart policy is not allowed")

    client = _get_client()

    # Repair permissions for all writable volumes before starting the container
    if volumes:
        for host_path, binding in volumes.items():
            if binding.get("mode") == "ro":
                continue
            try:
                target_uid_gid = None
                if user:
                    try:
                        parts = user.split(":", 1)
                        if len(parts) == 2:
                            target_uid_gid = (int(parts[0]), int(parts[1]))
                        else:
                            target_uid_gid = (int(parts[0]), int(parts[0]))
                    except Exception:
                        pass
                repair_bind_mount_permissions(
                    host_path,
                    container_path=binding.get("bind") or "/data",
                    owner_uid_gid=target_uid_gid,
                )
            except Exception as exc:
                logger.warning("Agent permission repair failed for %s: %s", host_path, exc)

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
        "tty": bool(tty),
        "privileged": False,
        "restart_policy": {"Name": restart_policy_name},
        "log_config": (
            LogConfig(type=LogConfig.types.JSON, config=_LOG_CONFIG) if LogConfig else None
        ),
        "cap_drop": list(_HARDENING_CAP_DROP),
        "cap_add": sorted(requested_caps) or None,
        "security_opt": list(_HARDENING_SECURITY_OPT) + (
            ["seccomp=unconfined"] if allow_unprivileged_user_namespaces else []
        ),
        "read_only": bool(read_only_rootfs),
        "environment": env or None,
        "ports": ports or None,
        "volumes": _validated_volumes(volumes),
        "user": user,
        "working_dir": workdir,
        "network": network,
        "tmpfs": {
            path: "rw,size=64m,mode=1777" for path in (tmpfs_paths or [])
        } or None,
    }
    if cpu_limit_percent is not None and cpu_limit_percent > 0:
        kwargs["nano_cpus"] = int(round(cpu_limit_percent / 100.0, 2) * 1_000_000_000)
    if ram_limit_mb is not None and ram_limit_mb > 0:
        kwargs["mem_limit"] = f"{int(ram_limit_mb)}m"
        kwargs["memswap_limit"] = f"{int(ram_limit_mb)}m"

    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    try:
        container = client.containers.run(**kwargs)
        try:
            for network_name in dict.fromkeys(extra_networks or []):
                if network_name and network_name != network:
                    client.networks.get(network_name).connect(container)
        except (DockerException, OSError) as exc:
            try:
                container.remove(force=True)
            except (DockerException, OSError):
                logger.warning("docker cleanup after network attach failure failed")
            raise DockerUnavailableError("Container network attachment failed") from exc

        if startup_check_seconds > 0:
            import time

            time.sleep(startup_check_seconds)
            container.reload()
            state = container.attrs.get("State", {})
            if state.get("Status") in {"exited", "dead"}:
                exit_code = int(state.get("ExitCode") or 0)
                logs = ""
                try:
                    logs = _decode(container.logs(tail=80, stdout=True, stderr=True)).strip()
                except Exception:
                    pass
                detail = f"Container wurde direkt nach dem Start beendet (Exit-Code {exit_code})."
                if logs:
                    detail = f"{detail} Letzte Logs: {logs[:700]}"
                try:
                    container.remove(force=True)
                except (DockerException, OSError):
                    logger.warning("docker cleanup after startup failure failed")
                raise DockerUnavailableError(detail)
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


def run_ephemeral(
    *,
    image: str,
    command: list[str],
    volumes: dict[str, dict[str, str]] | None = None,
    env: dict[str, str] | None = None,
    user: str | None = None,
    workdir: str | None = None,
    entrypoint: str | None = None,
    cap_add: list[str] | None = None,
    timeout: int = 1800,
) -> dict[str, Any]:
    """Run one hardened tool container and remove it on every exit path."""
    allowed_caps = {"DAC_OVERRIDE", "DAC_READ_SEARCH", "CHOWN", "FOWNER", "SETUID", "SETGID"}
    requested_caps = {str(cap).upper() for cap in (cap_add or [])}
    if not requested_caps.issubset(allowed_caps):
        raise HardeningError("requested capability is not allowed")
    client = _get_client()
    container = None
    try:
        container = client.containers.run(
            image=image,
            command=command,
            detach=True,
            environment=env or None,
            volumes=_validated_volumes(volumes),
            user=user,
            working_dir=workdir,
            entrypoint=entrypoint,
            privileged=False,
            cap_drop=list(_HARDENING_CAP_DROP),
            cap_add=sorted(requested_caps) or None,
            security_opt=list(_HARDENING_SECURITY_OPT),
        )
        wait_result = container.wait(timeout=timeout)
        stdout = _decode(container.logs(stdout=True, stderr=False))
        stderr = _decode(container.logs(stdout=False, stderr=True))
        exit_code = int(wait_result.get("StatusCode", 1))
        if exit_code != 0:
            return {
                "ok": False,
                "error": (stderr.strip() or stdout.strip() or f"exit {exit_code}")[:500],
                "stdout": stdout,
                "stderr": stderr,
            }
        return {"ok": True, "stdout": stdout, "stderr": stderr}
    except ImageNotFound as exc:
        raise ValueError("Tool image not found") from exc
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except (DockerException, OSError):
                logger.warning("ephemeral container cleanup failed")


@_coordinated_container_mutation
def start_container(name: str) -> dict[str, Any]:
    container = _get_container(name)
    try:
        container.start()
        return {"ok": True, "name": name}
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


@_coordinated_container_mutation
def stop_container(name: str, timeout: int | None = None) -> dict[str, Any]:
    container = _get_container(name)
    grace = timeout if timeout is not None else settings.default_stop_timeout
    try:
        container.stop(timeout=max(0, int(grace)))
        return {"ok": True, "name": name}
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


@_coordinated_container_mutation
def restart_container(name: str, timeout: int | None = None) -> dict[str, Any]:
    container = _get_container(name)
    grace = timeout if timeout is not None else settings.default_stop_timeout
    try:
        container.restart(timeout=max(0, int(grace)))
        return {"ok": True, "name": name}
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


@_coordinated_container_mutation
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


def _update_container_raw(container: Any, **kwargs: Any) -> dict:
    """POST /containers/{id}/update with Engine JSON fields (NanoCpus/Memory).

    docker-py's Container.update() rejects ``nano_cpus`` (missing from the
    SDK signature). The Engine API supports NanoCpus since 1.25. Mirrors the
    panel backend helper so local and remote nodes share one semantics.

    MagicMock containers fall back to ``container.update(**kwargs)`` so unit
    tests keep working without a real API client.
    """
    if "Mock" in type(container).__name__:
        return container.update(**kwargs)

    api = container.client.api
    url = api._url("/containers/{0}/update", container.id)
    data: dict[str, Any] = {}
    if "nano_cpus" in kwargs:
        data["NanoCpus"] = int(kwargs["nano_cpus"])
    if "mem_limit" in kwargs:
        val = kwargs["mem_limit"]
        if isinstance(val, str):
            from docker.utils import parse_bytes

            data["Memory"] = parse_bytes(val)
        else:
            data["Memory"] = int(val)
    if "memswap_limit" in kwargs:
        val = kwargs["memswap_limit"]
        if isinstance(val, str):
            from docker.utils import parse_bytes

            data["MemorySwap"] = parse_bytes(val)
        else:
            data["MemorySwap"] = int(val)

    res = api._post_json(url, data=data)
    return api._result(res, True)


@_coordinated_container_mutation
def update_container_resources(name: str, updates: dict[str, int | None]) -> dict[str, Any]:
    container = _get_container(name)
    container.reload()
    host_config = container.attrs.get("HostConfig")
    if not isinstance(host_config, dict):
        return {"ok": False, "error": "Resource state unavailable"}
    update_kwargs: dict[str, Any] = {}
    restore_kwargs: dict[str, Any] = {}
    if "cpu_limit_percent" in updates:
        cpu = updates["cpu_limit_percent"]
        update_kwargs["nano_cpus"] = int(round(cpu / 100.0, 2) * 1_000_000_000) if cpu else 0
        restore_kwargs["nano_cpus"] = host_config.get("NanoCpus", 0)
    if "ram_limit_mb" in updates:
        ram = updates["ram_limit_mb"]
        update_kwargs["mem_limit"] = f"{int(ram)}m" if ram else 0
        update_kwargs["memswap_limit"] = f"{int(ram)}m" if ram else -1
        restore_kwargs["mem_limit"] = host_config.get("Memory", 0)
        restore_kwargs["memswap_limit"] = host_config.get("MemorySwap", 0)
    if not update_kwargs:
        return {"ok": True}
    try:
        result = _update_container_raw(container, **update_kwargs)
        warnings = result.get("Warnings") if isinstance(result, dict) else None
        if warnings:
            try:
                _update_container_raw(container, **restore_kwargs)
            except (DockerException, OSError, TypeError):
                return {"ok": False, "error": "Resource update failed", "drift": True}
            return {"ok": False, "error": "Resource update rejected"}
        return {"ok": True}
    except (DockerException, OSError, TypeError) as exc:
        logger.warning("container resource update failed")
        raise DockerUnavailableError(_safe_error(exc)) from exc


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


def inspect_container_state(name: str) -> dict[str, Any] | None:
    """Return non-secret runtime and published-port state for one game container."""
    assert_msm_container_name(name)
    client = _get_client()
    try:
        container = client.containers.get(name)
        container.reload()
        attrs = container.attrs or {}
        state = attrs.get("State") or {}
        raw_ports = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
        port_bindings: dict[str, list[dict[str, Any]]] = {}
        for key, bindings in raw_ports.items():
            safe_bindings: list[dict[str, Any]] = []
            for binding in bindings or []:
                try:
                    host_port = int(binding.get("HostPort"))
                except (TypeError, ValueError):
                    continue
                safe_bindings.append(
                    {
                        "host_ip": str(binding.get("HostIp") or ""),
                        "host_port": host_port,
                    }
                )
            port_bindings[str(key)] = safe_bindings
        return {
            "name": name,
            "status": str(state.get("Status") or container.status or "unknown"),
            "running": bool(state.get("Running")),
            "oom_killed": bool(state.get("OOMKilled")),
            "exit_code": state.get("ExitCode"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "port_bindings": port_bindings,
        }
    except NotFound:
        return None
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


# ── Managed Postgres container (fixed name, not msm-srv-*) ─────────────────
# Separate from game containers: needs cap_add for initdb and dual-network setup.


def assert_managed_postgres_name(name: str) -> str:
    expected = settings.managed_postgres_container_name
    if not name or name != expected:
        raise ContainerNameError(f"Only managed container '{expected}' is allowed here")
    return name


def ensure_network(name: str, *, internal: bool = False) -> dict[str, Any]:
    client = _get_client()
    try:
        client.networks.get(name)
        return {"ok": True}
    except NotFound:
        pass
    except (DockerException, OSError) as exc:
        logger.warning("docker network lookup failed")
        return {"ok": False, "error": _safe_error(exc)}
    try:
        client.networks.create(name, driver="bridge", internal=internal)
        return {"ok": True}
    except (DockerException, OSError) as exc:
        logger.warning("docker network create failed")
        return {"ok": False, "error": _safe_error(exc)}


def inspect_managed_state(name: str) -> dict[str, Any] | None:
    assert_managed_postgres_name(name)
    client = _get_client()
    try:
        c = client.containers.get(name)
        c.reload()
        env_names = {
            str(item).split("=", 1)[0]
            for item in ((c.attrs.get("Config", {}) or {}).get("Env", []) or [])
        }
        return {
            "status": c.status,
            "name": name,
            "has_bootstrap_secret": "POSTGRES_PASSWORD" in env_names,
        }
    except NotFound:
        return None
    except (DockerException, OSError) as exc:
        raise DockerUnavailableError(_safe_error(exc)) from exc


def start_managed(name: str) -> dict[str, Any]:
    assert_managed_postgres_name(name)
    client = _get_client()
    try:
        c = client.containers.get(name)
        c.start()
        return {"ok": True, "name": name}
    except NotFound:
        return {"ok": False, "error": "Container not found"}
    except (DockerException, OSError) as exc:
        return {"ok": False, "error": _safe_error(exc)}


def ensure_managed_restart_policy(name: str, policy_name: str = "unless-stopped") -> dict[str, Any]:
    assert_managed_postgres_name(name)
    client = _get_client()
    try:
        c = client.containers.get(name)
        c.reload()
        current = (c.attrs.get("HostConfig", {}) or {}).get("RestartPolicy", {}) or {}
        if (current.get("Name") or "").lower() == policy_name.lower():
            return {"ok": True}
        c.update(restart_policy={"Name": policy_name})
        return {"ok": True}
    except NotFound:
        return {"ok": False, "error": "Container not found"}
    except (DockerException, OSError) as exc:
        return {"ok": False, "error": _safe_error(exc)}


def run_managed_postgres(
    *,
    name: str,
    image: str,
    env: dict[str, str] | None,
    host_port: int,
    host_ip: str,
    data_dir: str,
    network_name: str,
    cap_adds: list[str],
) -> dict[str, Any]:
    """Create msm-postgres with loopback bind + internal network for game containers.

    cap_add is required for postgres initdb (CHOWN/SETUID/…). Never logs env values.
    """
    assert_managed_postgres_name(name)
    if host_ip != "127.0.0.1":
        raise HardeningError("Managed PostgreSQL may only bind to 127.0.0.1")
    client = _get_client()

    try:
        existing = client.containers.get(name)
        existing.remove(force=True)
    except NotFound:
        pass
    except (DockerException, OSError) as exc:
        return {"ok": False, "error": _safe_error(exc)}

    # Pull if missing
    try:
        client.images.get(image)
    except ImageNotFound:
        try:
            client.images.pull(image)
        except (DockerException, OSError) as exc:
            return {"ok": False, "error": _safe_error(exc)}

    host_network_name = f"{network_name}-host"
    for required_name, internal in (
        (host_network_name, False),
        (network_name, True),
    ):
        try:
            client.networks.get(required_name)
        except NotFound:
            try:
                client.networks.create(required_name, driver="bridge", internal=internal)
            except (DockerException, OSError) as exc:
                return {"ok": False, "error": _safe_error(exc)}
        except (DockerException, OSError) as exc:
            return {"ok": False, "error": _safe_error(exc)}

    ports = {"5432/tcp": (host_ip, host_port)}
    volumes = {data_dir: {"bind": "/var/lib/postgresql/data", "mode": "rw"}}
    try:
        container = client.containers.run(
            image=image,
            name=name,
            detach=True,
            environment=env or None,
            ports=ports,
            volumes=volumes,
            privileged=False,
            cap_drop=list(_HARDENING_CAP_DROP),
            cap_add=list(cap_adds),
            security_opt=list(_HARDENING_SECURITY_OPT),
            network=host_network_name,
            restart_policy={"Name": "unless-stopped"},
            log_config=(
                LogConfig(type=LogConfig.types.JSON, config=_LOG_CONFIG) if LogConfig else None
            ),
        )
        try:
            client.networks.get(network_name).connect(container)
        except (DockerException, OSError) as exc:
            try:
                container.remove(force=True)
            except (DockerException, OSError):
                logger.warning("managed postgres cleanup after network failure failed")
            return {"ok": False, "error": "Managed PostgreSQL network attachment failed"}
        return {"ok": True, "name": name, "id": (getattr(container, "id", "") or "")[:12]}
    except (DockerException, OSError) as exc:
        logger.warning("managed postgres run failed")
        return {"ok": False, "error": _safe_error(exc)}


def exec_in_managed(
    name: str,
    command: list[str],
    timeout: int = 180,
    *,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """exec in managed postgres container (no msm-srv- prefix check)."""
    assert_managed_postgres_name(name)
    if not command:
        raise ValueError("command is required")
    client = _get_client()
    try:
        container = client.containers.get(name)
        container.reload()
        if container.status != "running":
            return {"ok": False, "error": "Container is not running", "stdout": "", "stderr": ""}
        result = container.exec_run(
            command,
            stdout=True,
            stderr=True,
            demux=True,
            environment=environment or None,
        )
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
    except NotFound:
        return {"ok": False, "error": "Container not found", "stdout": "", "stderr": ""}
    except (DockerException, OSError) as exc:
        logger.warning("managed docker exec failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def exec_in_managed_stdin(
    name: str,
    command: list[str],
    stdin_data: str,
    *,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Exec in managed Postgres with SQL over stdin instead of process argv."""
    assert_managed_postgres_name(name)
    if not command:
        raise ValueError("command is required")
    inherited = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "XDG_RUNTIME_DIR", "DOCKER_CONFIG", "SystemRoot", "TEMP", "TMP")
        if key in os.environ
    }
    env = {**inherited, "DOCKER_HOST": resolve_docker_host(), **(environment or {})}
    docker_args = ["docker", "exec", "-i"]
    for key in (environment or {}):
        docker_args.extend(["-e", key])
    docker_args.extend([name, *command])
    try:
        result = subprocess.run(
            docker_args,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=190,
            check=False,
            env=env,
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": "" if result.returncode == 0 else (result.stderr or result.stdout or f"exit {result.returncode}")[:500],
        }
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("managed postgres stdin exec failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


@_coordinated_container_mutation
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


@_coordinated_container_mutation
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


def stream_logs_sync(name: str, tail: int = 200, *, stop_event: Any | None = None):
    """Yield Docker log lines while remaining promptly interruptible."""
    assert_msm_container_name(name)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            ["docker", "logs", "--follow", "--tail", str(max(1, min(tail, 2000))), name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        while stop_event is None or not stop_event.is_set():
            ready, _, _ = select.select([proc.stdout], [], [], 0.2)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            text = line.rstrip("\r\n")
            if text and not text.startswith("Error response from daemon:"):
                yield text
    except (FileNotFoundError, OSError):
        logger.warning("docker log stream ended")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def container_logs(name: str, tail: int = 200) -> str:
    container = _get_container(name)
    data = container.logs(tail=max(1, min(tail, 2000)), stdout=True, stderr=True)
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)


def managed_bind_root(name: str) -> Path:
    """Ermittelt den Server-Root aus dem tatsächlichen sicheren Bind-Mount."""
    container = _get_container(name)
    root = settings.servers_path()
    workdir = str((container.attrs.get("Config") or {}).get("WorkingDir") or "")
    candidates: list[tuple[int, Path]] = []
    for mount in container.attrs.get("Mounts") or []:
        if mount.get("Type") != "bind" or not mount.get("RW", False):
            continue
        source = Path(str(mount.get("Source") or "")).resolve(strict=False)
        destination = str(mount.get("Destination") or "").rstrip("/")
        try:
            source.relative_to(root)
        except ValueError:
            continue
        contains_workdir = workdir == destination or workdir.startswith(destination + "/")
        candidates.append((1 if contains_workdir else 0, source))
    if not candidates:
        raise DockerUnavailableError("Managed server bind mount not found")
    return max(candidates, key=lambda item: item[0])[1]


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def repair_bind_mount_permissions(
    host_path: str,
    *,
    container_path: str = "/data",
    owner_uid_gid: tuple[int, int] | None = None,
    timeout: int = 600,
) -> dict:
    """Normalisiert Rechte eines Server-Bind-Mounts — Gruppenmodell wie im Backend.

    Hier stand die alte harte Fassung: ``chown`` auf einen Default plus
    ``chmod 0750/0640`` exakt. Das brach das Gruppenmodell des Backends in
    beide Richtungen — ``0640`` nahm der Gruppe das Schreibrecht, der exakte
    Modus loeschte das setgid-Bit, und neue Dateien erbten die geteilte
    Gruppe ``msm-srv-<gid>`` nicht mehr. Obendrein rief der Default
    ``container_runtime_uid_gid()`` auf, das es im Agenten nie gab: ohne
    expliziten ``user`` warf jede Reparatur ``NameError``, den das
    ``except Exception`` an der Aufrufstelle verschluckte — die Funktion war
    dort seit jeher tot.

    Jetzt gilt dieselbe Regel wie in `backend/services/docker_service.py`:
    Gruppe des Serververzeichnisses uebernehmen, ``g+rwxs`` auf Verzeichnisse,
    ``g+rw`` (+``g+x`` wo der Eigentuemer x hat) auf Dateien, ``o-rwx`` —
    und ``chown`` nur, wenn der Aufrufer die Ziel-UID ausdruecklich kennt
    (der Fall "Game-Container laeuft als user=X:Y").
    """
    import shlex
    base = os.path.realpath(host_path)
    if not os.path.isdir(base):
        return {"ok": False, "error": "Server-Verzeichnis existiert nicht", "stdout": "", "stderr": ""}
    try:
        geteilte_gid: int | None = os.stat(base).st_gid
    except OSError:
        geteilte_gid = None

    target = shlex.quote(container_path.rstrip("/") or "/data")
    script_parts = []
    if geteilte_gid is not None:
        # Gruppe **vor** den Rechten: sonst traegt eine Datei kurz g+rw fuer
        # eine Gruppe, der sie gleich nicht mehr gehoert.
        script_parts.append(f"chgrp -R {int(geteilte_gid)} {target} 2>/dev/null || true")
    script_parts.extend([
        f"find {target} -xdev -type d -exec chmod u+rwx,g+rwxs,o-rwx {{}} + 2>/dev/null || true",
        f"find {target} -xdev -type f -exec chmod u+rw,g+rw,o-rwx {{}} + 2>/dev/null || true",
        f"find {target} -xdev -type f -perm -u+x -exec chmod g+x {{}} + 2>/dev/null || true",
    ])
    if owner_uid_gid is not None:
        uid, gid = owner_uid_gid
        owner = f"{int(uid)}:{int(gid)}"
        script_parts.extend([
            f"find {target} -xdev -type d -exec chown {owner} {{}} + 2>/dev/null || true",
            f"find {target} -xdev -type f -exec chown {owner} {{}} + 2>/dev/null || true",
            f"find {target} -xdev -type l -exec chown -h {owner} {{}} + 2>/dev/null || true",
        ])
    script = "; ".join(script_parts) + "; exit 0"
    return run_ephemeral(
        image="alpine:3.21",
        command=["-c", script],
        volumes={base: {"bind": container_path, "mode": "rw"}},
        user="0:0",
        entrypoint="sh",
        cap_add=["CHOWN", "FOWNER", "DAC_OVERRIDE"],
        timeout=timeout,
    )
