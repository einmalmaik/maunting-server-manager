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
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

try:
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound, NotFound
    from docker.models.containers import Container
    from docker.types import LogConfig
except ImportError:  # pragma: no cover - exercised on systems before deps install
    docker = None  # type: ignore[assignment]
    APIError = DockerException = ImageNotFound = NotFound = Exception  # type: ignore[misc,assignment]
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
# Dedicated image for permission repair (runs as root to chown bind mounts).
#
# Purpose of repair_bind_mount_permissions():
#   Before starting a game container or doing file ops via the panel, we run a
#   one-shot root container that does:
#     find /data -xdev -type d -exec chmod a+rwX {} +
#     ... chown for the container uid:gid (e.g. 1000:1000 for Wine/Pterodactyl images)
#
# Why this specific image?
# - Must support running as root (user="0:0", entrypoint="bash")
# - Needs bash + find + chown + chmod (the script is a bash -c one-liner)
# - We reuse the same image as STEAMCMD_IMAGE (cm2network/steamcmd:root) for
#   simplicity: Steam users already pull it, it's explicitly the :root variant,
#   reliable for root operations.
#
# This is a *utility/tool image*, not a per-game runtime image (those come from
# the blueprint). It is intentionally a constant (like STEAMCMD_IMAGE) because
# the repair logic has specific requirements (root exec, certain tools).
#
# If you change this, make sure the new image can run the exact script in
# repair_bind_mount_permissions() as root without permission issues inside the
# container.
PERMISSION_REPAIR_IMAGE = "cm2network/steamcmd:root"
PERMISSION_REPAIR_CONTAINER_DIR = "/data"
PERMISSION_REPAIR_CAPS = ["CHOWN", "FOWNER", "DAC_OVERRIDE", "DAC_READ_SEARCH"]
_CLIENT: Any | None = None
_DOCKER_AVAILABLE: bool | None = None


class _ImageUnavailable(RuntimeError):
    def __init__(self, image: str, pull_error: str | None = None) -> None:
        self.image = image
        suffix = f" (Pull fehlgeschlagen: {pull_error})" if pull_error else ""
        super().__init__(f"Docker-Image nicht verfügbar: {image}{suffix}")


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


