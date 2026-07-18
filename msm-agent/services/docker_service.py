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
import subprocess
from pathlib import Path
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
        "security_opt": list(_HARDENING_SECURITY_OPT),
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
        result = container.update(**update_kwargs)
        warnings = result.get("Warnings") if isinstance(result, dict) else None
        if warnings:
            try:
                container.update(**restore_kwargs)
            except (DockerException, OSError):
                return {"ok": False, "error": "Resource update failed", "drift": True}
            return {"ok": False, "error": "Resource update rejected"}
        return {"ok": True}
    except (DockerException, OSError) as exc:
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


def container_logs(name: str, tail: int = 200) -> str:
    container = _get_container(name)
    data = container.logs(tail=max(1, min(tail, 2000)), stdout=True, stderr=True)
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)


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
    import shlex
    uid, gid = owner_uid_gid or container_runtime_uid_gid()
    target = shlex.quote(container_path)
    cmd = [
        "-c",
        f"find {target} -xdev -exec chown -h {uid}:{gid} {{}} + 2>/dev/null || true; "
        f"find {target} -xdev -type d -exec chmod 0750 {{}} + 2>/dev/null || true; "
        f"find {target} -xdev -type f -perm /111 -exec chmod 0750 {{}} + 2>/dev/null || true; "
        f"find {target} -xdev -type f ! -perm /111 -exec chmod 0640 {{}} + 2>/dev/null || true",
    ]
    return run_ephemeral(
        image="alpine:3.21",
        command=cmd,
        volumes={host_path: {"bind": container_path, "mode": "rw"}},
        user="0:0",
        entrypoint="sh",
        timeout=timeout,
    )
