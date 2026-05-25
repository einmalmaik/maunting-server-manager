"""Generischer Game-Plugin-Wrapper, getrieben von einer Blueprint-JSON.

Wird fuer **Community-Blueprints** instanziiert (Native-Plugins fuer DayZ/Conan
bleiben Python-Klassen mit game-spezifischer Installations-Logik). Der Wrapper
deckt die Standardfaelle ab:

- Docker-Image + Startup-Argv aus der Blueprint
- Source ``steam`` → SteamCMD-Install (App-ID aus der Blueprint)
- Source ``http``  → Streaming-Download via :mod:`blueprints.http_source`
- Source ``dockerOnly`` / ``custom`` → kein Install (UI markiert ``stopped``)
- Workshop-Mods via ``modInjection=startupArg|file`` (Modliste schreibt der
  Helfer ``write_workshop_modlist``)

Was NICHT abgedeckt ist (bewusst, KISS):

- Game-spezifische Filesystem-Operationen (Symlinks, .pak-Copy etc.). Wer
  sowas braucht, schreibt ein natives Plugin — das ist die dokumentierte
  Grenze des Blueprint-Systems.
"""

from __future__ import annotations

import threading

from blueprints import Blueprint, render_argv
from blueprints.http_source import install_http_source
from blueprints.schema import BlueprintSourceType
from games.base import (
    CONTAINER_DATA_DIR,
    ConfigField,
    GamePlugin,
    _append_console_log,
    active_mod_ids,
    finish_install,
    run_steamcmd_install,
    run_steamcmd_workshop_download,
)


class BlueprintPlugin(GamePlugin):
    """GamePlugin, das seine Metadaten ausschliesslich aus einer Blueprint liest."""

    def __init__(self, blueprint: Blueprint) -> None:
        self._blueprint = blueprint
        self.game_id = blueprint.meta.id
        self.game_name = blueprint.meta.name
        self.docker_image = blueprint.runtime.image
        bp_mods = blueprint.effective_mods()
        self.supports_mods = bp_mods.supportsMods
        self.supports_steam_workshop = bp_mods.supportsSteamWorkshop

    # ─ Identitaet ─────────────────────────────────────────────────────────

    def get_blueprint(self) -> Blueprint:
        return self._blueprint

    # ─ Setup ──────────────────────────────────────────────────────────────

    def install(self, server) -> dict:
        bp = self._blueprint
        if bp.source.type == BlueprintSourceType.STEAM:
            assert bp.source.steam is not None
            app_id = bp.source.steam.appId
            install_dir = server.install_dir
            server_id = server.id

            def _install():
                result = run_steamcmd_install(
                    server_id=server_id,
                    install_dir=install_dir,
                    app_id=app_id,
                )
                finish_install(server_id, result)

            threading.Thread(target=_install, daemon=True).start()
            return {"message": "Installation gestartet"}

        if bp.source.type == BlueprintSourceType.HTTP:
            install_dir = server.install_dir
            server_id = server.id

            def _http_install():
                _append_console_log(server_id, "[MSM] HTTP-Source-Download startet\n")
                result = install_http_source(bp, install_dir)
                if result.get("ok"):
                    _append_console_log(server_id, "[MSM] HTTP-Source erfolgreich entpackt\n")
                else:
                    _append_console_log(
                        server_id,
                        f"[MSM] HTTP-Source fehlgeschlagen: {result.get('error')}\n",
                    )
                finish_install(server_id, result)

            threading.Thread(target=_http_install, daemon=True).start()
            return {"message": "Installation gestartet"}

        if bp.source.type in (BlueprintSourceType.DOCKER_ONLY, BlueprintSourceType.CUSTOM):
            # Keine Files zu installieren — Status direkt auf ``stopped`` setzen.
            finish_install(server.id, {"ok": True})
            return {"message": "Installation nicht erforderlich (Source-Typ)"}

        return {"error": f"Unbekannter Source-Typ: {bp.source.type}"}

    # ─ Container ──────────────────────────────────────────────────────────

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
            extra_env=self._blueprint.runtime.env,
        )

    def build_container_env(self, server) -> dict[str, str]:
        # Werte werden NIE geloggt — wir geben sie 1:1 an Docker weiter.
        return dict(self._blueprint.runtime.env)

    # ─ Logs / Config (minimal) ────────────────────────────────────────────

    def get_logs(self, server, lines: int = 100) -> str:
        # Community-Blueprints haben kein vordefiniertes Logfile-Layout —
        # die UI nutzt stattdessen ``get_console_log`` aus der Basis.
        return ""

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def get_config_files(self) -> list[dict]:
        return []

    def get_backup_paths(self, server) -> list[str]:
        return [server.install_dir]

    # ─ Mods ───────────────────────────────────────────────────────────────

    def get_mod_support(self) -> dict | None:
        if not self.supports_mods:
            return None
        bp_mods = self._blueprint.effective_mods()
        return {
            "workshop_id": bp_mods.workshopAppId,
            "dependency_resolution": False,
            "required_tags": [],
        }

    def install_mod(self, server, workshop_id: str) -> dict:
        bp_mods = self._blueprint.effective_mods()
        if not bp_mods.supportsSteamWorkshop or not bp_mods.workshopAppId:
            return {"error": "Steam Workshop nicht in dieser Blueprint aktiviert"}
        workshop_app_id = bp_mods.workshopAppId
        server_id = server.id
        install_dir = server.install_dir

        def _install():
            run_steamcmd_workshop_download(
                server_id=server_id,
                install_dir=install_dir,
                workshop_app_id=workshop_app_id,
                workshop_item_id=workshop_id,
            )
            # Nach jedem Mod-Install die Modliste re-generieren (falls Datei-
            # injection); fuer startupArg ist das ein No-op.
            self.update_modlist(server)
            _append_console_log(server.id, f"[MSM] Mod {workshop_id} verarbeitet\n")

        threading.Thread(target=_install, daemon=True).start()
        return {"message": f"Mod {workshop_id} wird verarbeitet"}
