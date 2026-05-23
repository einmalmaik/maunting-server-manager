import glob
import os
import shutil
import subprocess
import threading

from config import settings
from games.base import GamePlugin, ServerStatus, ConfigField, build_systemd_unit, _run_install_with_logging, _append_console_log, query_a2s_info

_SYSTEM_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}


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
            subprocess.run(
                ["sudo", "tee", unit_path],
                input=unit_content, capture_output=True, text=True, check=True,
                env=_SYSTEM_ENV
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            return {"error": f"Konnte systemd-Unit nicht schreiben: {e}"}

        try:
            subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False, capture_output=True, env=_SYSTEM_ENV)
            subprocess.run(["sudo", "systemctl", "enable", unit_name], check=False, capture_output=True, env=_SYSTEM_ENV)
            subprocess.run(["sudo", "systemctl", "start", unit_name], check=False, capture_output=True, env=_SYSTEM_ENV)
        except FileNotFoundError as e:
            return {"error": f"sudo nicht gefunden: {e}"}

        # Auto-Backup nach Start auslösen (fire-and-forget)
        try:
            from database import SessionLocal
            import requests
            db2 = SessionLocal()
            try:
                requests.post(f"http://localhost:8000/api/backups/{server.id}/auto", timeout=5)
            finally:
                db2.close()
        except Exception:
            pass

        return {"message": "Server gestartet", "unit": unit_name}

    def stop(self, server) -> dict:
        unit_name = f"msm-{server.linux_user}.service"
        subprocess.run(["sudo", "systemctl", "stop", unit_name], check=False, capture_output=True, env=_SYSTEM_ENV)
        return {"message": "Server gestoppt", "unit": unit_name}

    def get_status(self, server) -> ServerStatus:
        unit_name = f"msm-{server.linux_user}.service"
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "is-active", unit_name],
                capture_output=True, text=True, timeout=5,
                env=_SYSTEM_ENV
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

    def install_mod(self, server, workshop_id: str) -> dict:
        """Conan Exiles mod install: SteamCMD download → copy .pak to Mods/ → update modlist.txt."""
        def _install():
            install_dir = server.install_dir
            workshop_dir = os.path.join(install_dir, "steamapps", "workshop", "content", self.WORKSHOP_ID, workshop_id)
            mods_dir = os.path.join(install_dir, "ConanSandbox", "Mods")

            # 1) Download via SteamCMD
            cmd = [
                settings.steamcmd_path,
                "+force_install_dir", install_dir,
                "+login", "anonymous",
                "+workshop_download_item", self.WORKSHOP_ID, workshop_id,
                "+quit",
            ]
            _run_install_with_logging(cmd, server.id, install_dir)

            # 2) Find .pak files and copy to ConanSandbox/Mods/
            os.makedirs(mods_dir, exist_ok=True)
            pak_files = glob.glob(os.path.join(workshop_dir, "**", "*.pak"), recursive=True)

            if not pak_files:
                _append_console_log(server.id, f"[MSM] Warnung: Keine .pak-Dateien für Mod {workshop_id} gefunden\n")
                return

            copied_paks = []
            for pak_path in pak_files:
                pak_name = os.path.basename(pak_path)
                dest = os.path.join(mods_dir, pak_name)
                shutil.copy2(pak_path, dest)
                copied_paks.append(pak_name)
                _append_console_log(server.id, f"[MSM] Mod-Datei kopiert: {pak_name}\n")

            # 3) Update modlist.txt
            self._update_modlist(server)

            _append_console_log(server.id, f"[MSM] Mod {workshop_id} Installation abgeschlossen.\n")

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        return {"message": f"Mod {workshop_id} wird installiert"}

    def _update_modlist(self, server) -> None:
        """Rebuilds modlist.txt from all installed mods in load order."""
        install_dir = server.install_dir
        mods_dir = os.path.join(install_dir, "ConanSandbox", "Mods")
        modlist_path = os.path.join(mods_dir, "modlist.txt")
        os.makedirs(mods_dir, exist_ok=True)

        # Get ordered mod list from DB
        try:
            from database import SessionLocal
            from models import Mod
            db = SessionLocal()
            try:
                mods = db.query(Mod).filter(Mod.server_id == server.id, Mod.enabled == True).order_by(Mod.load_order.asc()).all()
            finally:
                db.close()
        except Exception:
            return

        # For each enabled mod, find .pak files in its workshop dir
        lines = []
        for mod in mods:
            workshop_dir = os.path.join(install_dir, "steamapps", "workshop", "content", self.WORKSHOP_ID, mod.workshop_id)
            pak_files = glob.glob(os.path.join(workshop_dir, "**", "*.pak"), recursive=True)
            for pak_path in pak_files:
                pak_name = os.path.basename(pak_path)
                # Also check if .pak exists in Mods/ dir
                if os.path.exists(os.path.join(mods_dir, pak_name)):
                    lines.append(pak_name)

        try:
            with open(modlist_path, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(f"{line}\n")
        except OSError as e:
            _append_console_log(server.id, f"[MSM] Fehler beim Schreiben der modlist.txt: {e}\n")


