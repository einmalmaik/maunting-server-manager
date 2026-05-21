import os
import subprocess

from config import settings
from games.base import GamePlugin, ServerStatus, ConfigField, build_systemd_unit


class DayZPlugin(GamePlugin):
    """DayZ Linux Dedicated Server Plugin — Linux native.

    Offizielle Doku: https://community.bistudio.com/wiki/DayZ:Server_Hosting
    App ID: 223350. Es wird ausschließlich die native Linux-Version genutzt.
    Wine-Fallback ist nicht vorgesehen.
    """

    game_id = "dayz"
    game_name = "DayZ"
    supports_mods = True

    APP_ID = "223350"
    WORKSHOP_ID = "221100"

    def _resolve_executable(self, server) -> str | None:
        """Nur native Linux-Binaries — kein Wine-Fallback."""
        candidates = [
            os.path.join(server.install_dir, "DayZServer"),
            os.path.join(server.install_dir, "DayZServer_x64"),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def install(self, server) -> dict:
        cmd = [
            settings.steamcmd_path,
            "+force_install_dir", server.install_dir,
            "+login", "anonymous",
            "+app_update", self.APP_ID,
            "+quit",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return {"pid": proc.pid, "message": "Installation gestartet"}

    def update(self, server) -> dict:
        return self.install(server)

    def start(self, server) -> dict:
        exe = self._resolve_executable(server)
        if not exe:
            return {"error": "Server-Executable nicht gefunden. Bitte zuerst installieren."}

        unit_name = f"msm-{server.linux_user}.service"
        unit_path = f"/etc/systemd/system/{unit_name}"

        # Port-Parameter hinzufügen
        port_args = ""
        if server.game_port:
            port_args += f" -port={server.game_port}"
        # DayZ nutzt automatisch game_port+1 für Steam Query
        # und game_port+3 für BattlEye — durch unseren Port-Block (5 Ports)
        # ist das automatisch sicher.

        # Nur native Linux — kein Wine-Fallback
        exec_start = f"{exe}{port_args}"

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
        log_path = os.path.join(server.install_dir, "log", "script_1.log")
        if not os.path.exists(log_path):
            log_path = os.path.join(server.install_dir, "log_1.txt")
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

    def get_mod_support(self) -> dict | None:
        return {
            "workshop_id": self.WORKSHOP_ID,
            "dependency_resolution": True,
            "symlink_mods": True,
        }
