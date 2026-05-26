"""DayZ — Docker-basiertes Plugin, getrieben durch ``native/dayz.blueprint.json``.

Lifecycle laeuft komplett ueber GamePlugin-Defaults (docker_service). Spezifisch
sind: Mod-Symlink-Logik, Workshop-Pfade. Image/Startup/Mod-Injection kommen aus
der Blueprint, damit Verhalten und Doku konsistent bleiben.
"""

from __future__ import annotations

import glob
import os
import threading

from blueprints import Blueprint, render_argv
from blueprints.registry import native_dir
from blueprints.schema import BlueprintSourceType, load_blueprint_file
from games.base import (
    CONTAINER_DATA_DIR,
    ConfigField,
    GamePlugin,
    VolumeBind,
    _append_console_log,
    active_mod_ids,
    finish_install,
    run_steamcmd_install,
    run_steamcmd_workshop_download,
)


_BLUEPRINT_PATH = native_dir() / "dayz.blueprint.json"


class DayZPlugin(GamePlugin):
    """DayZ Linux Dedicated Server — Linux native, in Docker.

    Offizielle Doku: https://community.bohemia.net/wiki/DayZ:Hosting_a_Linux_Server
    App ID: 223350 (Server), Workshop App ID: 221100 (Mods).
    """

    game_id = "dayz"
    game_name = "DayZ"
    supports_mods = True
    supports_steam_workshop = True

    # Reine Convenience-Konstanten — gezogen aus der Blueprint, bewusst hier
    # noch verfuegbar fuer Workshop-Pfad-Berechnungen.
    APP_ID = "223350"
    WORKSHOP_ID = "221100"

    docker_image = "cm2network/steamcmd:root"
    container_needs_tmpfs = False
    container_read_only_rootfs = False

    def __init__(self) -> None:
        self._blueprint: Blueprint = load_blueprint_file(_BLUEPRINT_PATH)
        # Image & Workshop-IDs aus der Blueprint synchronisieren, damit die
        # Klassen-Attribute auch fuer aeltere Aufrufer (z. B. Tests) stimmen.
        self.docker_image = self._blueprint.runtime.image
        self.APP_ID = self._blueprint.source.steam.appId  # type: ignore[union-attr]
        bp_mods = self._blueprint.effective_mods()
        if bp_mods.workshopAppId:
            self.WORKSHOP_ID = bp_mods.workshopAppId

    def get_blueprint(self) -> Blueprint:
        return self._blueprint

    # ─ Setup ─────────────────────────────────────────────────────────────

    def install(self, server) -> dict:
        server_id = server.id
        install_dir = server.install_dir
        app_id = self.APP_ID

        requires_login = False
        if self._blueprint.source.type == BlueprintSourceType.STEAM and self._blueprint.source.steam:
            requires_login = self._blueprint.source.steam.requiresLogin

        def _install():
            result = run_steamcmd_install(
                server_id=server_id,
                install_dir=install_dir,
                app_id=app_id,
                use_authenticated_login=requires_login,
            )
            finish_install(server_id, result)

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        return {"message": "Installation gestartet"}

    # ─ Container-Config ──────────────────────────────────────────────────

    def build_container_command(self, server) -> list[str]:
        return render_argv(
            self._blueprint,
            install_dir=CONTAINER_DATA_DIR,
            ports={
                "game": server.game_port,
                "query": server.query_port,
                "rcon": server.rcon_port,
            },
            active_mod_ids=active_mod_ids(server),
        )

    # build_port_publishes erbt vom Base-Plugin (Phase 2):
    # game/query UDP + rcon TCP an public_bind_ip. Kein 0.0.0.0 erlaubt.

    def build_volume_binds(self, server) -> list[VolumeBind]:
        return [VolumeBind(server.install_dir, CONTAINER_DATA_DIR, read_only=False)]

    # ─ Logs ──────────────────────────────────────────────────────────────

    def get_logs(self, server, lines: int = 100) -> str:
        # DayZ schreibt rotierende Logs unter log/ und log_*.txt
        candidates = [
            os.path.join(server.install_dir, "log", "script_1.log"),
            os.path.join(server.install_dir, "log_1.txt"),
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        all_lines = f.readlines()
                    return "".join(all_lines[-lines:])
                except Exception:
                    continue
        return ""

    # ─ Config-Schema ─────────────────────────────────────────────────────

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField("hostname", "Server-Name", "text", default="DayZ Server", required=True),
            ConfigField("password", "Server-Passwort", "text", default=""),
            ConfigField("passwordAdmin", "Admin-Passwort", "text", default="", required=True),
            ConfigField("maxPlayers", "Max. Spieler", "number", default=60),
            ConfigField("serverTime", "Server-Zeit", "text", default="8:00"),
            ConfigField("serverTimeAcceleration", "Zeit-Beschleunigung", "number", default=1),
            ConfigField("serverNightTimeAcceleration", "Nacht-Beschleunigung", "number", default=1),
            ConfigField("disablePersonalLight", "Persönliches Licht aus", "bool", default=False),
            ConfigField("weather", "Wetter", "text", default=""),
            ConfigField("lightning", "Blitz-Ereignisse", "bool", default=False),
            ConfigField("maxPing", "Max. Ping", "number", default=200),
            ConfigField("timeStampFormat", "Zeitstempel-Format", "text", default="0"),
            ConfigField("logAverageFps", "FPS loggen", "bool", default=False),
            ConfigField("logMemory", "Speicher loggen", "bool", default=False),
            ConfigField("logPlayers", "Spieler loggen", "bool", default=False),
            ConfigField("logFile", "Log-Datei", "text", default="server.log"),
        ]

    def get_mod_support(self) -> dict | None:
        return {
            "workshop_id": self.WORKSHOP_ID,
            "dependency_resolution": False,
            "required_tags": [],
        }

    def get_config_files(self) -> list[dict]:
        return [
            {"name": "serverDZ.cfg", "path": "serverDZ.cfg"},
            {"name": "cfgplayerspawn.xml", "path": "cfgplayerspawn.xml"},
            {"name": "cfgeconomy.xml", "path": "cfgeconomy.xml"},
            {"name": "cfgspawnabletypes.xml", "path": "cfgspawnabletypes.xml"},
            {"name": "cfgweather.xml", "path": "cfgweather.xml"},
            {"name": "cfglimitsdefinition.xml", "path": "cfglimitsdefinition.xml"},
            {"name": "cfgpointsofinterest.xml", "path": "cfgpointsofinterest.xml"},
        ]

    def get_backup_paths(self, server) -> list[str]:
        return [
            os.path.join(server.install_dir, "mpmissions"),
            os.path.join(server.install_dir, "profile"),
            os.path.join(server.install_dir, "storage"),
        ]

    # ─ Mods ──────────────────────────────────────────────────────────────

    def install_mod(self, server, workshop_id: str) -> dict:
        """DayZ mod install: SteamCMD download → symlink to server root → copy keys.

        Symlink-Erstellung haengt am game-spezifischen Layout (``keys/*.bikey``)
        und bleibt deshalb in Python. Die Modliste wird hingegen ueber die
        Blueprint im Renderer in die Startargumente injiziert — kein extra File.
        """
        requires_login = False
        if self._blueprint.source.type == BlueprintSourceType.STEAM and self._blueprint.source.steam:
            requires_login = self._blueprint.source.steam.requiresLogin

        def _install():
            install_dir = server.install_dir
            workshop_dir = os.path.join(
                install_dir, "steamapps", "workshop", "content", self.WORKSHOP_ID, workshop_id
            )

            run_steamcmd_workshop_download(
                server_id=server.id,
                install_dir=install_dir,
                workshop_app_id=self.WORKSHOP_ID,
                workshop_item_id=workshop_id,
                use_authenticated_login=requires_login,
            )

            link_path = os.path.join(install_dir, workshop_id)
            if os.path.isdir(workshop_dir):
                if os.path.exists(link_path):
                    if os.path.islink(link_path):
                        os.unlink(link_path)
                    else:
                        _append_console_log(
                            server.id,
                            f"[MSM] Warnung: {link_path} existiert bereits und ist kein Symlink\n",
                        )
                        return
                os.symlink(workshop_dir, link_path)
                _append_console_log(
                    server.id, f"[MSM] Mod {workshop_id} verlinkt: {link_path} → {workshop_dir}\n"
                )

                # Bikey-Files in keys/ verlinken
                keys_dir = os.path.join(install_dir, "keys")
                os.makedirs(keys_dir, exist_ok=True)
                mod_keys = os.path.join(workshop_dir, "keys")
                if os.path.isdir(mod_keys):
                    for key_file in glob.glob(os.path.join(mod_keys, "*.bikey")):
                        key_link = os.path.join(keys_dir, os.path.basename(key_file))
                        if os.path.exists(key_link):
                            os.unlink(key_link)
                        os.symlink(key_file, key_link)
                        _append_console_log(
                            server.id, f"[MSM] Key verlinkt: {os.path.basename(key_file)}\n"
                        )
            else:
                _append_console_log(
                    server.id, f"[MSM] Warnung: Workshop-Verzeichnis nicht gefunden: {workshop_dir}\n"
                )

            _append_console_log(server.id, f"[MSM] Mod {workshop_id} Installation abgeschlossen.\n")

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        return {"message": f"Mod {workshop_id} wird installiert"}
