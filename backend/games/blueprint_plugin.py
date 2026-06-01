"""Generischer Game-Plugin-Wrapper, getrieben von einer Blueprint-JSON.

Wird fuer native und Community-Blueprints instanziiert. Native Unterstuetzung
bedeutet nur, dass MSM die Blueprint-Datei mitliefert; die Runtime bleibt fuer
alle Server-Typen dieselbe.

- Docker-Image + Startup-Argv aus der Blueprint
- Source ``steam`` → SteamCMD-Install (App-ID aus der Blueprint)
- Source ``http``  → Streaming-Download via :mod:`blueprints.http_source`
- Source ``dockerOnly`` / ``custom`` → kein Install (UI markiert ``stopped``)
- Workshop-Mods via ``modInjection=startupArg|file``
- deklarative Workshop-Dateiaktionen (copy/symlink) via ``mods.postInstall``
- deklarative INI-Patches vor dem Start via ``runtime.configPatches``
"""

from __future__ import annotations

import glob
import os
import shutil
import threading
from pathlib import Path

from blueprints import Blueprint, render_argv
from blueprints.http_source import install_http_source
from blueprints.renderer import render_env_values
from blueprints.schema import (
    BlueprintConfigPatchType,
    BlueprintModListContent,
    BlueprintSteamCompatibility,
    BlueprintSourceType,
    BlueprintWorkshopFileOperation,
)
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
from games.ini_utils import set_ini_value
from services.docker_service import PortPublish, VolumeBind
from services.port_role_service import blueprint_port_requirements, normalize_port_protocol
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
                error_msg = (
                    "Dieses Spiel benötigt einen globalen Steam-Account-Login. "
                    "Bitte unter Einstellungen → Steam Account einen Benutzer "
                    "und Passwort hinterlegen (Steam Guard muss deaktiviert sein, "
                    "siehe Hinweis dort)."
                )
                # Status auf "error" setzen, sonst bleibt der Server in
                # "installing" haengen (Create-Route ignoriert Rueckgabewert).
                finish_install(server.id, {"ok": False, "error": error_msg})
                return {"error": error_msg}

            app_id = bp.source.steam.appId
            install_dir = server.install_dir
            server_id = server.id

            def _install():
                # Reinstall-Schutz (manuelle .cfg/.ini etc.): Cache vor, Restore nach.
                # Frische Install: 0 Dateien → No-Op. Nutzt zentrale Helper aus updater.py.
                from games.updater import perform_install_with_protection
                platform_str = bp.source.steam.platform.value if bp.source.steam.platform else None
                result = perform_install_with_protection(
                    server,
                    lambda: run_steamcmd_install(
                        server_id=server_id,
                        install_dir=install_dir,
                        app_id=app_id,
                        use_authenticated_login=requires_login,
                        platform=platform_str,
                    ),
                    blueprint=bp,
                )
                finish_install(server_id, result)

            threading.Thread(target=_install, daemon=True).start()
            return {"message": "Installation gestartet"}

        if bp.source.type == BlueprintSourceType.HTTP:
            install_dir = server.install_dir
            server_id = server.id

            def _http_install():
                _append_console_log(server_id, "[MSM] HTTP-Source-Download startet\n")
                # Reinstall-Schutz (manuelle Configs): Cache vor, Restore nach dem Entpacken.
                from games.updater import perform_install_with_protection
                result = perform_install_with_protection(
                    server,
                    lambda: install_http_source(bp, install_dir),
                    blueprint=bp,
                )
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
        res = {
            "game": server.game_port,
            "query": server.query_port,
            "rcon": server.rcon_port,
        }
        ports_list = getattr(server, "ports", None) or []
        for p in ports_list:
            res[p.role] = p.port
        return res

    def _runtime_data_dir(self) -> str:
        return self._blueprint.runtime.workdir or CONTAINER_DATA_DIR

    def container_uid_gid(self, server) -> tuple[int, int]:
        runtime_user = self._blueprint.runtime.user
        if runtime_user:
            uid, gid = runtime_user.split(":", 1)
            return int(uid), int(gid)
        image = self._blueprint.runtime.image.lower()
        if self._runtime_data_dir() == "/home/container" or "ptero-eggs/yolks" in image:
            return 1000, 1000
        return super().container_uid_gid(server)

    def _uses_windows_compat_runtime(self) -> bool:
        bp = self._blueprint
        if bp.source.type != BlueprintSourceType.STEAM or bp.source.steam is None:
            return False
        return bp.source.steam.compatibility in (
            BlueprintSteamCompatibility.WINE,
            BlueprintSteamCompatibility.PROTON,
        )

    def build_container_command(self, server) -> list[str]:
        argv = render_argv(
            self._blueprint,
            install_dir=self._runtime_data_dir(),
            ports=self._server_ports(server),
            bind_ip=server.public_bind_ip or None,
            active_mod_ids=active_mod_ids(server),
            extra_env=self._blueprint.runtime.env,
        )
        if not argv or not self._uses_windows_compat_runtime():
            return argv
        first = Path(argv[0]).name.lower()
        if first in {"wine", "wine64", "proton"}:
            return argv
        if argv[0].lower().endswith(".exe"):
            return ["wine", *argv]
        return argv

    def build_volume_binds(self, server) -> list[VolumeBind]:
        return [
            VolumeBind(
                host_path=server.install_dir,
                container_path=self._runtime_data_dir(),
                read_only=False,
            )
        ]

    def container_workdir(self, server) -> str:
        return self._runtime_data_dir()

    def build_container_env(self, server) -> dict[str, str]:
        # Port-Tokens in Env-Werten aufloesen (z. B. ``SERVER_PORT={GAME_PORT}``
        # fuer ``itzg/minecraft-server``). Werte selbst werden NIE geloggt.
        return render_env_values(
            self._blueprint.runtime.env,
            ports=self._server_ports(server),
            bind_ip=server.public_bind_ip or None,
        )

    def prepare_runtime(self, server) -> None:
        base = Path(server.install_dir).resolve()
        ports = self._server_ports(server)
        values = {
            "GAME_PORT": ports.get("game"),
            "QUERY_PORT": ports.get("query"),
            "RCON_PORT": ports.get("rcon"),
            "VOICE_PORT": ports.get("voice"),
            "WEB_PORT": ports.get("web"),
        }
        for k, v in ports.items():
            if k not in ("game", "query", "rcon", "voice", "web"):
                if k.startswith("custom_"):
                    num = k.split("_", 1)[1]
                    values[f"CUSTOM_PORT_{num}"] = v
                else:
                    values[f"{k.upper()}_PORT"] = v

        for patch in self._blueprint.runtime.configPatches:
            if patch.type != BlueprintConfigPatchType.INI:
                continue
            target = (base / patch.file).resolve()
            target.relative_to(base)
            value = patch.value
            skip = False
            for token, port in values.items():
                placeholder = "{" + token + "}"
                if placeholder in value:
                    if not port:
                        skip = True
                        break
                    value = value.replace(placeholder, str(port))
            if skip:
                continue
            set_ini_value(str(target), patch.section, patch.key, value)

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
        protocols = {
            p.role: normalize_port_protocol(p.protocol)
            for p in (getattr(server, "ports", None) or [])
        }
        out: list[PortPublish] = []
        for role, blueprint_protocol in blueprint_port_requirements(self._blueprint.ports):
            host_port = port_map.get(role)
            if not host_port:
                continue
            protocol = protocols.get(role, blueprint_protocol)
            out.append(PortPublish(host_port, host_port, protocol, bind_ip))
        return out

    # ─ Logs / Config (minimal) ────────────────────────────────────────────

    def get_logs(self, server, lines: int = 100) -> str:
        # Community-Blueprints haben kein vordefiniertes Logfile-Layout —
        # die UI nutzt stattdessen den SSE-Console-Stream (MSM-Logdatei +
        # Rootless-Docker-Logstream aus docker_service).
        return ""

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def get_config_files(self) -> list[dict]:
        return []

    # ─ Mods ───────────────────────────────────────────────────────────────

    def get_mod_support(self) -> dict | None:
        if not self.supports_mods:
            return None
        bp_mods = self._blueprint.effective_mods()
        return {
            "workshop_id": bp_mods.workshopAppId,
            "dependency_resolution": False,
            "required_tags": bp_mods.filterTags,
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

        result: dict = {}

        def _install():
            nonlocal result
            try:
                download_res = run_steamcmd_workshop_download(
                    server_id=server_id,
                    install_dir=install_dir,
                    workshop_app_id=workshop_app_id,
                    workshop_item_id=workshop_id,
                    use_authenticated_login=requires_login,
                )
                if not download_res.get("ok", False):
                    result = {"error": download_res.get("error", "Workshop-Download fehlgeschlagen")}
                    return
                action_res = self._run_workshop_post_install_actions(server, workshop_id)
                if "error" in action_res:
                    result = action_res
                    return
                # Nach jedem Mod-Install die Modliste re-generieren (falls Datei-
                # injection); fuer startupArg ist das ein No-op.
                self.update_modlist(server)
                _append_console_log(server.id, f"[MSM] Mod {workshop_id} verarbeitet\n")
            except Exception as exc:
                result = {"error": str(exc)}

        thread = threading.Thread(target=_install, daemon=True)
        thread.start()
        thread.join()
        return result

    def format_modlist_lines(self, server, mods: list) -> list[str]:
        bp_mods = self._blueprint.effective_mods()
        if bp_mods.modListContent != BlueprintModListContent.POST_INSTALL_TARGET_BASENAMES:
            return [m.workshop_id for m in mods]

        lines: list[str] = []
        base = Path(server.install_dir).resolve()
        for mod in mods:
            workshop_id = str(mod.workshop_id)
            for action in bp_mods.postInstall:
                if "{BASENAME}" not in action.target:
                    continue
                for source in self._resolve_workshop_sources(base, action.source, workshop_id):
                    target = self._render_workshop_path(
                        action.target,
                        workshop_id,
                        basename=source.name,
                    )
                    target_path = (base / target).resolve()
                    try:
                        target_path.relative_to(base)
                    except ValueError:
                        continue
                    if target_path.exists():
                        lines.append(target_path.name)
        return lines

    def _run_workshop_post_install_actions(self, server, workshop_id: str) -> dict:
        bp_mods = self._blueprint.effective_mods()
        if not bp_mods.postInstall:
            return {}

        base = Path(server.install_dir).resolve()
        for action in bp_mods.postInstall:
            sources = self._resolve_workshop_sources(base, action.source, workshop_id)
            if action.required and not sources:
                return {"error": f"Keine Dateien für postInstall-Quelle gefunden: {action.source}"}

            for source in sources:
                target_rel = self._render_workshop_path(
                    action.target,
                    workshop_id,
                    basename=source.name,
                )
                target = (base / target_rel).resolve()
                try:
                    source.relative_to(base)
                    target.relative_to(base)
                except ValueError:
                    return {"error": "Blueprint postInstall-Pfad verlässt install_dir"}

                target.parent.mkdir(parents=True, exist_ok=True)
                if action.operation == BlueprintWorkshopFileOperation.COPY:
                    if not source.is_file():
                        return {"error": f"postInstall copy erwartet Datei: {source.name}"}
                    shutil.copy2(source, target)
                    continue

                if target.exists() or target.is_symlink():
                    if target.is_symlink():
                        target.unlink()
                    else:
                        return {"error": f"postInstall-Ziel existiert bereits: {target_rel}"}
                os.symlink(source, target, target_is_directory=source.is_dir())

        return {}

    def cleanup_mod(self, server, workshop_id: str) -> dict:
        bp_mods = self._blueprint.effective_mods()
        if not bp_mods.supportsSteamWorkshop or not bp_mods.workshopAppId:
            return {"ok": True, "removed": []}

        base = Path(server.install_dir).resolve()
        removed: list[str] = []

        def _safe_remove(path: Path) -> None:
            try:
                if path.is_symlink():
                    path.parent.resolve(strict=False).relative_to(base)
                else:
                    path.resolve(strict=False).relative_to(base)
            except (ValueError, RuntimeError):
                raise RuntimeError("Mod-Cleanup-Pfad verlaesst install_dir")

            if path.is_symlink() or path.is_file():
                path.unlink()
                removed.append(str(path.relative_to(base)))
                return
            if path.is_dir():
                shutil.rmtree(path)
                removed.append(str(path.relative_to(base)))

        # Erst Runtime-Artefakte entfernen, solange Workshop-Quellen noch da
        # sind und {BASENAME}-Targets eindeutig berechnet werden koennen.
        for action in bp_mods.postInstall:
            sources = self._resolve_workshop_sources(base, action.source, workshop_id)
            if "{BASENAME}" in action.target:
                target_names = [source.name for source in sources]
            else:
                target_names = [""]
            for basename in target_names:
                target_rel = self._render_workshop_path(
                    action.target,
                    workshop_id,
                    basename=basename,
                )
                target = base / target_rel
                if target.exists() or target.is_symlink():
                    _safe_remove(target)

        workshop_cache = (
            base
            / "steamapps"
            / "workshop"
            / "content"
            / (bp_mods.workshopAppId or "")
            / workshop_id
        )
        if workshop_cache.exists() or workshop_cache.is_symlink():
            _safe_remove(workshop_cache)

        _append_console_log(server.id, f"[MSM] Mod {workshop_id} entfernt\n")
        return {"ok": True, "removed": removed}

    def _resolve_workshop_sources(
        self,
        base: Path,
        source_template: str,
        workshop_id: str,
    ) -> list[Path]:
        source_rel = self._render_workshop_path(source_template, workshop_id)
        if any(ch in source_rel for ch in ("*", "?", "[")):
            matches = glob.glob(str(base / source_rel), recursive=True)
            return [Path(match).resolve() for match in matches]
        source = (base / source_rel).resolve()
        return [source] if source.exists() else []

    def _render_workshop_path(
        self,
        template: str,
        workshop_id: str,
        *,
        basename: str = "",
    ) -> str:
        bp_mods = self._blueprint.effective_mods()
        return (
            template
            .replace("{WORKSHOP_APP_ID}", bp_mods.workshopAppId or "")
            .replace("{WORKSHOP_ID}", workshop_id)
            .replace("{BASENAME}", basename)
        )
