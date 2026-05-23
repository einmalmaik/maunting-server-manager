import os
import subprocess
import threading

from config import settings
from games.base import GamePlugin, ServerStatus, ConfigField, build_systemd_unit, _run_install_with_logging


class ConanExilesUE5Plugin(GamePlugin):
    """Conan Exiles Enhanced (UE5) Dedicated Server Plugin — Linux native.

    Offizielle Doku: https://exiles-enhanced.inflexion.io/servers/linux/
    App ID: 443030. Es wird ausschließlich die native Linux-Version genutzt.
    Wine/UE4-Fallback ist nicht vorgesehen (dazu wäre ein separates Plugin nötig).
    """

    game_id = "conan_exiles_ue5"
    game_name = "Conan Exiles (UE5)"
    supports_mods = True

    APP_ID = "443030"
    WORKSHOP_ID = "440900"

    def _resolve_executable(self, server) -> str | None:
        """Nur native Linux-Binaries — kein Wine-Fallback."""
        candidates = [
            os.path.join(server.install_dir, "ConanSandboxServer.sh"),
            os.path.join(server.install_dir, "ConanSandboxServer"),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _build_exec_start(self, server, exe: str) -> str:
        """Build the ExecStart line für systemd (nur native Linux).
        Fügt Port-Parameter hinzu (Game, Query, RCon)."""
        args = "-log"
        if server.game_port:
            args += f" -Port={server.game_port}"
        if server.query_port:
            args += f" -QueryPort={server.query_port}"
        if server.rcon_port:
            args += f" -RconPort={server.rcon_port}"

        if exe.endswith(".sh"):
            return f"/bin/bash {exe} {args}"
        return f"{exe} {args}"

    def install(self, server) -> dict:
        cmd = [
            settings.steamcmd_path,
            "+force_install_dir", server.install_dir,
            "+login", "anonymous",
            "+app_update", self.APP_ID,
            "+quit",
        ]
        thread = threading.Thread(
            target=_run_install_with_logging,
            args=(cmd, server.id, server.install_dir),
            daemon=True,
        )
        thread.start()
        return {"message": "Installation gestartet"}

    def update(self, server) -> dict:
        return self.install(server)

    def start(self, server) -> dict:
        exe = self._resolve_executable(server)
        if not exe:
            return {"error": "Server-Executable nicht gefunden. Bitte zuerst installieren."}

        unit_name = f"msm-{server.linux_user}.service"
        unit_path = f"/etc/systemd/system/{unit_name}"
        exec_start = self._build_exec_start(server, exe)

        unit_content = build_systemd_unit(
            name=server.name,
            linux_user=server.linux_user,
            working_dir=server.install_dir,
            exec_start=exec_start,
            cpu_limit_percent=server.cpu_limit_percent,
            ram_limit_mb=server.ram_limit_mb,
            disk_limit_gb=server.disk_limit_gb,
        )
        try:
            with open(unit_path, "w", encoding="utf-8") as f:
                f.write(unit_content)
        except OSError as e:
            return {"error": f"Konnte systemd-Unit nicht schreiben: {e}"}

        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        subprocess.run(["systemctl", "enable", unit_name], check=False, capture_output=True)
        subprocess.run(["systemctl", "start", unit_name], check=False, capture_output=True)
        return {"message": "Server gestartet", "unit": unit_name}

    def stop(self, server) -> dict:
        unit_name = f"msm-{server.linux_user}.service"
        subprocess.run(["systemctl", "stop", unit_name], check=False, capture_output=True)
        return {"message": "Server gestoppt", "unit": unit_name}

    def get_status(self, server) -> ServerStatus:
        unit_name = f"msm-{server.linux_user}.service"
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit_name],
                capture_output=True, text=True, timeout=5
            )
            active = result.stdout.strip() == "active"
        except Exception:
            active = False

        return ServerStatus(
            status="running" if active else "stopped",
            cpu_percent=None,
            ram_mb=None,
            disk_mb=None,
            uptime_seconds=None,
            players_online=None,
        )

    def get_logs(self, server, lines: int = 100) -> str:
        log_path = os.path.join(server.install_dir, "ConanSandbox", "Saved", "Logs", "ConanSandbox.log")
        if not os.path.exists(log_path):
            return ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
            return "".join(all_lines[-lines:])
        except Exception:
            return ""

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

    def install_mod(self, server, workshop_id: str) -> dict:
        cmd = [
            settings.steamcmd_path,
            "+force_install_dir", server.install_dir,
            "+login", "anonymous",
            "+workshop_download_item", self.WORKSHOP_ID, workshop_id,
            "+quit",
        ]
        thread = threading.Thread(
            target=_run_install_with_logging,
            args=(cmd, server.id, server.install_dir),
            daemon=True,
        )
        thread.start()
        return {"message": f"Mod {workshop_id} wird installiert"}

    def get_mod_support(self) -> dict | None:
        return {
            "workshop_id": self.WORKSHOP_ID,
            "dependency_resolution": True,
        }
