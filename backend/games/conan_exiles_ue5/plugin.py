"""Conan Exiles (UE5 Enhanced) — Docker-basiertes Plugin.

Image/Startup/Mod-Injection kommen aus ``native/conan_exiles_ue5.blueprint.json``.
Game-spezifisch bleiben: SteamCMD-Args, Workshop-Pfade, .pak-Kopier-Logik,
modlist.txt-Generierung (pak-Dateinamen statt Workshop-IDs).
"""

from __future__ import annotations

import glob
import os
import shutil
import threading

from blueprints import Blueprint, render_argv
from blueprints.registry import native_dir
from blueprints.schema import load_blueprint_file
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
from games.ini_utils import set_ini_value


_BLUEPRINT_PATH = native_dir() / "conan_exiles_ue5.blueprint.json"


class ConanExilesUE5Plugin(GamePlugin):
    """Conan Exiles Enhanced (UE5) Dedicated Server — Linux native, in Docker.

    Offizielle Doku: https://exiles-enhanced.inflexion.io/servers/linux/
    App ID: 443030 (Server), Workshop App ID: 440900 (Mods).
    """

    game_id = "conan_exiles_ue5"
    game_name = "Conan Exiles (UE5)"
    supports_mods = True
    supports_steam_workshop = True

    APP_ID = "443030"
    WORKSHOP_ID = "440900"

    docker_image = "cm2network/steamcmd:root"
    container_needs_tmpfs = False
    container_read_only_rootfs = False

    def __init__(self) -> None:
        self._blueprint: Blueprint = load_blueprint_file(_BLUEPRINT_PATH)
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

        def _install():
            # Reinstall-Schutz für manuelle Configs: Cache + Restore via zentrale
            # perform_install_with_protection (verhindert Überschreiben von .ini/.cfg etc.).
            from games.updater import perform_install_with_protection
            result = perform_install_with_protection(
                server,
                lambda: run_steamcmd_install(
                    server_id=server_id,
                    install_dir=install_dir,
                    app_id=app_id,
                ),
                blueprint=self._blueprint,
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

    def prepare_runtime(self, server) -> None:
        """Schreibt Ports + RCON-Flag in Engine.ini/Game.ini.

        Conan ignoriert CLI-`-Port=`/`-QueryPort=`-Werte teilweise und liest
        stattdessen aus den INIs. Wir setzen die Werte aus dem MSM-Port-Modell
        bei jedem Start neu — User-Edits via File-Manager an anderen Keys
        bleiben unverändert (zeilen-orientierter Setter).

        Vgl. Pterodactyl-Egg-Startscript (Conan Enhanced UE5): identische
        Mapping-Logik.
        """
        config_dir = os.path.join(
            server.install_dir, "ConanSandbox", "Saved", "Config", "LinuxServer"
        )
        engine_ini = os.path.join(config_dir, "Engine.ini")
        game_ini = os.path.join(config_dir, "Game.ini")

        if server.game_port:
            set_ini_value(engine_ini, "URL", "Port", str(server.game_port))
        if server.query_port:
            set_ini_value(
                engine_ini, "OnlineSubsystemNull", "GameServerQueryPort", str(server.query_port)
            )
        if server.rcon_port:
            set_ini_value(game_ini, "RconPlugin", "RconPort", str(server.rcon_port))
            set_ini_value(game_ini, "RconPlugin", "RconEnabled", "True")

    # build_port_publishes erbt vom Base-Plugin (Phase 2):
    # game/query UDP + rcon TCP an public_bind_ip. Kein 0.0.0.0 erlaubt.

    def build_volume_binds(self, server) -> list[VolumeBind]:
        return [VolumeBind(server.install_dir, CONTAINER_DATA_DIR, read_only=False)]

    # ─ Logs ──────────────────────────────────────────────────────────────

    def get_logs(self, server, lines: int = 100) -> str:
        log_path = os.path.join(
            server.install_dir, "ConanSandbox", "Saved", "Logs", "ConanSandbox.log"
        )
        if not os.path.exists(log_path):
            return ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
            return "".join(all_lines[-lines:])
        except Exception:
            return ""

    # ─ Config-Schema ─────────────────────────────────────────────────────

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField("MaxNumbPlayers", "Max. Spieler", "number", default=40, description="Maximale Spieleranzahl"),
            ConfigField("ServerPassword", "Server-Passwort", "text", default="", description="Leer = kein Passwort"),
            ConfigField("AdminPassword", "Admin-Passwort", "text", default="", required=True),
            ConfigField("serverVoiceChat", "Voice Chat", "bool", default=True),
            ConfigField("serverCommunity", "Community", "number", default=0, description="0=none, 1=filtering, 2=PvE, 3=RP, 4=PvP"),
            ConfigField("PvPBlitzServer", "PvP Blitz", "bool", default=False),
            ConfigField("NetServerMaxTickRate", "Tick Rate", "number", default=30),
            ConfigField("MaxTransferDistance", "Max Transfer Distance", "number", default=100000),
        ]

    def get_mod_support(self) -> dict | None:
        """Conan Exiles UE5: Filtert nach 'Enhanced'-Tag (UE4-Mods teilen Workshop)."""
        return {
            "workshop_id": self.WORKSHOP_ID,
            "dependency_resolution": False,
            "required_tags": ["Enhanced"],
        }

    def get_config_files(self) -> list[dict]:
        return [
            {"name": "Engine.ini", "path": "ConanSandbox/Saved/Config/LinuxServer/Engine.ini"},
            {"name": "Game.ini", "path": "ConanSandbox/Saved/Config/LinuxServer/Game.ini"},
            {"name": "ServerSettings.ini", "path": "ConanSandbox/Saved/Config/LinuxServer/ServerSettings.ini"},
        ]

    def get_backup_paths(self, server) -> list[str]:
        return [
            os.path.join(server.install_dir, "ConanSandbox", "Saved"),
        ]

    # ─ Mods ──────────────────────────────────────────────────────────────

    def install_mod(self, server, workshop_id: str) -> dict:
        """Lädt Mod via SteamCMD-Container, kopiert .pak nach Mods/, aktualisiert modlist.txt."""
        result: dict = {}

        def _install():
            nonlocal result
            try:
                install_dir = server.install_dir
                workshop_dir = os.path.join(
                    install_dir, "steamapps", "workshop", "content", self.WORKSHOP_ID, workshop_id
                )
                mods_dir = os.path.join(install_dir, "ConanSandbox", "Mods")

                download_res = run_steamcmd_workshop_download(
                    server_id=server.id,
                    install_dir=install_dir,
                    workshop_app_id=self.WORKSHOP_ID,
                    workshop_item_id=workshop_id,
                )
                if not download_res.get("ok", False):
                    result = {"error": download_res.get("error", "Workshop-Download fehlgeschlagen")}
                    return

                os.makedirs(mods_dir, exist_ok=True)
                pak_files = glob.glob(os.path.join(workshop_dir, "**", "*.pak"), recursive=True)

                if not pak_files:
                    _append_console_log(
                        server.id, f"[MSM] Warnung: Keine .pak-Dateien für Mod {workshop_id} gefunden\n"
                    )
                    result = {"error": "Keine .pak-Dateien gefunden"}
                    return

                for pak_path in pak_files:
                    pak_name = os.path.basename(pak_path)
                    dest = os.path.join(mods_dir, pak_name)
                    shutil.copy2(pak_path, dest)
                    _append_console_log(server.id, f"[MSM] Mod-Datei kopiert: {pak_name}\n")

                self.update_modlist(server)
                _append_console_log(server.id, f"[MSM] Mod {workshop_id} Installation abgeschlossen.\n")
            except Exception as exc:
                result = {"error": str(exc)}

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        thread.join()
        return result

    # ─ Modlist (Blueprint: modInjection=file) ─────────────────────────────

    def format_modlist_lines(self, server, mods: list) -> list[str]:
        """Conan-spezifisches Format: jede Zeile ist ein im Mods/-Verzeichnis
        vorhandener .pak-Dateiname. Wir scannen die Workshop-Inhalte je Mod
        und nehmen nur paks, die wir tatsaechlich nach ``ConanSandbox/Mods``
        kopiert haben (Vermeidung von Phantom-Eintraegen).
        """
        install_dir = server.install_dir
        mods_dir = os.path.join(install_dir, "ConanSandbox", "Mods")
        lines: list[str] = []
        for mod in mods:
            workshop_dir = os.path.join(
                install_dir,
                "steamapps",
                "workshop",
                "content",
                self.WORKSHOP_ID,
                mod.workshop_id,
            )
            pak_files = glob.glob(os.path.join(workshop_dir, "**", "*.pak"), recursive=True)
            for pak_path in pak_files:
                pak_name = os.path.basename(pak_path)
                if os.path.exists(os.path.join(mods_dir, pak_name)):
                    lines.append(pak_name)
        return lines

    # Alter Name (Backward-Compat fuer evtl. externe Aufrufer); delegiert auf
    # den blueprintbasierten Helfer.
    def _update_modlist(self, server) -> None:
        self.update_modlist(server)
