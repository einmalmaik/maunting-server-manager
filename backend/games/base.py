"""Game-Plugin-Basis — Docker-only Lifecycle.

Jeder GamePlugin liefert minimale, Docker-spezifische Bausteine:
  - build_container_command(server)  → cmd-Args im Container
  - build_container_env(server)      → ENV-Vars im Container
  - build_port_publishes(server)     → Liste von PortPublish (host↔container)
  - build_volume_binds(server)       → Liste von VolumeBind (host↔container)

`start/stop/get_status/get_logs` haben Default-Implementierungen in der Basis,
die alle Container-Operationen über `docker_service` ausführen. Server-Typen
kommen aus Blueprints; game-spezifische Runtime-Varianten werden deklarativ in
der Blueprint beschrieben und vom generischen `BlueprintPlugin` ausgeführt.

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

from blueprints.schema import Blueprint, BlueprintModInjection
from games import updater  # zentrale KISS-Updater-Logik (Blueprint-konform)


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

    Bei Erfolg → status="stopped" (bereit zum Starten), oder ``next_status``
    aus dem Result-Dict (z. B. "awaiting_files").
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
            server.status = result.get("next_status", "stopped")
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
        f"chmod -R a+rwx {shlex.quote(CONTAINER_DATA_DIR)}; "
        "exit $rc"
    )
    return ["-c", script]


def _redact(text: str, secrets_to_redact: list[str]) -> str:
    for s in secrets_to_redact:
        if s:
            text = text.replace(s, "***")
    return text


def run_steamcmd_install(
    *,
    server_id: int,
    install_dir: str,
    app_id: str,
    extra_args: list[str] | None = None,
    use_authenticated_login: bool = False,
    platform: str | None = None,
) -> dict:
    """Lädt/aktualisiert eine Steam-App in `install_dir` via ephemerem
    SteamCMD-Container. Blockiert bis SteamCMD fertig ist.

    Schreibt strukturiertes Console-Log und gibt strukturiertes dict zurück.
    """
    from services.steam_account_service import SteamAccountService

    os.makedirs(install_dir, exist_ok=True)

    if use_authenticated_login:
        if not SteamAccountService.is_configured():
            err = (
                "Dieses Spiel benötigt einen globalen Steam-Account-Login. "
                "Bitte unter Einstellungen → Steam Account einen Benutzer "
                "und Passwort hinterlegen (Steam Guard muss deaktiviert sein)."
            )
            _append_console_log(server_id, f"[MSM] {err}\n")
            return {"ok": False, "error": err}
        username = SteamAccountService.get_username()
        password = SteamAccountService.get_decrypted_password()
        login_args = ["+login", username, password]
        secrets_to_redact = [password]
    else:
        login_args = ["+login", "anonymous"]
        secrets_to_redact = []

    steam_args: list[str] = []
    if platform:
        steam_args.extend(["+@sSteamCmdForcePlatformType", platform])
    steam_args.extend([
        "+force_install_dir", CONTAINER_DATA_DIR,
        *login_args,
        "+app_update", app_id, "validate",
    ])
    if extra_args:
        steam_args.extend(extra_args)
    steam_args.append("+quit")

    _append_console_log(server_id, f"[MSM] SteamCMD startet für App {app_id} (Docker)\n")

    uid, gid = docker_service.container_runtime_uid_gid()
    chown_uid, chown_gid = uid, gid

    def _live_log(line: str) -> None:
        """Callback für Live-Streaming: redact Secrets, dann in Console-Log schreiben."""
        _append_console_log(server_id, _redact(line, secrets_to_redact))

    result = docker_service.run_ephemeral(
        image=STEAMCMD_IMAGE,
        command=_build_steamcmd_bash_command(steam_args, chown_uid, chown_gid),
        volumes=[VolumeBind(install_dir, CONTAINER_DATA_DIR, read_only=False)],
        user="0:0",
        cap_adds=STEAMCMD_CAPS,
        entrypoint="bash",
        env={"HOME": CONTAINER_DATA_DIR},
        timeout=3600,
        log_callback=_live_log,
    )

    # Bei Live-Streaming ist out leer (— wurde bereits live geschrieben).
    # Fallback für den Fall, dass der Stream unterbrochen wurde.
    out = (result.get("stdout") or "") + (result.get("stderr") or "")
    if out:
        _append_console_log(server_id, _redact(out, secrets_to_redact))
    if result["ok"]:
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
    use_authenticated_login: bool = False,
) -> dict:
    """Lädt ein Workshop-Item via ephemerem SteamCMD-Container.

    Intelligent: SteamCMD validiert und holt nur Deltas. Kein erzwungenes
    Vollredownload — das übernimmt SteamCMD selbst, sofern lokale Files
    existieren.
    """
    from services.steam_account_service import SteamAccountService

    if use_authenticated_login:
        if not SteamAccountService.is_configured():
            err = (
                "Dieses Spiel benötigt einen Steam-Account für Workshop-Downloads. "
                "Bitte unter Einstellungen → Steam Account hinterlegen."
            )
            _append_console_log(server_id, f"[MSM] {err}\n")
            return {"ok": False, "error": err}
        username = SteamAccountService.get_username()
        password = SteamAccountService.get_decrypted_password()
        login_args = ["+login", username, password]
        secrets_to_redact = [password]
    else:
        login_args = ["+login", "anonymous"]
        secrets_to_redact = []

    steam_args: list[str] = [
        "+force_install_dir", CONTAINER_DATA_DIR,
        *login_args,
        "+workshop_download_item", workshop_app_id, workshop_item_id,
        "+quit",
    ]
    _append_console_log(
        server_id, f"[MSM] SteamCMD Workshop-Download: app={workshop_app_id} item={workshop_item_id}\n"
    )

    def _live_log(line: str) -> None:
        _append_console_log(server_id, _redact(line, secrets_to_redact))

    uid, gid = docker_service.container_runtime_uid_gid()
    chown_uid, chown_gid = uid, gid
    result = docker_service.run_ephemeral(
        image=STEAMCMD_IMAGE,
        command=_build_steamcmd_bash_command(steam_args, chown_uid, chown_gid),
        volumes=[VolumeBind(install_dir, CONTAINER_DATA_DIR, read_only=False)],
        user="0:0",
        cap_adds=STEAMCMD_CAPS,
        entrypoint="bash",
        env={"HOME": CONTAINER_DATA_DIR},
        timeout=3600,
        log_callback=_live_log,
    )
    out = (result.get("stdout") or "") + (result.get("stderr") or "")
    if out:
        _append_console_log(server_id, _redact(out, secrets_to_redact))
    if not result["ok"]:
        _append_console_log(
            server_id, f"\n[MSM] Workshop-Download fehlgeschlagen: {result['error']}\n"
        )
    return result


# ── Mod-Helfer (Blueprint-getrieben) ───────────────────────────────────────


def _query_active_mods(server_id: int) -> list:
    """Liest aktive Mods (``enabled=True``) in ``load_order``-Reihenfolge.

    Eine frische DB-Session, weil die Request-Session bei Background-Threads
    laengst geschlossen sein kann. Wir geben ORM-Objekte zurueck, damit der
    Caller auch ``workshop_id``, ``name`` etc. lesen kann.
    """
    from database import SessionLocal
    from models import Mod

    db = SessionLocal()
    try:
        return (
            db.query(Mod)
            .filter(Mod.server_id == server_id, Mod.enabled == True)  # noqa: E712
            .order_by(Mod.load_order.asc())
            .all()
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Active-Mods-Abfrage fuer Server %s fehlgeschlagen: %s", server_id, exc)
        return []
    finally:
        db.close()


def active_mod_ids(server) -> list[str]:
    """Liefert Workshop-IDs aller aktiven Mods in Lade-Reihenfolge."""
    return [m.workshop_id for m in _query_active_mods(server.id)]


def write_workshop_modlist(server, relative_path: str, lines: list[str]) -> None:
    """Schreibt eine Workshop-Modliste in eine sichere Datei unter ``install_dir``.

    Sicherheits-Invarianten:
    - ``relative_path`` MUSS relativ sein und darf nach ``realpath``-Aufloesung
      nicht aus ``install_dir`` ausbrechen (Symlink-Escape-Schutz).
    - Bei jedem Fehler wird in das Console-Log geschrieben, NICHT geraised —
      sonst wuerden Mod-Mutationen im Router crashen.
    """
    install_dir = getattr(server, "install_dir", None) or ""
    if not install_dir:
        _append_console_log(server.id, "[MSM] Modliste: kein install_dir gesetzt\n")
        return
    if not relative_path or relative_path.startswith("/") or "\x00" in relative_path:
        _append_console_log(server.id, "[MSM] Modliste: unsicherer Pfad abgelehnt\n")
        return
    if any(part == ".." for part in relative_path.split("/")):
        _append_console_log(server.id, "[MSM] Modliste: Pfad enthaelt '..'\n")
        return

    try:
        install_real = os.path.realpath(install_dir)
        target = os.path.realpath(os.path.join(install_real, relative_path))
    except OSError as exc:
        _append_console_log(server.id, f"[MSM] Modliste: realpath fehlgeschlagen: {exc}\n")
        return

    rel = os.path.relpath(target, install_real)
    if rel.startswith("..") or os.path.isabs(rel):
        _append_console_log(
            server.id,
            f"[MSM] Modliste: Pfad {relative_path} verlaesst install_dir — abgelehnt\n",
        )
        return

    target_dir = os.path.dirname(target)
    try:
        os.makedirs(target_dir, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(f"{line}\n")
    except OSError as exc:
        _append_console_log(server.id, f"[MSM] Modliste: Schreiben fehlgeschlagen: {exc}\n")


# ── Plugin-Basis ───────────────────────────────────────────────────────────


class GamePlugin(ABC):
    """Basisklasse für Game-Plugins.

    Pflichtfelder:
      - game_id, game_name, supports_mods, supports_steam_workshop
      - docker_image: das Base-Image, in dem der Server läuft
      - container_needs_tmpfs: wenn True, wird /tmp als tmpfs hinzugefügt

    Pflichtmethoden:
      - build_container_command, build_container_env, build_port_publishes,
        get_config_schema, get_config_files, get_logs
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

    def container_uid_gid(self, server) -> tuple[int, int]:
        return docker_service.container_runtime_uid_gid()

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

    # ── Zentrale Updater-Hooks (KISS, Blueprint-getrieben) ───────────────────

    def prepare_for_updates(self, server) -> None:
        """
        Hook: wird vor Neustarts / Installs aufgerufen.

        Zentrale Stelle für passive oder vorbereitende Update-Checks
        (Workshop-Mods + Server-Dateien). Default ist No-Op.

        Wird vom neuen `games.updater` Modul bedient.
        Keine Netzwerk-Calls oder schwere Arbeit hier – nur Delegation.
        """
        return None

    def check_for_mod_updates(self, server) -> list[dict]:
        """
        Liefert Liste von Workshop-Mods, die installiert oder aktualisiert
        werden sollten (siehe games.updater.check_workshop_mod_updates).

        Wird nach Server-Neustart und im Background-Scheduler genutzt.
        """
        try:
            bp = self.get_blueprint()
            if bp is None:
                return []
            return updater.check_workshop_mod_updates(server, bp)
        except Exception as exc:  # pragma: no cover - defensiv
            logger.warning("Mod-Update-Check fehlgeschlagen für Server %s: %s", getattr(server, "id", "?"), exc)
            return []

    def check_for_server_file_update(self, server) -> dict:
        """
        Prüft, ob Server-Dateien (Game-Binaries) aktualisiert werden sollten.
        Wird **ausschließlich** vor Neustarts ausgeführt (nie zur Laufzeit).
        """
        try:
            bp = self.get_blueprint()
            if bp is None:
                return {"action": "none", "reason": "no_blueprint"}
            return updater.check_server_file_update(server, bp)
        except Exception as exc:  # pragma: no cover - defensiv
            logger.warning("Serverfile-Update-Check fehlgeschlagen für Server %s: %s", getattr(server, "id", "?"), exc)
            return {"action": "none", "reason": "error"}

    def perform_server_file_update(self, server) -> dict:
        """
        Führt ein Server-Datei-Update (Game-Binaries) **synchron** aus.

        Wird **ausschließlich** im Restart-Pfad (routers/servers.py) aufgerufen,
        wenn check_for_server_file_update() ein Update meldet.

        Garantiert:
        - Update läuft VOR plugin.start() (Container-Start)
        - cache_manual_configs wird vor dem Update aufgerufen
        - restore_manual_configs wird nach dem Update (oder Fehlerfall) aufgerufen
        - Blockiert den Aufrufer bis Abschluss (Caller muss to_thread nutzen!)

        Implementierung: Delegation an games.updater.apply_server_file_update
        (KISS: keine Duplizierung der Source-Logik, nutzt bestehende
        run_steamcmd_install + install_http_source).

        Native Plugins (DayZ, Conan) profitieren automatisch über get_blueprint().

        Sicherheit (AGENTS.md):
        - Diese Methode fängt Fehler intern ab (siehe apply_).
        - Restart darf unter KEINEN Umständen durch Server-Datei-Update scheitern.
        - Keine Mod-Logik, kein Background, keine E-Mails.

        Rückgabe: {"ok": bool, ...} — siehe apply_server_file_update.
        """
        try:
            bp = self.get_blueprint()
            if bp is None:
                return {"ok": False, "error": "no_blueprint"}
            # Delegation (Lazy nicht nötig, da "updater" bereits auf Modulebene importiert ist)
            return updater.apply_server_file_update(server, bp)
        except Exception as exc:  # pragma: no cover - defensiv, Restart-Sicherheit
            logger.warning("perform_server_file_update fehlgeschlagen für Server %s: %s", getattr(server, "id", "?"), exc)
            # Niemals Exception nach oben werfen — Restart muss weiterlaufen
            return {"ok": False, "error": str(exc)}

    def perform_workshop_mod_updates(self, server) -> dict:
        """
        Führt erkannte Workshop-Mod-Updates und -Neuinstallationen **synchron** aus.

        Aufruf: ausschließlich aus Restart-Pfad (routers/servers.py) und optional
        direktem Start-Pfad, nachdem check_for_mod_updates() Handlungsbedarf gemeldet hat.

        Verwendete bestehende Funktionen (genau wie gefordert):
        - self.install_mod(server, workshop_id): Game-spezifischer Einstieg (Plugins
          überschreiben für Custom-Pfade etc.). Ruft intern run_steamcmd_workshop_download
          (siehe base.py:306) auf, um den eigentlichen SteamCMD-Workshop-Download
          durchzuführen (mit Login-Handling, Logging, chown etc.).
        - updater.update_mod_metadata_after_success(...): Setzt nach Erfolg
          korrekt Mod.last_updated (remote time_updated) + Mod.installed_version.

        Ablauf pro Mod:
        1. Console-Log (Transparenz).
        2. install_mod aufrufen (blockierend, kann lange dauern).
        3. Bei Erfolg (kein "error" im Result): Metadaten-Update in DB.
        4. Am Ende: update_modlist(server) für Blueprints mit FILE-Injection.

        KISS + AGENTS.md:
        - Keine neuen Abstraktionen, keine Pipelines, keine Manager-Klassen.
        - Symmetrisch zu perform_server_file_update.
        - Fehler werden nur geloggt + in Console; der Restart/Start läuft IMMER weiter
          (sicheres Verhalten, keine harten Abhängigkeiten).
        - Nur aktive Mods (enabled=True) werden betrachtet (Check filtert).
        - Blockierend → Caller (Router) muss asyncio.to_thread verwenden.
        - Deutsche Kommentare, minimale Code-Änderung.

        Rückgabe: {"ok": bool, "applied": int, "errors": list, "message": str}
        """
        try:
            bp = self.get_blueprint()
            if bp is None:
                return {"ok": False, "error": "no_blueprint"}

            needed = self.check_for_mod_updates(server)
            if not needed:
                return {"ok": True, "applied": 0, "message": "keine Workshop-Mod-Updates nötig"}

            _append_console_log(
                server.id,
                f"[MSM] Starte Workshop-Mod-Updates/Installationen für {len(needed)} Mod(s) "
                "(via install_mod + internes run_steamcmd_workshop_download vor Container-Start)...\n"
            )

            applied = 0
            errors: list[str] = []

            for u in needed:
                wid = u.get("workshop_id", "")
                name = u.get("name", wid)
                action = u.get("action", "update")
                _append_console_log(
                    server.id,
                    f"[MSM]   → {action.upper()}: Workshop-Mod {wid} ({name})\n"
                )
                try:
                    mod_res = self.install_mod(server, wid)
                    success = (
                        isinstance(mod_res, dict)
                        and mod_res.get("ok", True) is not False
                        and "error" not in mod_res
                    )
                    if success:
                        # Erfolg: Metadaten sofort in DB schreiben (last_updated + installed_version)
                        updater.update_mod_metadata_after_success(
                            server.id, wid, u.get("remote_updated")
                        )
                        applied += 1
                        _append_console_log(
                            server.id, f"[MSM]     ✓ {wid} erfolgreich verarbeitet.\n"
                        )
                    else:
                        err = (
                            (mod_res or {}).get("error", "unbekannter Fehler")
                            if isinstance(mod_res, dict)
                            else str(mod_res)
                        )
                        errors.append(f"{wid}: {err}")
                        _append_console_log(
                            server.id, f"[MSM]     ✗ {wid} fehlgeschlagen: {err}\n"
                        )
                except Exception as exc:  # pragma: no cover - defensiv
                    errors.append(f"{wid}: {exc}")
                    _append_console_log(
                        server.id, f"[MSM]     ✗ {wid} Exception während install_mod: {exc}\n"
                    )

            # Mod-Liste für Injection aktualisieren (z. B. modlist.txt) – nach allen Downloads
            try:
                self.update_modlist(server)
            except Exception as exc:
                _append_console_log(
                    server.id,
                    f"[MSM] update_modlist nach Workshop-Mod-Updates fehlgeschlagen (nicht kritisch): {exc}\n",
                )

            ok = len(errors) == 0
            summary = f"{applied} Workshop-Mod(s) erfolgreich installiert/aktualisiert"
            if errors:
                summary += f" ({len(errors)} Fehler)"
            _append_console_log(
                server.id, f"[MSM] Workshop-Mod-Updates abgeschlossen: {summary}.\n"
            )

            return {
                "ok": ok,
                "applied": applied,
                "errors": errors,
                "message": summary,
            }
        except Exception as exc:  # pragma: no cover - Restart/Start-Sicherheit (AGENTS.md)
            logger.warning(
                "perform_workshop_mod_updates fehlgeschlagen für Server %s: %s",
                getattr(server, "id", "?"),
                exc,
            )
            sid = getattr(server, "id", 0)
            _append_console_log(
                sid,
                f"[MSM] Workshop-Mod-Update fehlgeschlagen — Restart/Start wird trotzdem fortgesetzt: {exc}\n",
            )
            return {"ok": False, "error": str(exc), "applied": 0}

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

        uid, gid = self.container_uid_gid(server)
        run_user = f"{uid}:{gid}"
        name = container_name_for(server.id)
        volume_binds = self.build_volume_binds(server)

        for volume in volume_binds:
            if volume.read_only:
                continue
            repair = docker_service.repair_bind_mount_permissions(
                volume.host_path,
                container_path=volume.container_path,
                owner_uid_gid=(uid, gid),
            )
            if not repair.get("ok"):
                err = repair.get("error") or "Berechtigungen konnten nicht vorbereitet werden"
                _append_console_log(server.id, f"[MSM] Permission-Repair fehlgeschlagen: {err}\n")
                return {"error": err}

        result = docker_service.run_container(
            name=name,
            image=self.docker_image,
            command=self.build_container_command(server),
            env=self.build_container_env(server),
            ports=port_publishes,
            volumes=volume_binds,
            cpu_limit_percent=server.cpu_limit_percent,
            ram_limit_mb=server.ram_limit_mb,
            user=run_user,
            workdir=self.container_workdir(server),
            read_only_rootfs=self.container_read_only_rootfs,
            tmpfs_paths=self.container_tmpfs_paths(server),
        )
        if not result["ok"]:
            _append_console_log(server.id, f"[MSM] Container-Start fehlgeschlagen: {result['error']}\n")
            return {"error": result["error"]}

        _append_console_log(server.id, f"[MSM] Container {name} gestartet\n")

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

    # ─ Config ────────────────────────────────────────────────────────────

    @abstractmethod
    def get_config_schema(self) -> list[ConfigField]:
        ...

    @abstractmethod
    def get_config_files(self) -> list[dict]:
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

    # ─ Blueprint-Hook ────────────────────────────────────────────────────

    def get_blueprint(self) -> Blueprint | None:
        """Liefert die Blueprint, aus der dieses Plugin seine Metadaten zieht.

        Native Plugins ueberschreiben dies und laden ihre
        ``backend/blueprints/native/<id>.blueprint.json``. Plugins ohne
        Blueprint (z. B. rein generischer ``BlueprintPlugin``-Wrapper laesst
        ``self._blueprint`` setzen) muessen dies konsistent ausliefern.
        """
        return None

    def format_modlist_lines(self, server, mods: list) -> list[str]:
        """Wandelt aktive Mod-Rows in eine Liste von Zeilen fuer ``modlist.txt``.

        Default: eine Workshop-ID pro Zeile. Plugins mit game-spezifischem
        Datei-Format (z. B. Conan, das pak-Dateinamen erwartet) ueberschreiben.
        """
        return [m.workshop_id for m in mods]

    def update_modlist(self, server) -> None:
        """Schreibt die Workshop-Modliste, falls die Blueprint ``file``-Injection nutzt.

        Sicherheits-Invariante: Der finale Pfad muss innerhalb des
        ``install_dir`` des Servers liegen. Symlink-Escape per ``realpath``
        ausgeschlossen.
        """
        blueprint = self.get_blueprint()
        if blueprint is None:
            return
        bp_mods = blueprint.effective_mods()
        if not bp_mods.supportsSteamWorkshop:
            return
        if bp_mods.modInjection != BlueprintModInjection.FILE:
            return
        rel_path = bp_mods.modListFilePath or ""
        if not rel_path:
            return
        write_workshop_modlist(server, rel_path, self.format_modlist_lines(server, _query_active_mods(server.id)))
