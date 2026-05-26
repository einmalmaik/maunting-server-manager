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
from pathlib import Path

from blueprints import Blueprint, render_argv
from blueprints.http_source import install_http_source
from blueprints.renderer import render_env_values
from blueprints.schema import BlueprintPortProtocol, BlueprintSourceType
from games.base import (
    CONTAINER_DATA_DIR,
    ConfigField,
    GamePlugin,
    _append_console_log,
    _require_bind_ip,
    active_mod_ids,
    finish_install,
    run_steamcmd_install,
    run_steamcmd_workshop_download,
)
from services.docker_service import PortPublish
from services.steam_account_service import SteamAccountService


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
            requires_login = bp.source.steam.requiresLogin

            if requires_login and not SteamAccountService.is_configured():
                return {
                    "error": (
                        "Dieses Spiel benoetigt einen globalen Steam-Account-Login. "
                        "Bitte unter Einstellungen → Steam Account einen Benutzer "
                        "und Passwort hinterlegen (Steam Guard muss deaktiviert sein, "
                        "siehe Hinweis dort)."
                    )
                }

            app_id = bp.source.steam.appId
            install_dir = server.install_dir
            server_id = server.id

            def _install():
                result = run_steamcmd_install(
                    server_id=server_id,
                    install_dir=install_dir,
                    app_id=app_id,
                    use_authenticated_login=requires_login,
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

        if bp.source.type == BlueprintSourceType.MANUAL_UPLOAD:
            assert bp.source.manual is not None
            install_dir = Path(server.install_dir)
            install_dir.mkdir(parents=True, exist_ok=True)

            readme = install_dir / "MANUAL_INSTALL.md"
            if not readme.exists():
                readme.write_text(
                    f"# Manuelle Installation: {bp.meta.name}\n\n"
                    f"{bp.source.manual.instructions}\n\n"
                    f"Erforderliche Dateien:\n"
                    + "\n".join(f"- `{p}`" for p in bp.source.manual.requiredFiles)
                    + (f"\n\nWeitere Infos: {bp.source.manual.instructionsUrl}\n" if bp.source.manual.instructionsUrl else "\n"),
                    encoding="utf-8",
                )

            _append_console_log(
                server.id,
                f"[MSM] Blueprint '{bp.meta.id}' erwartet manuelle Uploads:\n"
                + "\n".join(f"  - {p}" for p in bp.source.manual.requiredFiles)
                + "\n[MSM] Status: awaiting_files\n",
            )
            finish_install(server.id, {"ok": True, "next_status": "awaiting_files"})
            return {"message": "Installation: warte auf manuellen Upload"}

        if bp.source.type in (BlueprintSourceType.DOCKER_ONLY, BlueprintSourceType.CUSTOM):
            _append_console_log(
                server.id,
                f"[MSM] Blueprint '{bp.meta.id}' ist Docker-only — keine Dateien "
                "zu installieren. Image enthaelt den Server. Status: bereit zum Starten.\n",
            )
            finish_install(server.id, {"ok": True})
            return {"message": "Installation nicht erforderlich (Source-Typ)"}

        return {"error": f"Unbekannter Source-Typ: {bp.source.type}"}

    # ─ Container ──────────────────────────────────────────────────────────

    def _server_ports(self, server) -> dict[str, int | None]:
        return {
            "game": server.game_port,
            "query": server.query_port,
            "rcon": server.rcon_port,
        }

    def build_container_command(self, server) -> list[str]:
        return render_argv(
            self._blueprint,
            install_dir=CONTAINER_DATA_DIR,
            ports=self._server_ports(server),
            active_mod_ids=active_mod_ids(server),
            extra_env=self._blueprint.runtime.env,
        )

    def build_container_env(self, server) -> dict[str, str]:
        # Port-Tokens in Env-Werten aufloesen (z. B. ``SERVER_PORT={GAME_PORT}``
        # fuer ``itzg/minecraft-server``). Werte selbst werden NIE geloggt.
        return render_env_values(
            self._blueprint.runtime.env,
            ports=self._server_ports(server),
        )

    def build_port_publishes(self, server) -> list[PortPublish]:
        """Port-Publishes aus der Blueprint statt UDP-Hartkodierung.

        Liest Protokoll je Port-Rolle aus ``blueprint.ports``. Damit funktionieren
        TCP-Spiele (Minecraft & Co.) genauso wie UDP-Spiele (DayZ, Hytale) im
        gleichen Blueprint-System. Host- und Container-Port sind identisch —
        Container-seitig nutzt das Image den gleichen Port (entweder per
        Startup-Arg ``--bind 0.0.0.0:{GAME_PORT}`` oder per Env-Var
        ``SERVER_PORT={GAME_PORT}``).

        Bind-IP-Pflicht aus :func:`games.base._require_bind_ip` bleibt bestehen —
        kein ``0.0.0.0``-Bypass.
        """
        bind_ip = _require_bind_ip(server)
        port_map: dict[str, int | None] = self._server_ports(server)
        out: list[PortPublish] = []
        for port_def in self._blueprint.ports:
            role = port_def.name.value
            host_port = port_map.get(role)
            if not host_port:
                continue
            protocol = (
                "tcp" if port_def.protocol == BlueprintPortProtocol.TCP else "udp"
            )
            out.append(PortPublish(host_port, host_port, protocol, bind_ip))
        return out

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
        requires_login = False
        if self._blueprint.source.type == BlueprintSourceType.STEAM and self._blueprint.source.steam:
            requires_login = self._blueprint.source.steam.requiresLogin

        def _install():
            run_steamcmd_workshop_download(
                server_id=server_id,
                install_dir=install_dir,
                workshop_app_id=workshop_app_id,
                workshop_item_id=workshop_id,
                use_authenticated_login=requires_login,
            )
            # Nach jedem Mod-Install die Modliste re-generieren (falls Datei-
            # injection); fuer startupArg ist das ein No-op.
            self.update_modlist(server)
            _append_console_log(server.id, f"[MSM] Mod {workshop_id} verarbeitet\n")

        threading.Thread(target=_install, daemon=True).start()
        return {"message": f"Mod {workshop_id} wird verarbeitet"}
