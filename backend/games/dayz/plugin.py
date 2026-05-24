"""DayZ — Docker-basiertes Plugin.

Lifecycle läuft komplett über GamePlugin-Defaults (docker_service). Spezifisch
sind: Mod-Symlink-Logik, Start-Argumente (-mod=…;), Workshop-Pfade.
"""

from __future__ import annotations

import glob
import os
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


class DayZPlugin(GamePlugin):
    """DayZ Linux Dedicated Server — Linux native, in Docker.

    Offizielle Doku: https://community.bohemia.net/wiki/DayZ:Hosting_a_Linux_Server
    App ID: 223350 (Server), Workshop App ID: 221100 (Mods).
    """

    game_id = "dayz"
    game_name = "DayZ"
    supports_mods = True

    APP_ID = "223350"
    WORKSHOP_ID = "221100"

    docker_image = "cm2network/steamcmd:root"
    container_needs_tmpfs = False
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
            finish_install(server_id, result)

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        return {"message": "Installation gestartet"}

    # ─ Mods → Start-Argument ─────────────────────────────────────────────

    def _get_active_mod_ids(self, server) -> list[str]:
        """Liest aktivierte (`enabled=True`) Mods in Lade-Reihenfolge.

        Inaktive Mods bleiben im DB-State, werden aber NICHT in die
        Startargumente geschrieben.
        """
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
                return [m.workshop_id for m in mods]
            finally:
                db.close()
        except Exception:
            return []

    # ─ Container-Config ──────────────────────────────────────────────────

    def _container_executable(self) -> str:
        """Pfad zum DayZ-Server-Binary INNERHALB des Containers (Bind-Mount)."""
        return f"{CONTAINER_DATA_DIR}/DayZServer"

    def build_container_command(self, server) -> list[str]:
        cmd: list[str] = [self._container_executable()]
        if server.game_port:
            cmd.append(f"-port={server.game_port}")
        mod_ids = self._get_active_mod_ids(server)
        if mod_ids:
            # DayZ erwartet ein einzelnes Argument mit semikolon-separierten Mod-IDs.
            cmd.append(f"-mod={';'.join(mod_ids)};")
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
        """DayZ mod install: SteamCMD download → symlink to server root → copy keys."""
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
