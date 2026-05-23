"""Docker-CLI Wrapper — startet, stoppt und inspiziert Game-Server-Container.

KISS: dünner subprocess-Wrapper um `docker`-CLI. Keine neuen Python-Deps.
Identisches Muster wie `firewall_service.py` (UFW). API klein und auditierbar.

Sicherheitsinvarianten:
- Keine Geheimnisse (Passwörter, API-Keys) landen in Log-Output. Aufrufer ist
  verantwortlich, sensible Env-Werte nicht in Konsole zu schreiben; dieses
  Modul rückt nichts ins Klartext-Log.
- Stderr/Stdout werden nur in strukturierten Rückgaben weitergereicht; nicht
  über `print()` oder ungefiltertes Logging.
- Kein `--privileged`, kein `--network host`, kein `--cap-add` außer explizit
  durch Aufrufer angefragt.
- `_check_docker()` cached das Ergebnis, damit jeder Aufruf billig ist.

Rückgabe-Format einheitlich:
    {"ok": True,  ...payload}
    {"ok": False, "error": "kurze, sichere Fehlermeldung"}
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)

# Fester PATH für Subprozesse — verhindert PATH-Hijacking via User-Env
_SYSTEM_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
}

# Default-Log-Optionen — begrenzen Disk-Druck durch Container-Logs
_DEFAULT_LOG_OPTS = ["--log-driver=json-file", "--log-opt=max-size=10m", "--log-opt=max-file=3"]

# Hardening-Flags, die wir IMMER setzen (KISS: keine Plugin-Override-Option in Phase 1)
_HARDENING_FLAGS = ["--cap-drop=ALL", "--security-opt=no-new-privileges"]


@dataclass(frozen=True)
class PortPublish:
    """Eine einzelne Port-Veröffentlichung für `docker run -p ...`."""
    host_port: int
    container_port: int
    protocol: str = "udp"  # "udp" oder "tcp"
    host_ip: str | None = None  # None = an alle Interfaces binden (0.0.0.0)

    def to_arg(self) -> str:
        proto = self.protocol.lower()
        if proto not in ("tcp", "udp"):
            raise ValueError(f"Ungültiges Protokoll: {self.protocol}")
        if self.host_ip:
            return f"{self.host_ip}:{self.host_port}:{self.container_port}/{proto}"
        return f"{self.host_port}:{self.container_port}/{proto}"


@dataclass(frozen=True)
class VolumeBind:
    """Bind-Mount für `docker run -v ...`."""
    host_path: str
    container_path: str
    read_only: bool = False

    def to_arg(self) -> str:
        flag = "ro" if self.read_only else "rw"
        return f"{self.host_path}:{self.container_path}:{flag}"


_DOCKER_AVAILABLE: bool | None = None


def _check_docker(force: bool = False) -> bool:
    """Cached-Check: ist `docker`-CLI verfügbar?

    Wird beim ersten Aufruf einmal evaluiert. Tests können `force=True` setzen,
    um den Cache zurückzusetzen.
    """
    global _DOCKER_AVAILABLE
    if _DOCKER_AVAILABLE is not None and not force:
        return _DOCKER_AVAILABLE
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=5, env=_SYSTEM_ENV,
        )
        _DOCKER_AVAILABLE = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _DOCKER_AVAILABLE = False
    return _DOCKER_AVAILABLE


def _run_docker(args: list[str], timeout: int = 60, stdin: str | None = None) -> dict:
    """Führt `docker <args>` aus und liefert strukturiertes Ergebnis.

    Niemals Stack-Traces nach außen. Bei Fehlern eine kurze, sichere Meldung.
    """
    if not _check_docker():
        return {"ok": False, "error": "Docker ist nicht verfügbar", "stdout": "", "stderr": ""}

    cmd = ["docker", *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_SYSTEM_ENV,
            input=stdin,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            # Stderr nicht direkt loggen — könnte Pfade/Env-Werte enthalten
            logger.warning("docker %s fehlgeschlagen (rc=%d)", args[0] if args else "?", result.returncode)
            return {"ok": False, "error": err, "stdout": result.stdout, "stderr": result.stderr}
        return {"ok": True, "stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Docker-Befehl hat Timeout überschritten", "stdout": "", "stderr": ""}
    except FileNotFoundError:
        return {"ok": False, "error": "Docker ist nicht verfügbar", "stdout": "", "stderr": ""}
    except OSError as e:
        logger.warning("docker subprocess OSError: %s", e)
        return {"ok": False, "error": "Docker-Befehl konnte nicht ausgeführt werden", "stdout": "", "stderr": ""}


# ── Lifecycle ──────────────────────────────────────────────────────────────


def is_available() -> bool:
    """Public-API: ist Docker auf diesem Host nutzbar?"""
    return _check_docker(force=True)


def pull(image: str) -> dict:
    """Lädt ein Image vor (idempotent, ok wenn schon lokal)."""
    return _run_docker(["pull", image], timeout=600)


def exists(name: str) -> bool:
    """Prüft, ob ein Container mit diesem Namen existiert (auch wenn gestoppt)."""
    result = _run_docker(["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
    if not result["ok"]:
        return False
    return name in result["stdout"].splitlines()


def is_running(name: str) -> bool:
    result = _run_docker(["ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
    if not result["ok"]:
        return False
    return name in result["stdout"].splitlines()


def remove(name: str, force: bool = True) -> dict:
    """Entfernt einen Container (idempotent — kein Fehler, wenn nicht vorhanden)."""
    if not exists(name):
        return {"ok": True, "stdout": "", "stderr": "", "note": "container did not exist"}
    args = ["rm"]
    if force:
        args.append("-f")
    args.append(name)
    return _run_docker(args, timeout=30)


def stop(name: str, timeout: int = 30) -> dict:
    """Stoppt einen Container mit Graceful-Timeout."""
    if not is_running(name):
        return {"ok": True, "stdout": "", "stderr": "", "note": "container was not running"}
    return _run_docker(["stop", "-t", str(timeout), name], timeout=timeout + 10)


def start(name: str) -> dict:
    """Startet einen existierenden, gestoppten Container neu (nutzt vorhandene Config)."""
    return _run_docker(["start", name], timeout=30)


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
    """Startet einen neuen, langlebigen Container für einen Game-Server.

    Falls bereits ein Container mit demselben Namen existiert (z. B. gestoppt
    vom letzten Lauf), wird er vorher entfernt — wir starten immer mit frischen
    Flags, weil Limits/Ports sich geändert haben könnten.
    """
    # Vorherigen Container räumen — KISS, immer frische Config.
    if exists(name):
        remove_result = remove(name, force=True)
        if not remove_result["ok"]:
            return remove_result

    args: list[str] = ["run"]
    if detach:
        args.append("-d")
    args.extend(["--name", name])
    args.extend(["--restart=on-failure:5"])
    args.extend(_DEFAULT_LOG_OPTS)
    args.extend(_HARDENING_FLAGS)

    if read_only_rootfs:
        args.append("--read-only")

    if tmpfs_paths:
        for path in tmpfs_paths:
            args.extend(["--tmpfs", f"{path}:rw,size=64m,mode=1777"])

    if user:
        args.extend(["--user", user])

    if workdir:
        args.extend(["--workdir", workdir])

    if cpu_limit_percent is not None and cpu_limit_percent > 0:
        # 100 % == 1 Core; 200 % == 2 Cores
        cpus = round(cpu_limit_percent / 100.0, 2)
        args.extend([f"--cpus={cpus}"])

    if ram_limit_mb is not None and ram_limit_mb > 0:
        args.extend([f"--memory={ram_limit_mb}m"])
        # Swap == RAM verhindert, dass Container in Swap überläuft und Host destabilisiert.
        args.extend([f"--memory-swap={ram_limit_mb}m"])

    if env:
        for key, value in env.items():
            # Niemals den Wert in Logs schreiben — wir übergeben ihn direkt an docker.
            args.extend(["-e", f"{key}={value}"])

    if ports:
        for p in ports:
            args.extend(["-p", p.to_arg()])

    if volumes:
        for v in volumes:
            args.extend(["-v", v.to_arg()])

    if extra_args:
        args.extend(extra_args)

    args.append(image)
    if command:
        args.extend(command)

    return _run_docker(args, timeout=120)


def run_ephemeral(
    *,
    image: str,
    command: list[str],
    volumes: list[VolumeBind] | None = None,
    env: dict[str, str] | None = None,
    user: str | None = None,
    workdir: str | None = None,
    timeout: int = 1800,
) -> dict:
    """Führt ein einmaliges `docker run --rm`-Kommando aus (für SteamCMD-Installs etc.).

    Blockiert bis der Container beendet ist (oder Timeout).
    """
    args: list[str] = ["run", "--rm"]
    args.extend(_HARDENING_FLAGS)

    if user:
        args.extend(["--user", user])
    if workdir:
        args.extend(["--workdir", workdir])
    if env:
        for key, value in env.items():
            args.extend(["-e", f"{key}={value}"])
    if volumes:
        for v in volumes:
            args.extend(["-v", v.to_arg()])

    args.append(image)
    args.extend(command)

    return _run_docker(args, timeout=timeout)


# ── Inspect / Stats / Logs ─────────────────────────────────────────────────


def inspect_state(name: str) -> dict | None:
    """Liefert State-Felder (Status, StartedAt, ExitCode) oder None."""
    if not exists(name):
        return None
    fmt = (
        "{{.State.Status}}|{{.State.StartedAt}}|{{.State.ExitCode}}|{{.State.OOMKilled}}"
    )
    result = _run_docker(["inspect", "--format", fmt, name])
    if not result["ok"]:
        return None
    parts = result["stdout"].strip().split("|")
    if len(parts) < 4:
        return None
    return {
        "status": parts[0],
        "started_at": parts[1],
        "exit_code": int(parts[2]) if parts[2].lstrip("-").isdigit() else None,
        "oom_killed": parts[3].lower() == "true",
    }


def stats(name: str) -> dict | None:
    """Liefert CPU%/RAM (MB) für einen laufenden Container — one-shot.

    Nutzt `docker stats --no-stream`. Bei gestopptem Container: None.
    """
    if not is_running(name):
        return None
    result = _run_docker(
        ["stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", name],
        timeout=10,
    )
    if not result["ok"]:
        return None
    line = result["stdout"].strip().splitlines()
    if not line:
        return None
    parts = line[0].split("|")
    if len(parts) < 2:
        return None
    cpu_str, mem_str = parts[0], parts[1]
    cpu_percent: float | None = None
    try:
        cpu_percent = float(cpu_str.rstrip("%").strip())
    except ValueError:
        cpu_percent = None
    ram_mb = _parse_mem_used_mb(mem_str)
    return {"cpu_percent": cpu_percent, "ram_mb": ram_mb}


def _parse_mem_used_mb(mem_usage: str) -> int | None:
    """Wandelt `docker stats`-MemUsage ("123.4MiB / 2GiB") in genutzte MB."""
    if not mem_usage:
        return None
    used = mem_usage.split("/")[0].strip()
    # Beispiele: "123.4MiB", "1.2GiB", "456KiB", "789B"
    number = ""
    for ch in used:
        if ch.isdigit() or ch == ".":
            number += ch
        else:
            break
    unit = used[len(number):].strip().lower()
    try:
        value = float(number)
    except ValueError:
        return None
    if unit.startswith("g"):
        return int(value * 1024)
    if unit.startswith("m"):
        return int(value)
    if unit.startswith("k"):
        return int(value / 1024)
    if unit.startswith("b") or unit == "":
        return int(value / (1024 * 1024))
    return None


def logs(name: str, lines: int = 200) -> str:
    """Liefert die letzten N Zeilen Container-Logs als String. Leer bei Fehler."""
    if not exists(name):
        return ""
    result = _run_docker(["logs", "--tail", str(lines), name], timeout=15)
    if not result["ok"]:
        return ""
    # stdout + stderr zusammen, damit auch Crash-Output sichtbar bleibt
    return (result.get("stdout") or "") + (result.get("stderr") or "")


def exec_in(name: str, command: list[str], timeout: int = 30) -> dict:
    """Führt ein Kommando in einem laufenden Container aus."""
    if not is_running(name):
        return {"ok": False, "error": "Container läuft nicht", "stdout": "", "stderr": ""}
    args = ["exec", name, *command]
    return _run_docker(args, timeout=timeout)


# ── Disk-Usage (Soft-Limit) ────────────────────────────────────────────────


def disk_usage_mb(path: str) -> int | None:
    """Liefert die Bytes-Größe eines Pfads in MB (gerundet) oder None bei Fehler.

    Nutzt `du -sb` für Konsistenz mit Disk-Soft-Limit. Verhält sich gutartig,
    wenn der Pfad nicht existiert oder Lese-Rechte fehlen.
    """
    if not os.path.isdir(path):
        return None
    try:
        result = subprocess.run(
            ["du", "-sb", path],
            capture_output=True, text=True, timeout=60, env=_SYSTEM_ENV,
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
    """Aktuelles UID:GID des Panel-Prozesses. Wird als `--user` an Container übergeben,
    damit Bind-Mount-Files die korrekte Besitzer haben.
    """
    return os.getuid(), os.getgid()
