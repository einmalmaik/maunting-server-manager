import glob
import os
import subprocess
import threading

from config import settings
from games.base import GamePlugin, ServerStatus, ConfigField, build_systemd_unit, _run_install_with_logging, _append_console_log, query_a2s_info


class DayZPlugin(GamePlugin):
    """DayZ Linux Dedicated Server Plugin — Linux native.

    Offizielle Doku: https://community.bohemia.net/wiki/DayZ:Hosting_a_Linux_Server
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
        thread = threading.Thread(
            target=_run_install_with_logging,
            args=(cmd, server.id, server.install_dir),
            daemon=True,
        )
        thread.start()
        return {"message": "Installation gestartet"}

    def update(self, server) -> dict:
        return self.install(server)

    def _get_mod_ids(self, server) -> list[str]:
        """Reads installed mod IDs from DB."""
        try:
            from database import SessionLocal
            from models import Mod
            db = SessionLocal()
            try:
                mods = db.query(Mod).filter(Mod.server_id == server.id).order_by(Mod.load_order.asc()).all()
                return [m.workshop_id for m in mods]
            finally:
                db.close()
        except Exception:
            return []

    def start(self, server) -> dict:
        exe = self._resolve_executable(server)
        if not exe:
            return {"error": "Server-Executable nicht gefunden. Bitte zuerst installieren."}

        unit_name = f"msm-{server.linux_user}.service"
        unit_path = f"/etc/systemd/system/{unit_name}"

        port_args = ""
        if server.game_port:
            port_args += f" -port={server.game_port}"

        # Build -mod= parameter from installed mods
        mod_ids = self._get_mod_ids(server)
        mod_arg = ""
        if mod_ids:
            mod_arg = f' "-mod={";".join(mod_ids)};"'

        exec_start = f"{exe}{port_args}{mod_arg}"

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
            subprocess.run(
                ["sudo", "tee", unit_path],
                input=unit_content, capture_output=True, text=True, check=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            return {"error": f"Konnte systemd-Unit nicht schreiben: {e}"}

        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False, capture_output=True)
        subprocess.run(["sudo", "systemctl", "enable", unit_name], check=False, capture_output=True)
        subprocess.run(["sudo", "systemctl", "start", unit_name], check=False, capture_output=True)
        return {"message": "Server gestartet", "unit": unit_name}

    def stop(self, server) -> dict:
        unit_name = f"msm-{server.linux_user}.service"
        subprocess.run(["sudo", "systemctl", "stop", unit_name], check=False, capture_output=True)
        return {"message": "Server gestoppt", "unit": unit_name}

    def get_status(self, server) -> ServerStatus:
        unit_name = f"msm-{server.linux_user}.service"
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "is-active", unit_name],
                capture_output=True, text=True, timeout=5
            )
            active = result.stdout.strip() == "active"
        except Exception:
            active = False

        players_online = None
        if active and server.query_port:
            a2s = query_a2s_info("127.0.0.1", server.query_port)
            if a2s:
                players_online = a2s["players"]

        return ServerStatus(
            status="running" if active else "stopped",
            cpu_percent=None,
            ram_mb=None,
            disk_mb=None,
            uptime_seconds=None,
            players_online=players_online,
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

    def install_mod(self, server, workshop_id: str) -> dict:
        """DayZ mod install: SteamCMD download → symlink to server root → copy keys."""
        def _install():
            install_dir = server.install_dir
            workshop_dir = os.path.join(install_dir, "steamapps", "workshop", "content", self.WORKSHOP_ID, workshop_id)

            # 1) Download via SteamCMD
            cmd = [
                settings.steamcmd_path,
                "+force_install_dir", install_dir,
                "+login", "anonymous",
                "+workshop_download_item", self.WORKSHOP_ID, workshop_id,
                "+quit",
            ]
            _run_install_with_logging(cmd, server.id, install_dir)

            # 2) Symlink mod folder to server root
            link_path = os.path.join(install_dir, workshop_id)
            if os.path.isdir(workshop_dir):
                if os.path.exists(link_path):
                    if os.path.islink(link_path):
                        os.unlink(link_path)
                    else:
                        _append_console_log(server.id, f"[MSM] Warnung: {link_path} existiert bereits und ist kein Symlink\n")
                        return
                os.symlink(workshop_dir, link_path)
                _append_console_log(server.id, f"[MSM] Mod {workshop_id} verlinkt: {link_path} → {workshop_dir}\n")

                # 3) Symlink keys
                keys_dir = os.path.join(install_dir, "keys")
                os.makedirs(keys_dir, exist_ok=True)
                mod_keys = os.path.join(workshop_dir, "keys")
                if os.path.isdir(mod_keys):
                    for key_file in glob.glob(os.path.join(mod_keys, "*.bikey")):
                        key_link = os.path.join(keys_dir, os.path.basename(key_file))
                        if os.path.exists(key_link):
                            os.unlink(key_link)
                        os.symlink(key_file, key_link)
                        _append_console_log(server.id, f"[MSM] Key verlinkt: {os.path.basename(key_file)}\n")
            else:
                _append_console_log(server.id, f"[MSM] Warnung: Workshop-Verzeichnis nicht gefunden: {workshop_dir}\n")

            _append_console_log(server.id, f"[MSM] Mod {workshop_id} Installation abgeschlossen.\n")

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        return {"message": f"Mod {workshop_id} wird installiert"}

    def get_mod_support(self) -> dict | None:
        return {
            "workshop_id": self.WORKSHOP_ID,
            "dependency_resolution": True,
            "symlink_mods": True,
        }
