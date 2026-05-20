import os
import subprocess

from config import settings
from games.base import GamePlugin, ServerStatus, ConfigField


class DayZPlugin(GamePlugin):
    game_id = "dayz"
    game_name = "DayZ"
    supports_mods = True

    APP_ID = "223350"  # DayZ Server

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
        # DayZ Server auf Linux läuft via Wine oder nativ (Experimental)
        # Hier: Wine-Wrapper oder direkt
        exe = os.path.join(server.install_dir, "DayZServer")
        if not os.path.exists(exe) and not os.path.exists(exe + ".exe"):
            return {"error": "Server-Executable nicht gefunden. Bitte zuerst installieren."}

        unit_name = f"msm-{server.linux_user}.service"
        unit_path = f"/etc/systemd/system/{unit_name}"

        # Wine-Modus prüfen
        wine_prefix = f"{server.install_dir}/.wine"
        if os.path.exists(exe + ".exe"):
            exec_line = f"WINEPREFIX={wine_prefix} wine {exe}.exe"
        else:
            exec_line = exe

        unit_content = f"""[Unit]
Description=MSM Server {server.name}
After=network.target

[Service]
Type=simple
User={server.linux_user}
WorkingDirectory={server.install_dir}
ExecStart={exec_line}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
        with open(unit_path, "w", encoding="utf-8") as f:
            f.write(unit_content)
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
        ]

    def get_config_files(self) -> list[dict]:
        return [
            {"name": "serverDZ.cfg", "path": "serverDZ.cfg"},
            {"name": "cfgplayerspawn.xml", "path": "cfgplayerspawn.xml"},
            {"name": "cfgeconomy.xml", "path": "cfgeconomy.xml"},
            {"name": "cfgspawnabletypes.xml", "path": "cfgspawnabletypes.xml"},
            {"name": "cfgweather.xml", "path": "cfgweather.xml"},
        ]

    def get_backup_paths(self, server) -> list[str]:
        return [
            os.path.join(server.install_dir, "mpmissions"),
            os.path.join(server.install_dir, "profile"),
        ]

    def get_mod_support(self) -> dict | None:
        return {
            "workshop_id": "221100",  # DayZ Workshop
            "dependency_resolution": True,
        }