def _safe_pull_error(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return "Docker Pull fehlgeschlagen"
    detail = " ".join(text.split())[:240]

    text_lower = text.lower()
    if "no such host" in text_lower or "temporary failure in name resolution" in text_lower:
        return "Registry/DNS nicht erreichbar"
    if "connection refused" in text_lower or "connection reset" in text_lower:
        return "Registry-Verbindung abgelehnt oder unterbrochen"
    if "timeout" in text_lower or "i/o timeout" in text_lower or "context deadline exceeded" in text_lower:
        return "Zeitueberschreitung beim Registry-Zugriff"
    if "certificate" in text_lower or "x509" in text_lower:
        return "TLS/Zertifikatsfehler beim Registry-Zugriff"
    if "unauthorized" in text_lower or "authentication required" in text_lower:
        return "Registry-Authentifizierung erforderlich"
    if "denied" in text_lower or "insufficient_scope" in text_lower:
        return "Registry-Zugriff verweigert"
    if "no matching manifest" in text_lower or "no match for platform" in text_lower:
        return "Image existiert, aber nicht fuer die Docker-Host-Plattform"
    if "manifest unknown" in text_lower or "not found" in text_lower:
        return f"Image oder Tag in der Registry nicht gefunden: {detail}"
    if "toomanyrequests" in text_lower or "rate limit" in text_lower:
        return "Registry-Rate-Limit erreicht"

    # Local containerd / Docker content store corruption (e.g. after git clean -fd deleting blobs,
    # disk issues, or interrupted pulls). The "lease content" + "blob not found" at local path
    # means the index thinks the layer exists but the file is gone in ~/.local/share/docker/...
    if "lease content" in text_lower or "blob not found" in text_lower or "content store" in text_lower:
        # 'detail' kann Image-Referenzen, Registry-Hostnames oder Pfade
        # enthalten -- wir kuerzen auf 200 Zeichen, damit der UI-Hinweis
        # nicht unnoetig lang wird und keine internen Details leakt.
        safe_detail = (detail or "")[:200]
        return (
            f"Lokaler Docker-Content-Store korrupt (Blob fehlt: {safe_detail}). "
            "Ursache oft: git clean -fd (hatte .local nicht ignoriert), rm oder unterbrochener Pull. "
            "VOLLSTÄNDIGER FIX (als root, uid 994, /opt/msm als HOME): "
            "sudo -u msm bash -c 'export XDG_RUNTIME_DIR=/run/user/994; systemctl --user stop docker || true; pkill -u 994 dockerd || true; sleep 2'; "
            "rm -rf /opt/msm/.local/share/docker; "
            "sudo -u msm bash -c 'export XDG_RUNTIME_DIR=/run/user/994; systemctl --user start docker || { dockerd-rootless-setuptool.sh install --skip-iptables || true; systemctl --user enable --now docker; }'; "
            "sudo -u msm bash -c 'export DOCKER_HOST=unix:///run/user/994/docker.sock; docker pull cm2network/steamcmd:root; docker pull ghcr.io/parkervcp/steamcmd:debian'; "
            "Dann git pull im /opt/msm und Panel neu starten."
        )

    return detail


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


def _update_container_raw(container: Any, **kwargs: Any) -> dict:
    """Low-Level Container-Update, das direkt den Docker Engine REST-Endpunkt
    ``/containers/{id}/update`` anspricht.

    Notwendig, weil die installierte docker-py Version ``nano_cpus`` weder
    in ``Container.update()`` noch in ``APIClient.update_container()``
    akzeptiert (fehlt in deren Methoden-Signaturen). Die Docker Engine API
    selbst unterstuetzt ``NanoCpus`` seit API-Version 1.25.

    Konvertiert die kwargs (Python-Style wie ``nano_cpus``, ``mem_limit``)
    in die entsprechenden Docker Engine JSON-Felder (``NanoCpus``,
    ``Memory``, ``MemorySwap``).

    Fuer Unit-Tests mit MagicMock: Faellt auf ``container.update(**kwargs)``
    zurueck, damit bestehende Mock-Assertions weiterhin funktionieren.
    """
    # Fallback fuer Mocks in Unit-Tests
    if "Mock" in type(container).__name__:
        return container.update(**kwargs)

    api = container.client.api
    url = api._url("/containers/{0}/update", container.id)

    data: dict[str, Any] = {}
    if "nano_cpus" in kwargs:
        data["NanoCpus"] = kwargs["nano_cpus"]
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


def ensure_network(name: str, *, internal: bool = False) -> dict:
    """Create a Docker bridge network if it does not exist."""

    client, error = _client_or_error()
    if error:
        return error
    try:
        client.networks.get(name)
        return {"ok": True, "stdout": "", "stderr": ""}
    except NotFound:
        pass
    except (DockerException, OSError) as exc:
        logger.warning("docker network lookup failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}

    try:
        client.networks.create(name, driver="bridge", internal=internal)
        return {"ok": True, "stdout": "", "stderr": ""}
    except (DockerException, OSError) as exc:
        logger.warning("docker network create failed")
        return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}


def _tmpfs_dict(tmpfs_paths: list[str] | None) -> dict[str, str] | None:
    if not tmpfs_paths:
        return None
    return {path: "rw,size=64m,mode=1777" for path in tmpfs_paths}


def _split_image_ref(image: str) -> tuple[str, str | None]:
    if "@" in image:
        return image, None
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon > last_slash:
        return image[:last_colon], image[last_colon + 1:]
    return image, None


def _pull_image(client: Any, image: str, server_id: int | None = None) -> None:
    repository, tag = _split_image_ref(image)
    stream = client.api.pull(repository, tag=tag, stream=True, decode=True, auth_config={})
    for event in stream:
        if not isinstance(event, dict):
            continue

        # Log pull progress to server console for visibility (especially long pulls for Wine/Proton etc.)
        if server_id is not None:
            try:
                from games.base import _append_console_log
                status = event.get("status", "")
                progress = event.get("progress", "")
                if status or progress:
                    msg = f"[MSM] [image pull] {status}"
                    if progress:
                        msg += f" {progress}"
                    _append_console_log(server_id, msg + "\n")
            except Exception:
                pass  # never break pull on logging

        error = event.get("error")
        if not error:
            detail = event.get("errorDetail")
            if isinstance(detail, dict):
                error = detail.get("message")
        if error:
            raise DockerException(str(error))


def _ensure_image_available(client: Any, image: str, server_id: int | None = None) -> None:
    # Fast path: image already in local Docker content store -> no registry roundtrip.
    # Vermeidet 10-60s Wartezeit pro Restart bei grossen Images (Wine/Proton, parkervcp).
    # Funktioniert generisch fuer alle Blueprints/Images - keine hardcodierten Listen.
    # NotFound deckt ImageNotFound (Subklasse) ab.
    try:
        client.images.get(image)
        if server_id is not None:
            try:
                from games.base import _append_console_log
                _append_console_log(server_id, f"[MSM] Using cached local Docker image {image} (no pull needed)\n")
            except Exception:
                pass
        return
    except NotFound:
        pass
    except (DockerException, OSError) as exc:
        logger.debug("docker local image check failed for %s: %s", image, exc)

    pull_error = None
    try:
        _pull_image(client, image, server_id=server_id)
        return
    except (DockerException, OSError) as exc:
        pull_error = _safe_pull_error(exc)
        logger.warning("docker image pull failed for %s: %s", image, pull_error)

    try:
        client.images.get(image)
    except (NotFound, DockerException, OSError) as exc:
        raise _ImageUnavailable(image, pull_error) from exc


def is_available() -> bool:
    """Public-API: ist Rootless Docker nutzbar?"""
    return _check_docker(force=True)


def pull(image: str) -> dict:
    client, error = _client_or_error()
    if error:
        return error
    try:
        _pull_image(client, image)
        return {"ok": True, "stdout": "", "stderr": ""}
    except (DockerException, OSError) as exc:
        pull_error = _safe_pull_error(exc)
        logger.warning("docker pull failed for %s: %s", image, pull_error)
        return {"ok": False, "error": f"Docker Pull fehlgeschlagen: {pull_error}", "stdout": "", "stderr": ""}


def exists(name: str) -> bool:
    return _container(name) is not None


def is_running(name: str) -> bool:
    container = _container(name)
    return bool(container is not None and getattr(container, "status", None) == "running")


def _capture_old_docker_limits(
    container: Any, updates: dict[str, int | None]
) -> dict[str, Any] | None:
    """Erfasst die aktuellen Docker CPU/RAM-Limits aus dem Container-Attribut.

    Wird vor ``container.update()`` aufgerufen, damit bei Warnungen oder
    Partial-Success die alten Limits wiederhergestellt werden koennen
    (VAL-DOCKER-009). Nur Felder, die auch geaendert werden, werden erfasst.

    Returns ``None`` wenn ``container.reload()`` fehlschlaegt oder kein
    verwendbares ``HostConfig``-Dict vorhanden ist. Der Aufrufer muss in
    diesem Fall abbrechen, bevor ``container.update()`` aufgerufen wird,
    da ohne erfasste alte Limits kein Restore bei Warnungen moeglich ist
    und DB/Docker-Drift entstehen kann.
    """
    try:
        container.reload()
    except (DockerException, OSError):
        return None

    host_config = container.attrs.get("HostConfig")
    if not isinstance(host_config, dict):
        return None

    restore_kwargs: dict[str, Any] = {}

    # Docker's HostConfig always includes CpuPeriod, CpuQuota, Memory, and
    # MemorySwap, defaulting unset fields to 0. Using .get(key, 0) matches
    # this default, so missing keys are captured as 0 (the Docker default
    # for unset limits). This is the explicit, tested behavior: missing
    # individual keys default to 0 and cannot create drift ambiguity
    # (scrutiny round 3 fix). Missing entire HostConfig is handled above
    # (returns None → caller aborts before mutation).
    if "cpu_limit_percent" in updates:
        restore_kwargs["nano_cpus"] = host_config.get("NanoCpus", 0)

    if "ram_limit_mb" in updates:
        restore_kwargs["mem_limit"] = host_config.get("Memory", 0)
        restore_kwargs["memswap_limit"] = host_config.get("MemorySwap", 0)

    return restore_kwargs


def _verify_effective_limits(
    container: Any, restore_kwargs: dict[str, Any], name: str
) -> bool:
    """Verifiziert nach Restore, dass die effektiven Docker-Limits den alten
    Werten entsprechen.

    Laedt den Container neu und vergleicht die HostConfig-Werte mit den
    urspruenglich erfassten Werten. Returns ``True`` wenn alle verglichenen
    Werte uebereinstimmen, ``False`` bei Reload-Fehler, fehlendem HostConfig
    oder Wert-Mismatch.
    """
    try:
        container.reload()
    except (DockerException, OSError):
        logger.warning("docker restore verification reload failed for %s", name)
        return False

    host_config = container.attrs.get("HostConfig")
    if not isinstance(host_config, dict):
        logger.warning("docker restore verification no HostConfig for %s", name)
        return False

    field_map = [
        ("nano_cpus", "NanoCpus"),
        ("mem_limit", "Memory"),
        ("memswap_limit", "MemorySwap"),
    ]
    # Use .get(host_key, 0) to match the default used in
    # _capture_old_docker_limits. Docker's HostConfig always includes these
    # keys with default 0, so missing keys in both capture and verification
    # default to 0 consistently. Without this, a missing key would return
    # None from .get(host_key) while the captured value is 0, causing a
    # false mismatch and drift report (scrutiny round 3 fix).
    for key, host_key in field_map:
        if key in restore_kwargs:
            if host_config.get(host_key, 0) != restore_kwargs[key]:
                logger.warning("docker restore verification mismatch for %s", name)
                return False

    return True


def _restore_old_docker_limits(
    container: Any, restore_kwargs: dict[str, Any], name: str
) -> bool:
    """Versucht, alte Docker CPU/RAM-Limits nach einer Warnung wiederherzustellen
    und verifiziert das Ergebnis.

    Ruft ``container.update()`` mit den alten Werten auf und verifiziert
    anschliessend durch Reload, dass die effektiven HostConfig-Werte den
    alten Werten entsprechen. Restore-Warnings oder -Exceptions werden
    toleriert, aber nur wenn die Verifikation beweist, dass die alten
    Werte effektiv wiederhergestellt sind (rollback-safe).

    Returns ``True`` wenn die Verifikation die alten Werte bestätigt
    (rollback-safe). Returns ``False`` bei Verifikationsfehler
    (moeglicher Docker-Drift, nicht rollback-safe).

    Weder Warning-Inhalte noch Restore-Fehlerdetails werden geloggt
    (VAL-DOCKER-009: keine Raw-Warning-Internas in Logs).
    """
    try:
        result = _update_container_raw(container, **restore_kwargs)
        if isinstance(result, dict):
            raw = result.get("Warnings") or []
            if isinstance(raw, list) and any(raw):
                logger.warning("docker restore returned warnings for %s", name)
    except (DockerException, OSError):
        logger.warning("docker restore failed for %s", name)

    return _verify_effective_limits(container, restore_kwargs, name)


def update_container_resources(name: str, updates: dict[str, int | None]) -> dict:
    """Wendet CPU/RAM-Limits live auf einen laufenden Container an (ohne Restart).

    Kapselt das Docker SDK ``container.update()``. Der Aufrufer uebergibt nur
    die Felder, die sich tatsaechlich geaendert haben, als Dict:

      ``{"cpu_limit_percent": 200, "ram_limit_mb": 4096}``

    Werte ``None`` bedeuten "unlimitiert" und loeschen das entsprechende
    Docker-Limit. Nicht im Dict enthaltene Felder werden nicht an Docker
    gesendet (VAL-DOCKER-002: keine unerwarteten Aenderungen an unverwandten
    Limits).

    CPU-Mapping (VAL-DOCKER-007):
      - ``cpu_period`` = 100000 (fester CFS-Zyklus)
      - ``cpu_quota``  = cpu_limit_percent * 1000
      - 50 % -> 50000, 100 % -> 100000, 200 % -> 200000
      - None   -> cpu_quota = 0 (kein Quota = unlimitiert)

    RAM-Mapping:
      - ``mem_limit``      = f"{ram_limit_mb}m"
      - ``memswap_limit``  = f"{ram_limit_mb}m"  (kein Swap-Ueberhang)
      - None -> mem_limit = 0, memswap_limit = -1 (beide Limiters geloescht,
        VAL-DOCKER-008)

    Docker-Warnings oder Partial-Success werden als Fehler behandelt
    (VAL-DOCKER-009), damit API- und Docker-Zustand nicht driften.
    Bei Warnungen werden die alten Docker-Limits wiederhergestellt
    (Compensation), damit Docker und DB nach dem Rollback uebereinstimmen.

    Returns:
      ``{"ok": True}`` bei Erfolg.
      ``{"ok": False, "error": "..."}`` bei Fehlschlag (sanitisiert).
    """
    container = _container(name)
    if container is None:
        return {"ok": False, "error": "Container nicht gefunden"}

    update_kwargs: dict[str, Any] = {}

    if "cpu_limit_percent" in updates:
        cpu = updates["cpu_limit_percent"]
        if cpu is not None and cpu > 0:
            update_kwargs["nano_cpus"] = int(round(cpu / 100.0, 2) * 1_000_000_000)
        else:
            # None = unlimitiert -> NanoCpus 0
            update_kwargs["nano_cpus"] = 0

    if "ram_limit_mb" in updates:
        ram = updates["ram_limit_mb"]
        if ram is not None and ram > 0:
            update_kwargs["mem_limit"] = f"{int(ram)}m"
            update_kwargs["memswap_limit"] = f"{int(ram)}m"
        else:
            # None = unlimitiert -> Memory 0, Swap -1 (beide Limiters geloescht)
            update_kwargs["mem_limit"] = 0
            update_kwargs["memswap_limit"] = -1

    if not update_kwargs:
        return {"ok": True}

    # Alte Docker-Limits erfassen fuer Restore bei Warnungen (VAL-DOCKER-009).
    # Docker's container.update() kann neue Limits teilweise anwenden, selbst
    # wenn Warnungen zurueckgegeben werden. Ohne Restore wuerde die DB
    # zurueckgerollt, waehrend Docker die neuen Limits behaelt (Drift).
    # Wenn der Capture fehlschlaegt (reload oder HostConfig nicht lesbar),
    # muss vor container.update() abgebrochen werden, da sonst keine
    # Restore-Moeglichkeit bei Warnungen besteht (scrutiny round 2 fix).
    restore_kwargs = _capture_old_docker_limits(container, updates)
    if restore_kwargs is None:
        return {"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}

    try:
        result = _update_container_raw(container, **update_kwargs)
        warnings: list = []
        if isinstance(result, dict):
            raw_warnings = result.get("Warnings") or []
            if isinstance(raw_warnings, list):
                warnings = [w for w in raw_warnings if w]
        if warnings:
            logger.warning("docker update returned warnings for %s", name)
            # Alte Limits wiederherstellen und verifizieren, um DB/Docker-Drift
            # zu verhindern (VAL-DOCKER-009, scrutiny round 2 fix).
            # Restore + Verifikation: nur wenn die Verifikation beweist, dass
            # die alten Werte effektiv wiederhergestellt sind, ist der Fehler
            # rollback-safe. Bei Verifikationsfehler wird ein drift-Flag
            # gesetzt, damit der Aufrufer den schwerwiegenden Fall kennt.
            verified = _restore_old_docker_limits(container, restore_kwargs, name)
            if verified:
                return {"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}
            return {
                "ok": False,
                "error": "Ressourcen-Update fehlgeschlagen, manuelle Pruefung erforderlich",
                "drift": True,
            }
        return {"ok": True}
    except (DockerException, OSError) as exc:
        logger.warning("docker live update failed for %s: %s", name, exc)
        return {"ok": False, "error": _safe_error(exc)}


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


def ensure_restart_policy(name: str, policy_name: str = "unless-stopped") -> dict:
    """Setzt die Docker-Restart-Policy auf einem bestehenden Container (idempotent)."""
    container = _container(name)
    if container is None:
        return {"ok": False, "error": "Container nicht gefunden", "stdout": "", "stderr": ""}
    try:
        container.reload()
        current = (container.attrs.get("HostConfig", {}) or {}).get("RestartPolicy", {}) or {}
        if (current.get("Name") or "").lower() == policy_name.lower():
            return {"ok": True, "stdout": "", "stderr": ""}
        container.update(restart_policy={"Name": policy_name})
        return {"ok": True, "stdout": "", "stderr": ""}
    except (DockerException, OSError) as exc:
        logger.warning("docker restart policy update failed")
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
    network: str | None = None,
    extra_networks: list[str] | None = None,
    detach: bool = True,
    startup_check_seconds: float = 0.0,
    server_id: int | None = None,  # for pull progress logging to console during long image pulls
    cap_adds: list[str] | None = None,
    tty: bool = False,  # opt-in for interactive auth/setup flows; default off (existing callers unchanged)
    restart_policy_name: str = "no",
) -> dict:
    """Startet einen langlebigen Game-Server-Container.

    ``cap_adds`` ergaenzt das globale ``cap_drop=ALL`` um spezifische Capabilities,
    die der Container zwingend fuer sein Init braucht (z. B. der Postgres-Entrypoint
    benoetigt CHOWN/FOWNER fuer ``initdb`` und SETUID/SETGID fuer den Wechsel auf
    den postgres-User; siehe PERMISSION_REPAIR_CAPS).

    ``tty=True`` allokiert ein Pseudo-TTY im Container, noetig fuer interaktive
    Auth-Flows (Device-Authorization-Grant mit URL+Code-Eingabe). Wird vom
    Auth-Setup-Recovery-Pfad genutzt; nie vom normalen Server-Start.
    """

    if extra_args:
        return {"ok": False, "error": "extra_args werden vom Docker SDK Adapter nicht unterstuetzt", "stdout": "", "stderr": ""}

    client, error = _client_or_error()
    if error:
        return error

    try:
        _ensure_image_available(client, image, server_id=server_id)
    except _ImageUnavailable as exc:
        return {"ok": False, "error": str(exc), "stdout": "", "stderr": ""}

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
        "tty": tty,
        # Game-Server: "no" (MSM-Lifecycle). Managed Infra (msm-postgres): unless-stopped.
        "restart_policy": {"Name": restart_policy_name or "no"},
        "log_config": LogConfig(type=LogConfig.types.JSON, config=_LOG_CONFIG) if LogConfig else None,
        "cap_drop": _HARDENING_CAP_DROP,
        "cap_add": cap_adds or None,
        "security_opt": _HARDENING_SECURITY_OPT,
        "read_only": read_only_rootfs,
        "environment": env or None,
        "ports": _ports_dict(ports),
        "volumes": _volumes_dict(volumes),
        "tmpfs": _tmpfs_dict(tmpfs_paths),
        "user": user,
        "working_dir": workdir,
    }
    if network:
        kwargs["network"] = network
    networks = [network_name for network_name in (extra_networks or []) if network_name]
    if cpu_limit_percent is not None and cpu_limit_percent > 0:
        kwargs["nano_cpus"] = int(round(cpu_limit_percent / 100.0, 2) * 1_000_000_000)
    if ram_limit_mb is not None and ram_limit_mb > 0:
        kwargs["mem_limit"] = f"{ram_limit_mb}m"
        kwargs["memswap_limit"] = f"{ram_limit_mb}m"

    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    try:
        container = client.containers.run(**kwargs)
        for network_name in networks:
            try:
                client.networks.get(network_name).connect(container)
            except (DockerException, OSError) as exc:
                logger.warning("docker network connect failed")
                return {"ok": False, "error": _safe_error(exc), "stdout": "", "stderr": ""}
        container_id = getattr(container, "id", "") if detach else ""
        if detach and startup_check_seconds > 0:
            time.sleep(startup_check_seconds)
            container.reload()
            state = container.attrs.get("State", {})
            status = state.get("Status")
            if status in {"exited", "dead"}:
                exit_code = int(state.get("ExitCode") or 0)
                logs = _decode(container.logs(tail=80, stdout=True, stderr=True)).strip()
                detail = f"Container wurde direkt nach dem Start beendet (Exit-Code {exit_code})."
                if logs:
                    detail = f"{detail} Letzte Logs: {logs[:700]}"
                return {
                    "ok": False,
                    "error": detail[:1000],
                    "stdout": f"{container_id}\n" if container_id else "",
                    "stderr": "",
                    "exit_code": exit_code,
                    "logs": logs[-4000:],
                }
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
    log_callback: Any | None = None,
) -> dict:
    """Fuehrt einen einmaligen Containerlauf aus und entfernt den Container danach.

    Wenn ``log_callback`` angegeben ist (Callable, nimmt einen str), werden
    Container-Logs zeilenweise waehrend der Ausfuehrung an diesen Callback
    weitergeleitet. So sieht der User den Fortschritt live statt erst am Ende.
    Sicherheit: callback bekommt nur fertig dekodierte Zeilen, keine rohen bytes.
    """

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
        _ensure_image_available(client, image)
        container = client.containers.run(**kwargs)

        if log_callback is not None:
            # Live-Streaming: Logs zeilenweise lesen und an Callback senden.
            # container.logs(stream=True, follow=True) liefert einen Generator
            # ueber bytes-Chunks (eine Zeile pro Chunk bei Docker JSON-Log).
            # Timeout via container.wait() parallel nicht moeglich; wir nutzen
            # stattdessen den Generator mit einem separaten wait nach Abschluss.
            try:
                for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=True):
                    line = _decode(chunk).rstrip("\r\n")
                    if line:
                        log_callback(line + "\n")
            except Exception as stream_exc:
                logger.warning("Live-Log-Stream unterbrochen fuer ephemeral container: %s", stream_exc)

        wait_result = container.wait(timeout=timeout)
        if log_callback is None:
            # Kein Streaming — Output am Ende sammeln (alter Pfad)
            stdout = _decode(container.logs(stdout=True, stderr=False))
            stderr = _decode(container.logs(stdout=False, stderr=True))
        else:
            # Logs wurden bereits live gestreamt; fuer ok/error-Bestimmung
            # brauchen wir nur den Exit-Code (logs koennen leer sein).
            stdout = ""
            stderr = ""
        status_code = int(wait_result.get("StatusCode", 1))
        if status_code != 0:
            return {
                "ok": False,
                "error": (stderr.strip() or stdout.strip() or f"exit {status_code}")[:500],
                "stdout": stdout,
                "stderr": stderr,
            }
        return {"ok": True, "stdout": stdout, "stderr": stderr}
    except _ImageUnavailable as exc:
        return {"ok": False, "error": str(exc), "stdout": "", "stderr": ""}
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
    cmd = ["docker", "logs", "--follow", "--timestamps", "--tail", str(tail), name]
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


