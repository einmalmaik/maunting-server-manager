"""Game-Plugin-Basis — Docker-only Lifecycle.

Jeder GamePlugin liefert minimale, Docker-spezifische Bausteine:
  - build_container_command(server)  → cmd-Args im Container
  - build_container_env(server)      → ENV-Vars im Container
  - build_port_publishes(server)     → Liste von PortPublish (host↔container)
  - build_volume_binds(server)       → Liste von VolumeBind (host↔container)

`start/stop/get_status/get_logs` haben Default-Implementierungen in der Basis,
die alle Container-Operationen über `docker_service` ausführen. Plugins
überschreiben nur, was wirklich game-spezifisch ist (z. B. Custom-Workshop-
Pfade beim install_mod).

Es gibt KEINE systemd-/linux-user-Pfade mehr. Game-Server laufen ausschließlich
in Docker-Containern. Isolation kommt von Docker, nicht von POSIX-Usern.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from services import docker_service
from services.docker_service import PortPublish, VolumeBind


def _require_bind_ip(server) -> str:
    """Liefert ``server.public_bind_ip`` oder wirft RuntimeError.

    Wird vor jedem Container-Start aufgerufen — ohne explizite Bind-IP würde
    Docker auf 0.0.0.0 binden und die UFW-Regeln umgehen.
    """
    bind_ip = getattr(server, "public_bind_ip", None) or None
    if not bind_ip or bind_ip == "0.0.0.0":
        raise RuntimeError(
            "public_bind_ip fehlt oder ist 0.0.0.0 — bitte im Server-Detail "
            "eine konkrete Host-IP zuweisen, bevor der Server gestartet wird."
        )
    return bind_ip


def default_port_publishes(server) -> list[PortPublish]:
    """Standard-Port-Mapping: game/query UDP, rcon TCP — immer an Bind-IP."""
    bind_ip = _require_bind_ip(server)
    ports: list[PortPublish] = []
    if server.game_port:
        ports.append(PortPublish(server.game_port, server.game_port, "udp", bind_ip))
    if server.query_port:
        ports.append(PortPublish(server.query_port, server.query_port, "udp", bind_ip))
    if server.rcon_port:
        ports.append(PortPublish(server.rcon_port, server.rcon_port, "tcp", bind_ip))
    return ports

logger = logging.getLogger(__name__)


@dataclass
class ConfigField:
    key: str
    label: str
    type: str  # text, number, bool, select, textarea
    default: Any = None
    options: list[str] | None = None
    description: str = ""
    required: bool = False


@dataclass
class ServerStatus:
    status: str  # stopped, running, installing, updating, starting, error
    cpu_percent: float | None = None
    ram_mb: int | None = None
    disk_mb: int | None = None
    uptime_seconds: int | None = None
    message: str | None = None


def _map_container_status(docker_status: str) -> str:
    """Mapped Docker-Container-States auf MSM-Status-Codes.

    Docker liefert u. a. "exited", "dead", "created", "removing", "paused",
    "restarting", "running". Diese Strings landen sonst 1:1 im Frontend und
    sind dort nicht uebersetzbar. Wir reduzieren auf das kleine Set, das die
    UI versteht und i18n-Schluessel hat.
    """
    if docker_status == "running":
        return "running"
    if docker_status == "restarting":
        return "starting"
    # exited, dead, created, removing, paused -> stopped
    return "stopped"


# ── Console-Logging (MSM-eigene Log-Datei pro Server) ──────────────────────


def _console_log_path(server_id: int) -> str:
    """Pfad zur MSM Console-Log-Datei. Liegt zentral unter backend/logs/<id>/console.log
    — unabhängig vom install_dir, damit der Pfad auch dann existiert, wenn der
    Bind-Mount geleert wird.
    """
    base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    srv_dir = os.path.join(base_dir, str(server_id))
    os.makedirs(srv_dir, exist_ok=True)
    return os.path.join(srv_dir, "console.log")


def _append_console_log(server_id: int, text: str) -> None:
    try:
        log_path = _console_log_path(server_id)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text)
            f.flush()
    except OSError as e:
        logger.warning("Could not write console log for server %s: %s", server_id, e)


def finish_install(server_id: int, result: dict) -> None:
    """Background-Thread-Callback: setzt Server-Status nach SteamCMD-Lauf.

    Wird aus dem Hintergrund-Thread aufgerufen, der die Installation/Update
    ausführt. Muss eine FRISCHE DB-Session öffnen, weil die Request-Session
    längst geschlossen ist (Request endete mit "Installation gestartet").

    Bei Erfolg → status="stopped" (bereit zum Starten).
    Bei Fehler → status="error" + Fehlertext (gekürzt).
    """
    # Inline-Imports, weil base.py beim Modul-Load noch keine Modelle kennen darf
    # (zirkulärer Import via games.__init__).
    from database import SessionLocal
    from models import Server

    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            return
        if result.get("ok"):
            server.status = "stopped"
            server.status_message = None
        else:
            err = result.get("error") or "Installation fehlgeschlagen"
            server.status = "error"
            server.status_message = err[:500]
        db.commit()
    except Exception as e:
        # Kein Re-Raise — Thread soll nicht crashen, nur loggen
        logger.warning("finish_install failed for server %s: %s", server_id, e)
        db.rollback()
    finally:
        db.close()


# ── Container-Name ─────────────────────────────────────────────────────────


def container_name_for(server_id: int) -> str:
    """Stabile, vorhersagbare Container-Bezeichnung pro Server."""
    return f"msm-srv-{server_id}"


# ── Bind-Mount-Layout ──────────────────────────────────────────────────────


# Im Container wird das Server-Verzeichnis IMMER unter /data eingehängt.
# Plugins bauen ihre Kommandos relativ zu /data.
CONTAINER_DATA_DIR = "/data"


def default_volume_binds(server) -> list[VolumeBind]:
    """Standard-Bind: install_dir (Host) → /data (Container, RW)."""
    return [VolumeBind(host_path=server.install_dir, container_path=CONTAINER_DATA_DIR, read_only=False)]


# ── SteamCMD-Installer im ephemeren Container ──────────────────────────────


# Kuratiertes, gepflegtes SteamCMD-Image. Eine Pin auf einen festen Tag erfolgt
# in einer Folge-Phase (Phase 5 wird Image+Tag pro Plugin-Egg konfigurierbar).
STEAMCMD_IMAGE = "cm2network/steamcmd:root"
# Pfad des steamcmd-Wrappers im Image. Wird in den bash-Aufruf eingesetzt.
STEAMCMD_BIN = "/home/steam/steamcmd/steamcmd.sh"
# Caps, die wir nach `--cap-drop=ALL` für den SteamCMD-Lauf wieder brauchen:
# - DAC_OVERRIDE: /home/steam (mode 700, steam-User-only) traversieren
# - DAC_READ_SEARCH: idem, für Read-Operationen ohne Owner-Match
# - CHOWN + FOWNER: am Ende `chown -R uid:gid /data`
STEAMCMD_CAPS = ["DAC_OVERRIDE", "DAC_READ_SEARCH", "CHOWN", "FOWNER"]


def _build_steamcmd_bash_command(steam_args: list[str], chown_uid: int, chown_gid: int) -> list[str]:
    """Verpackt SteamCMD-Args in ein `bash -c`-Kommando, das nach Abschluss /data
    auf die Host-UID umownt.

    Hintergrund: Das `:root`-Image hat `/home/steam` mit Mode 750 (steam-User-
    only). Wir müssen SteamCMD als root im Container laufen lassen, sonst
    schlägt schon das `stat`/`exec` auf den Wrapper mit `permission denied`
    fehl. Container-Root ist durch `--cap-drop=ALL --security-opt=no-new-
    privileges` und einen Bind-Mount-only-Schreibpfad genügend abgeschottet.
    Nach dem Lauf chown'en wir /data zurück auf die msm-Host-UID, damit das
    Panel als unprivilegierter User weiterarbeiten kann.
    """
    quoted = " ".join(shlex.quote(a) for a in steam_args)
    script = (
        f"{shlex.quote(STEAMCMD_BIN)} {quoted}; "
        "rc=$?; "
        f"chown -R {int(chown_uid)}:{int(chown_gid)} {shlex.quote(CONTAINER_DATA_DIR)}; "
        "exit $rc"
    )
    return ["-c", script]


def run_steamcmd_install(
    *,
    server_id: int,
    install_dir: str,
    app_id: str,
    extra_args: list[str] | None = None,
) -> dict:
    """Lädt/aktualisiert eine Steam-App in `install_dir` via ephemerem
    SteamCMD-Container. Blockiert bis SteamCMD fertig ist.

    Schreibt strukturiertes Console-Log und gibt strukturiertes dict zurück.
    """
    os.makedirs(install_dir, exist_ok=True)

    steam_args: list[str] = [
        "+force_install_dir", CONTAINER_DATA_DIR,
        "+login", "anonymous",
        "+app_update", app_id, "validate",
    ]
    if extra_args:
        steam_args.extend(extra_args)
    steam_args.append("+quit")

    _append_console_log(server_id, f"[MSM] SteamCMD startet für App {app_id} (Docker)\n")

    uid, gid = docker_service.host_uid_gid()
    result = docker_service.run_ephemeral(
        image=STEAMCMD_IMAGE,
        command=_build_steamcmd_bash_command(steam_args, uid, gid),
        volumes=[VolumeBind(install_dir, CONTAINER_DATA_DIR, read_only=False)],
        # Explizit Container-Root: das `:root`-Image hat /home/steam Mode 700
        # für den steam-User. Files werden im bash-Wrapper nach dem Run auf
        # {uid}:{gid} ge-chown't, damit der Panel-User sie danach lesen kann.
        user="0:0",
        # Nach --cap-drop=ALL die minimal nötigen Caps wiederherstellen, damit
        # Container-Root nicht von Linux-DAC eingeschränkt wird (sonst greift
        # Mode-700 auch für root, weil CAP_DAC_OVERRIDE fehlt).
        cap_adds=STEAMCMD_CAPS,
        entrypoint="bash",
        # SteamCMD legt Cache/Auth in $HOME ab. Auf /data umleiten, damit der
        # Cache zwischen Runs persistent im Bind-Mount landet (kein Vollredownload).
        env={"HOME": CONTAINER_DATA_DIR},
        timeout=3600,
    )

    if result["ok"]:
        # SteamCMD-Output ins Console-Log spiegeln (ohne Stderr separat zu loggen — wir
        # kombinieren bewusst, damit das UI eine vollständige Sicht hat)
        out = (result.get("stdout") or "") + (result.get("stderr") or "")
        _append_console_log(server_id, out)
        _append_console_log(server_id, f"\n[MSM] SteamCMD abgeschlossen (App {app_id}).\n")
    else:
        _append_console_log(server_id, f"\n[MSM] SteamCMD fehlgeschlagen: {result['error']}\n")
    return result


def run_steamcmd_workshop_download(
    *,
    server_id: int,
    install_dir: str,
    workshop_app_id: str,
    workshop_item_id: str,
) -> dict:
    """Lädt ein Workshop-Item via ephemerem SteamCMD-Container.

    Intelligent: SteamCMD validiert und holt nur Deltas. Kein erzwungenes
    Vollredownload — das übernimmt SteamCMD selbst, sofern lokale Files
    existieren.
    """
    steam_args: list[str] = [
        "+force_install_dir", CONTAINER_DATA_DIR,
        "+login", "anonymous",
        "+workshop_download_item", workshop_app_id, workshop_item_id,
        "+quit",
    ]
    _append_console_log(
        server_id, f"[MSM] SteamCMD Workshop-Download: app={workshop_app_id} item={workshop_item_id}\n"
    )
    uid, gid = docker_service.host_uid_gid()
    result = docker_service.run_ephemeral(
        image=STEAMCMD_IMAGE,
        command=_build_steamcmd_bash_command(steam_args, uid, gid),
        volumes=[VolumeBind(install_dir, CONTAINER_DATA_DIR, read_only=False)],
        user="0:0",
        cap_adds=STEAMCMD_CAPS,
        entrypoint="bash",
        env={"HOME": CONTAINER_DATA_DIR},
        timeout=3600,
    )
    out = (result.get("stdout") or "") + (result.get("stderr") or "")
    if out:
        _append_console_log(server_id, out)
    if not result["ok"]:
        _append_console_log(
            server_id, f"\n[MSM] Workshop-Download fehlgeschlagen: {result['error']}\n"
        )
    return result


# ── Plugin-Basis ───────────────────────────────────────────────────────────


class GamePlugin(ABC):
    """Basisklasse für Game-Plugins.

    Pflichtfelder:
      - game_id, game_name, supports_mods, supports_steam_workshop
      - docker_image: das Base-Image, in dem der Server läuft
      - container_needs_tmpfs: wenn True, wird /tmp als tmpfs hinzugefügt

    Pflichtmethoden:
      - build_container_command, build_container_env, build_port_publishes,
        get_config_schema, get_config_files, get_backup_paths, get_logs
    """

    game_id: str = ""
    game_name: str = ""
    supports_mods: bool = False
    # Phase 4 — Capability-Flag: kommt der Mod-Manager-Tab im Frontend zum
    # Einsatz? Wenn False, blendet die UI den Workshop-Browser aus und kann den
    # Mod-Manager-Tab ausblenden (Backend bleibt zusaetzlich Defensiv-Layer).
    supports_steam_workshop: bool = False

    # Docker-spezifisch — Pflicht für alle konkreten Plugins
    docker_image: str = ""
    container_needs_tmpfs: bool = True  # /tmp als tmpfs (read-only rootfs ist Default)
    container_read_only_rootfs: bool = False  # Game-Binaries schreiben oft in WorkingDir → False

    # ─ Setup / Lifecycle ─────────────────────────────────────────────────

    @abstractmethod
    def install(self, server) -> dict:
        """Installiert oder aktualisiert die Game-Binaries. Threading erlaubt."""
        ...

    def update(self, server) -> dict:
        """Standard-Update == frische Installation (SteamCMD validate macht es smart)."""
        return self.install(server)

    @abstractmethod
    def build_container_command(self, server) -> list[str]:
        """Args, mit denen der Server im Container gestartet wird (ohne Image)."""
        ...

    def build_container_env(self, server) -> dict[str, str]:
        """Default: keine zusätzlichen Env-Vars."""
        return {}

    def build_port_publishes(self, server) -> list[PortPublish]:
        """Welche Ports werden veröffentlicht? (game/query/rcon).

        Default-Implementierung: bindet game_port/query_port als UDP und
        rcon_port als TCP an ``server.public_bind_ip``. Diese darf NIE
        ``None`` oder ``0.0.0.0`` sein — sonst publiziert Docker auf allen
        Interfaces und hängt die UFW-Falle aus. Plugins, die andere Ports
        brauchen, können überschreiben — die Bind-IP-Pflicht bleibt aber
        bestehen.
        """
        return default_port_publishes(server)

    def build_volume_binds(self, server) -> list[VolumeBind]:
        """Default: nur install_dir → /data."""
        return default_volume_binds(server)

    def container_workdir(self, server) -> str:
        return CONTAINER_DATA_DIR

    def container_tmpfs_paths(self, server) -> list[str]:
        return ["/tmp"] if self.container_needs_tmpfs else []

    def prepare_runtime(self, server) -> None:
        """Hook: vor jedem Container-Start aufgerufen.

        Plugins können hier game-spezifische Config-Files (INIs, .cfg, JSON)
        vorbereiten — z. B. Ports in `Engine.ini`/`Game.ini` schreiben,
        damit das Spiel die vom Port-Manager zugewiesenen Werte tatsächlich
        nutzt. CLI-Argumente reichen bei vielen UE-Spielen NICHT.

        Default ist no-op. Side-effects sind erlaubt (Dateischreiben in
        install_dir), aber bewusst KEIN Container-Code, kein Logging
        sensibler Werte und keine Network-Calls.
        """
        return None

    # ─ Default Lifecycle (Docker) ────────────────────────────────────────

    def start(self, server) -> dict:
        """Standard-Start: Container mit aktuellen Limits/Ports neu hochziehen."""
        if not self.docker_image:
            return {"error": "Plugin hat kein docker_image konfiguriert"}
        if not docker_service.is_available():
            return {"error": "Docker ist auf diesem Host nicht verfügbar"}

        # Pflicht-Bind-IP früh validieren — sonst riskieren wir 0.0.0.0.
        try:
            port_publishes = self.build_port_publishes(server)
        except RuntimeError as e:
            _append_console_log(server.id, f"[MSM] Start abgelehnt: {e}\n")
            return {"error": str(e)}

        # Game-spezifische Config-Files vor dem Start aktualisieren (Ports, etc.)
        try:
            self.prepare_runtime(server)
        except Exception as e:
            _append_console_log(
                server.id, f"[MSM] prepare_runtime fehlgeschlagen: {e}\n"
            )

        # Image bei Bedarf vorziehen — KISS, scheitert nicht hart bei Offline
        pull_result = docker_service.pull(self.docker_image)
        if not pull_result["ok"]:
            _append_console_log(
                server.id, f"[MSM] Hinweis: Pull für {self.docker_image} fehlgeschlagen, nutze lokales Image\n"
            )

        uid, gid = docker_service.host_uid_gid()
        name = container_name_for(server.id)

        result = docker_service.run_container(
            name=name,
            image=self.docker_image,
            command=self.build_container_command(server),
            env=self.build_container_env(server),
            ports=port_publishes,
            volumes=self.build_volume_binds(server),
            cpu_limit_percent=server.cpu_limit_percent,
            ram_limit_mb=server.ram_limit_mb,
            user=f"{uid}:{gid}",
            workdir=self.container_workdir(server),
            read_only_rootfs=self.container_read_only_rootfs,
            tmpfs_paths=self.container_tmpfs_paths(server),
        )
        if not result["ok"]:
            _append_console_log(server.id, f"[MSM] Container-Start fehlgeschlagen: {result['error']}\n")
            return {"error": result["error"]}

        _append_console_log(server.id, f"[MSM] Container {name} gestartet\n")

        # Optionaler Auto-Backup-Trigger (Fire-and-forget, lokaler Loopback)
        try:
            import requests
            requests.post(f"http://127.0.0.1:8000/api/backups/{server.id}/auto", timeout=5)
        except Exception:
            pass

        return {"message": "Server gestartet", "container": name}

    def stop(self, server) -> dict:
        """Standard-Stop: Container graceful stoppen (30 s)."""
        name = container_name_for(server.id)
        result = docker_service.stop(name, timeout=30)
        if not result["ok"]:
            return {"error": result["error"]}
        _append_console_log(server.id, f"[MSM] Container {name} gestoppt\n")
        return {"message": "Server gestoppt", "container": name}

    def get_status(self, server) -> ServerStatus:
        """Liefert Live-Status aus Docker (Container-State + CPU/RAM via stats)."""
        name = container_name_for(server.id)
        state = docker_service.inspect_state(name)
        if state is None:
            return ServerStatus(status="stopped")

        is_running = state["status"] == "running"
        live_stats = docker_service.stats(name) if is_running else None

        msg = None
        if state.get("oom_killed"):
            msg = "Container wurde wegen RAM-Limit beendet (OOM)"

        return ServerStatus(
            status=_map_container_status(state["status"]),
            cpu_percent=(live_stats or {}).get("cpu_percent"),
            ram_mb=(live_stats or {}).get("ram_mb"),
            disk_mb=None,  # Disk wird zentral im Scheduler-Job ermittelt
            uptime_seconds=None,
            message=msg,
        )

    # ─ Logs ──────────────────────────────────────────────────────────────

    @abstractmethod
    def get_logs(self, server, lines: int = 100) -> str:
        """Liest die game-spezifischen Logs (z. B. UE-/DayZ-Logfile im install_dir)."""
        ...

    def get_console_log(self, server, lines: int = 200) -> str:
        """MSM-Console-Log (Install-Output, MSM-Events) + Docker-Container-Logs."""
        log_path = _console_log_path(server.id)
        msm_part = ""
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                msm_part = "".join(all_lines[-lines:])
            except Exception:
                msm_part = ""
        docker_part = docker_service.logs(container_name_for(server.id), lines=lines)
        if not docker_part:
            return msm_part
        return msm_part + "\n--- container logs ---\n" + docker_part

    # ─ Config ────────────────────────────────────────────────────────────

    @abstractmethod
    def get_config_schema(self) -> list[ConfigField]:
        ...

    @abstractmethod
    def get_config_files(self) -> list[dict]:
        ...

    @abstractmethod
    def get_backup_paths(self, server) -> list[str]:
        ...

    # ─ Mods ──────────────────────────────────────────────────────────────

    def install_mod(self, server, workshop_id: str) -> dict:
        """Default: Plugins ohne Mod-Support liefern einen Fehler."""
        if not self.supports_mods:
            return {"error": "Mod-Installation nicht unterstützt"}
        return {"error": "Mod-Installation nicht implementiert"}

    def get_mod_support(self) -> dict | None:
        if self.supports_mods:
            return {"workshop_id": None, "dependency_resolution": False, "required_tags": []}
        return None
