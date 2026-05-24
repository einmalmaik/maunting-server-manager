"""Conan Exiles (UE5 Enhanced) — Docker-basiertes Plugin.

Lifecycle läuft komplett über GamePlugin-Defaults (docker_service). Spezifisch
sind: SteamCMD-Args, Workshop-Pfade, .pak-Kopier-Logik, modlist.txt-Generierung.
"""

from __future__ import annotations

import glob
import os
import shutil
import threading

from games.base import (
    CONTAINER_DATA_DIR,
    ConfigField,
    GamePlugin,
    PortPublish,
    VolumeBind,
    _append_console_log,
    finish_install,
    run_steamcmd_install,
    run_steamcmd_workshop_download,
)


class ConanExilesUE5Plugin(GamePlugin):
    """Conan Exiles Enhanced (UE5) Dedicated Server — Linux native, in Docker.

    Offizielle Doku: https://exiles-enhanced.inflexion.io/servers/linux/
    App ID: 443030 (Server), Workshop App ID: 440900 (Mods).
    """

    game_id = "conan_exiles_ue5"
    game_name = "Conan Exiles (UE5)"
    supports_mods = True

    APP_ID = "443030"
    WORKSHOP_ID = "440900"

    # SteamCMD-Image enthält bereits glibc + Steam-Runtime; Conan-Binaries
    # laufen im selben Image, weil sie nur die Steam-Runtime brauchen.
    docker_image = "cm2network/steamcmd:root"
    container_needs_tmpfs = False  # UE legt mehrere temporäre Verzeichnisse in CWD an
    container_read_only_rootfs = False

    # ─ Setup ─────────────────────────────────────────────────────────────

    def install(self, server) -> dict:
        server_id = server.id
        install_dir = server.install_dir
        app_id = self.APP_ID

        def _install():
            result = run_steamcmd_install(
                server_id=server_id,
                install_dir=install_dir,
                app_id=app_id,
            )
            # Status zurücksetzen — sonst bleibt der Server für immer auf "installing"
            finish_install(server_id, result)

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        return {"message": "Installation gestartet"}

    # ─ Container-Config ──────────────────────────────────────────────────

    def _container_executable(self, server) -> str:
        """Pfad zum Conan-Start-Script INNERHALB des Containers."""
        return f"{CONTAINER_DATA_DIR}/ConanSandboxServer.sh"

    def build_container_command(self, server) -> list[str]:
        cmd = ["/bin/bash", self._container_executable(server), "-log"]
        if server.game_port:
            cmd.append(f"-Port={server.game_port}")
        if server.query_port:
            cmd.append(f"-QueryPort={server.query_port}")
        if server.rcon_port:
            cmd.append(f"-RconPort={server.rcon_port}")
        return cmd

    def build_port_publishes(self, server) -> list[PortPublish]:
        ports: list[PortPublish] = []
        host_ip = getattr(server, "public_bind_ip", None) or None
        if server.game_port:
            ports.append(PortPublish(server.game_port, server.game_port, "udp", host_ip))
        if server.query_port:
            ports.append(PortPublish(server.query_port, server.query_port, "udp", host_ip))
        if server.rcon_port:
            ports.append(PortPublish(server.rcon_port, server.rcon_port, "tcp", host_ip))
        return ports

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
        def _install():
            install_dir = server.install_dir
            workshop_dir = os.path.join(
                install_dir, "steamapps", "workshop", "content", self.WORKSHOP_ID, workshop_id
            )
            mods_dir = os.path.join(install_dir, "ConanSandbox", "Mods")

            run_steamcmd_workshop_download(
                server_id=server.id,
                install_dir=install_dir,
                workshop_app_id=self.WORKSHOP_ID,
                workshop_item_id=workshop_id,
            )

            os.makedirs(mods_dir, exist_ok=True)
            pak_files = glob.glob(os.path.join(workshop_dir, "**", "*.pak"), recursive=True)

            if not pak_files:
                _append_console_log(
                    server.id, f"[MSM] Warnung: Keine .pak-Dateien für Mod {workshop_id} gefunden\n"
                )
                return

            for pak_path in pak_files:
                pak_name = os.path.basename(pak_path)
                dest = os.path.join(mods_dir, pak_name)
                shutil.copy2(pak_path, dest)
                _append_console_log(server.id, f"[MSM] Mod-Datei kopiert: {pak_name}\n")

            self._update_modlist(server)
            _append_console_log(server.id, f"[MSM] Mod {workshop_id} Installation abgeschlossen.\n")

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        return {"message": f"Mod {workshop_id} wird installiert"}

    def _update_modlist(self, server) -> None:
        """Rebuildet modlist.txt aus aktivierten Mods in Lade-Reihenfolge."""
        install_dir = server.install_dir
        mods_dir = os.path.join(install_dir, "ConanSandbox", "Mods")
        modlist_path = os.path.join(mods_dir, "modlist.txt")
        os.makedirs(mods_dir, exist_ok=True)

        try:
            from database import SessionLocal
            from models import Mod
            db = SessionLocal()
            try:
                mods = (
                    db.query(Mod)
                    .filter(Mod.server_id == server.id, Mod.enabled == True)  # noqa: E712
                    .order_by(Mod.load_order.asc())
                    .all()
                )
            finally:
                db.close()
        except Exception:
            return

        lines: list[str] = []
        for mod in mods:
            workshop_dir = os.path.join(
                install_dir, "steamapps", "workshop", "content", self.WORKSHOP_ID, mod.workshop_id
            )
            pak_files = glob.glob(os.path.join(workshop_dir, "**", "*.pak"), recursive=True)
            for pak_path in pak_files:
                pak_name = os.path.basename(pak_path)
                if os.path.exists(os.path.join(mods_dir, pak_name)):
                    lines.append(pak_name)

        try:
            with open(modlist_path, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(f"{line}\n")
        except OSError as e:
            _append_console_log(server.id, f"[MSM] Fehler beim Schreiben der modlist.txt: {e}\n")