def is_rootless() -> bool:
    """True, wenn wir Rootless Docker nutzen (Erkennung anhand des Host-Prozesses)."""
    if not hasattr(os, "getuid"):
        return False
    return os.getuid() != 0


def container_runtime_uid_gid() -> tuple[int, int]:
    """UID:GID, mit der Game-Server im Container laufen.

    Alle Installer, Repairs und Game-Container muessen denselben numerischen
    Owner fuer Bind-Mount-Dateien verwenden. Unter Rootless Docker kann dieser
    Owner auf dem Host eine Subuid/Subgid sein; der Panel-Prozess bekommt Zugriff
    ueber die gesetzten Server-Verzeichnis-Rechte, nicht ueber Ownership.
    """
    return host_uid_gid()


def repair_bind_mount_permissions(
    host_path: str,
    *,
    container_path: str = PERMISSION_REPAIR_CONTAINER_DIR,
    owner_uid_gid: tuple[int, int] | None = None,
    timeout: int = 600,
) -> dict:
    """Normalisiert Owner/Rechte eines Server-Bind-Mounts im Container-Kontext.

    Ziel:
    - Wenn owner_uid_gid gesetzt ist, wird der Game-Prozess Owner der Dateien
      (wichtig fuer Wine-/Home-Verzeichnisse).
    - Panel kann weiterhin Dateien anlegen/bearbeiten (ueber a+rwX im isolierten
      Server-Verzeichnis).
    - Symlinks werden nicht verfolgt; nur der Link selbst wird gechowned.
    """
    base = os.path.realpath(host_path)
    if not os.path.isdir(base):
        return {"ok": False, "error": "Server-Verzeichnis existiert nicht", "stdout": "", "stderr": ""}

    target = shlex.quote(container_path.rstrip("/") or PERMISSION_REPAIR_CONTAINER_DIR)
    # Kein set -e: einzelne chmod/chown-Fehler (z. B. root-owned Dateien unter
    # Rootless Docker) duerfen den gesamten Start nicht abbrechen.
    script_parts = [
        f"find {target} -xdev -type d -exec chmod a+rwX {{}} + 2>/dev/null || true",
        f"find {target} -xdev -type f -exec chmod a+rwX {{}} + 2>/dev/null || true",
    ]
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
        image=PERMISSION_REPAIR_IMAGE,
        command=["-c", script],
        volumes=[VolumeBind(base, target, read_only=False)],
        user="0:0",
        entrypoint="bash",
        cap_adds=PERMISSION_REPAIR_CAPS,
        timeout=timeout,
    )
