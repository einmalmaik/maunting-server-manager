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
- deklarative Seed-Dateien (nur wenn fehlend) via ``runtime.seedFiles``
- deklarative INI-/Regex-Patches vor dem Start via ``runtime.configPatches``
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shlex
import shutil
import stat
import threading
from pathlib import Path

from blueprints import Blueprint, render_argv
from blueprints.http_source import install_http_source
from blueprints.renderer import render_env_values
from blueprints.schema import (
    BlueprintConfigPatchType,
    BlueprintModInjection,
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
    _resolve_steam_login,
    active_mod_ids,
    finish_install,
    run_steamcmd_install,
    run_steamcmd_workshop_download_batch,
)
from games.ini_utils import set_ini_value
from services.docker_service import PortPublish, VolumeBind
from services.port_role_service import blueprint_port_requirements, normalize_port_protocol
from services.steam_account_service import SteamAccountService


logger = logging.getLogger(__name__)


def _load_detached_server(server_id: int):
    """Laedt einen Server samt Node in einer eigenen, sofort geschlossenen Session.

    Der Aufrufer erhaelt ein vollstaendig geladenes, aber losgeloestes Objekt.
    Das ist genau der Zustand, den ein Installations-Thread braucht:

    - Er darf die Request-Session nicht verwenden. Die ist geschlossen, sobald
      die Antwort geschrieben wurde, und waere ausserdem nicht thread-sicher.
    - Er darf aber auch keine Session ueber die gesamte Installation offen
      halten — ein SteamCMD-Lauf dauert Minuten bis Stunden und wuerde so lange
      eine Verbindung aus dem Pool blockieren.

    `joinedload` ist hier notwendig und nicht bloss eine Optimierung: nach dem
    Schliessen der Session kann eine nicht geladene Relationship nicht mehr
    nachgeladen werden.
    """
    from sqlalchemy.orm import joinedload

    from database import SessionLocal
    from models import Server

    db = SessionLocal()
    try:
        server = (
            db.query(Server)
            .options(joinedload(Server.node))
            .filter(Server.id == server_id)
            .first()
        )
        if server is not None:
            # `expunge_all` loest Server und Node aus der Session, ohne die
            # bereits geladenen Werte zu verwerfen.
            db.expunge_all()
        return server
    finally:
        db.close()


def _resolve_github_token_for_server(server_id: int) -> str:
    """GitHub-Token fuer genau diesen Server (Bindung vor panelweitem Zugang).

    Eigene, kurzlebige Session — der Aufruf kommt aus einem Installations-Thread.
    Ohne verfuegbaren Zugang wird ein leerer String zurueckgegeben; oeffentliche
    Repositories funktionieren dann weiterhin ohne Token.
    """
    from database import SessionLocal
    from models import KIND_GITHUB_TOKEN
    from services.credential_service import resolve_for_server

    db = SessionLocal()
    try:
        resolved = resolve_for_server(db, server_id, KIND_GITHUB_TOKEN)
    finally:
        db.close()
    return resolved.secret if resolved is not None else ""


def _start_install_worker(server_id: int, name: str, body) -> None:
    """Startet einen Installations-Thread, der immer terminal abschliesst.

    Der Rumpf bekommt einen frisch geladenen Server uebergeben. Frueher
    schleppten die Closures das Request-gebundene ORM-Objekt mit; ein Zugriff
    auf `server.node` im Thread lief dann auf eine geschlossene Session und warf
    `DetachedInstanceError` — auf Remote-Nodes bei jeder Installation.

    `finish_install` ist der einzige Ort, der Serverstatus, Provisionierungs-Task
    und die node-weite Install-Sperre abschliesst. Wirft der Thread davor eine
    Ausnahme, wurde bisher nichts davon erreicht: Der Server blieb dauerhaft auf
    "installing", der Task auf "running" und die Sperre bis zu ihrem TTL belegt —
    damit war jede weitere Installation oder jedes Update auf diesem Node
    blockiert. Der Wrapper faengt deshalb alles ab und meldet einen Fehlschlag.

    Die Fehlermeldung nennt bewusst nur den Ausnahmetyp: Pfade und
    Providerdetails gehoeren nicht in den fuer Benutzer sichtbaren Serverstatus.
    """

    def _runner() -> None:
        try:
            server = _load_detached_server(server_id)
            if server is None:
                # Der Server wurde zwischen Anforderung und Threadstart entfernt.
                finish_install(
                    server_id,
                    {"ok": False, "error": "Server existiert nicht mehr"},
                )
                return
            body(server)
        except Exception as exc:
            logger.exception("Installations-Thread abgebrochen (server_id=%s)", server_id)
            finish_install(
                server_id,
                {"ok": False, "error": f"Installation abgebrochen ({type(exc).__name__})"},
            )

    threading.Thread(target=_runner, name=name, daemon=True).start()


WORKSHOP_BATCH_SIZE = 25
"""Max Workshop-Items pro SteamCMD-Aufruf.

Begruendung: SteamCMD-CLI-Limit + getestete Stabilitaet. Groessere Batches
fuehren zu Timeouts in ``+workshop_download_item``-Ketten. Chunks oberhalb
dieses Limits lohnen selten: langer Lauf, schlechtere Fehler-Isolation pro
Mod. Provider-neutraler Wert, gilt fuer jeden SteamCMD-gestuetzten Blueprint.
"""


def _steam_install_is_reinstall(install_dir: str, app_id: str) -> bool:
    """True wenn Server-Dateien bereits vorhanden (expliziter Reinstall-Pfad)."""
    root = Path(install_dir)
    if not root.is_dir():
        return False
    manifest = root / "steamapps" / f"appmanifest_{app_id}.acf"
    if manifest.is_file():
        return True
    try:
        return any(root.iterdir())
    except OSError:
        return False


def _purge_conan_extracted_mod_cache(install_base: Path, pak_basename: str) -> list[str]:
    """Loescht gecachte LinuxServer-Extracts nach Pak-Update (Conan-Mount)."""
    stem = Path(pak_basename).stem
    extracted = install_base / "ConanSandbox" / "Saved" / "ExtractedMods"
    if not extracted.is_dir():
        return []
    removed: list[str] = []
    for name in (
        f"{stem}-LinuxServer.pak",
        f"{stem}-LinuxServer.utoc",
        f"{stem}-LinuxServer.ucas",
    ):
        path = extracted / name
        if not path.is_file():
            continue
        try:
            path.resolve(strict=False).relative_to(install_base.resolve())
        except ValueError:
            continue
        path.unlink(missing_ok=True)
        removed.append(str(path.relative_to(install_base)))
    return removed


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
        self.supports_curseforge = bp_mods.supportsCurseForge

    # ─ Identitaet ─────────────────────────────────────────────────────────

    def get_blueprint(self) -> Blueprint:
        return self._blueprint

    # ─ Setup ──────────────────────────────────────────────────────────────

    def install(self, server) -> dict:
        bp = self._blueprint
        if bp.source.type == BlueprintSourceType.STEAM:
            assert bp.source.steam is not None
            requires_login = bp.source.steam.requiresLogin

            # Das Gate prueft dieselbe Aufloesung wie der spaetere Login: erst
            # ein diesem Server zugewiesenes Konto, dann der panelweite Account.
            # Sonst wuerde ein Kunde mit eigenem Steam-Konto hier abgewiesen,
            # obwohl die Installation funktionieren wuerde.
            if requires_login and _resolve_steam_login(server.id) is None:
                error_msg = (
                    "Dieses Spiel benötigt einen Steam-Account-Login. Bitte im "
                    "Server unter Zugangsdaten ein eigenes Steam-Konto hinterlegen "
                    "oder unter Einstellungen → Steam Account einen panelweiten "
                    "Account setzen (Steam Guard muss deaktiviert sein)."
                )
                # Status auf "error" setzen, sonst bleibt der Server in
                # "installing" haengen (Create-Route ignoriert Rueckgabewert).
                finish_install(server.id, {"ok": False, "error": error_msg})
                return {"error": error_msg}

            app_id = bp.source.steam.appId
            install_dir = server.install_dir
            server_id = server.id

            def _install(server):
                # Reinstall-Schutz (manuelle .cfg/.ini etc.): Cache vor, Restore nach.
                # Frische Install: 0 Dateien → No-Op. Nutzt zentrale Helper aus updater.py.
                from games.updater import _steam_effective_branch, perform_install_with_protection
                platform_str = bp.source.steam.platform.value if bp.source.steam.platform else None
                reinstall = _steam_install_is_reinstall(install_dir, app_id)
                # Reinstall: immer validate + aktuelle Binaries von Steam (Workshop unangetastet).
                validate_flag = (
                    True
                    if reinstall
                    else bool(getattr(bp.source.steam, "validate_", True))
                )
                if reinstall:
                    _append_console_log(
                        server_id,
                        "[MSM] Reinstall: hole aktuelle Spiel-Binaries von Steam "
                        "(Configs werden gesichert, Workshop-Mods nicht neu installiert).\n",
                    )
                result = perform_install_with_protection(
                    server,
                    lambda: run_steamcmd_install(
                        server_id=server_id,
                        install_dir=install_dir,
                        app_id=app_id,
                        use_authenticated_login=requires_login,
                        platform=platform_str,
                        # intentionally not passing steamcmd_image; use the dedicated STEAMCMD_IMAGE
                        # which has the pre-installed binary at the expected path
                        validate=validate_flag,
                        beta_branch=_steam_effective_branch(bp.source.steam),
                        node=getattr(server, "node", None),
                    ),
                    blueprint=bp,
                )
                finish_install(server_id, result)

            _start_install_worker(server_id, f"install-steam-{server_id}", _install)
            return {"message": "Installation gestartet"}

        if bp.source.type == BlueprintSourceType.HTTP:
            install_dir = server.install_dir
            server_id = server.id

            def _http_install(server):
                _append_console_log(server_id, "[MSM] HTTP-Source-Download startet\n")
                node = getattr(server, "node", None)
                reinstall = (
                    Path(install_dir).is_dir() and any(Path(install_dir).iterdir())
                    if node is None or getattr(node, "is_local", False)
                    else False
                ) if Path(install_dir).exists() else False
                if reinstall:
                    _append_console_log(
                        server_id,
                        "[MSM] Reinstall: lade aktuelle Server-Dateien von der HTTP-Quelle "
                        "(manuelle Configs werden gesichert, Workshop-Mods unverändert).\n",
                    )
                # Reinstall-Schutz (manuelle Configs): Cache vor, Restore nach dem Entpacken.
                from games.updater import perform_install_with_protection
                def _install_source():
                    if node is not None and not getattr(node, "is_local", False):
                        from blueprints.http_source import _detect_archive_type
                        from services.node_client import NodeClient
                        from urllib.parse import urlparse

                        cfg = bp.source.http
                        assert cfg is not None
                        archive_type = cfg.archiveType or _detect_archive_type(urlparse(cfg.url).path)
                        if archive_type is None:
                            return {"ok": False, "error": "Archive-Typ konnte nicht erkannt werden"}
                        return NodeClient.from_node(node, timeout=600).install_http_source({
                            "server_id": str(server_id),
                            "url": cfg.url,
                            "sha256": cfg.sha256,
                            "archive_type": archive_type.value,
                            "extract_to": cfg.extractTo,
                        })
                    return install_http_source(bp, install_dir)

                result = perform_install_with_protection(server, _install_source, blueprint=bp)
                if result.get("ok"):
                    _append_console_log(server_id, "[MSM] HTTP-Source erfolgreich entpackt\n")
                else:
                    _append_console_log(
                        server_id,
                        f"[MSM] HTTP-Source fehlgeschlagen: {result.get('error')}\n",
                    )
                finish_install(server_id, result)

            _start_install_worker(server_id, f"install-http-{server_id}", _http_install)
            return {"message": "Installation gestartet"}

        if bp.source.type == BlueprintSourceType.GITHUB:
            install_dir = server.install_dir
            server_id = server.id

            def _github_install(server):
                from blueprints.github_source import install_github_source

                _append_console_log(server_id, "[MSM] GitHub-Source: Clone/Pull startet\n")
                reinstall = Path(install_dir).is_dir() and (Path(install_dir) / ".git").is_dir()
                if reinstall:
                    _append_console_log(
                        server_id,
                        "[MSM] Reinstall/Update: git pull auf konfigurierten Branch "
                        "(Configs werden gesichert).\n",
                    )
                from games.updater import perform_install_with_protection

                def _install_source():
                    node = getattr(server, "node", None)
                    # Serverbezogener GitHub-Zugang vor dem panelweiten. Damit
                    # laeuft ein Kundenserver nicht mit dem Token des Betreibers.
                    token = _resolve_github_token_for_server(server_id)
                    if node is not None and not getattr(node, "is_local", False):
                        from services.node_client import NodeClient

                        cfg = bp.source.github
                        assert cfg is not None
                        return NodeClient.from_node(node, timeout=600).install_github_source({
                            "server_id": str(server_id),
                            "repo": cfg.repo,
                            "branch": cfg.branch,
                            "token": token,
                            "setup_commands": cfg.setupCommands,
                            "sub_path": cfg.subPath,
                            "runtime_image": bp.runtime.image,
                        })
                    return install_github_source(bp, install_dir, token)

                result = perform_install_with_protection(server, _install_source, blueprint=bp)
                if result.get("ok"):
                    _append_console_log(
                        server_id,
                        f"[MSM] GitHub-Source OK (branch={result.get('branch')}, "
                        f"commit={str(result.get('commit', ''))[:12]})\n",
                    )
                else:
                    _append_console_log(
                        server_id,
                        f"[MSM] GitHub-Source fehlgeschlagen: {result.get('error')}\n",
                    )
                finish_install(server_id, result)

            _start_install_worker(server_id, f"install-github-{server_id}", _github_install)
            return {"message": "Installation gestartet"}

        if bp.source.type == BlueprintSourceType.MANUAL_UPLOAD:
            assert bp.source.manual is not None
            install_dir = Path(server.install_dir)
            node = getattr(server, "node", None)
            is_remote = node is not None and not getattr(node, "is_local", False)
            if not is_remote:
                install_dir.mkdir(parents=True, exist_ok=True)

            readme = install_dir / "MANUAL_INSTALL.md"
            readme_content = (
                    f"# Manuelle Installation: {bp.meta.name}\n\n"
                    f"{bp.source.manual.instructions}\n\n"
                    f"Erforderliche Dateien:\n"
                    + "\n".join(f"- `{p}`" for p in bp.source.manual.requiredFiles)
                    + (f"\n\nWeitere Infos: {bp.source.manual.instructionsUrl}\n" if bp.source.manual.instructionsUrl else "\n")
            )
            if is_remote:
                from services.node_client import NodeClient

                NodeClient.from_node(node).files_write(server.id, "MANUAL_INSTALL.md", readme_content)
            elif not readme.exists():
                readme.write_text(readme_content, encoding="utf-8")

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
            host_install_dir=server.install_dir,
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

        resolved_seeds: list[dict[str, str]] = []
        for seed in self._blueprint.runtime.seedFiles:
            content = self._substitute_port_tokens(seed.content, values)
            if content is None:
                continue
            resolved_seeds.append({"file": seed.file, "content": content})

        resolved_patches: list[dict[str, str | None]] = []
        local_patches: list[tuple[object, str]] = []
        for patch in self._blueprint.runtime.configPatches:
            value = self._substitute_port_tokens(patch.value, values)
            if value is None:
                continue

            resolved_patches.append({
                "type": patch.type.value,
                "file": patch.file,
                "section": patch.section,
                "key": patch.key,
                "regex": patch.regex,
                "value": value,
            })
            local_patches.append((patch, value))

        executable_files = self._declared_required_executable_files()
        node = getattr(server, "node", None)
        if node is not None and not getattr(node, "is_local", False):
            from services.node_client import NodeClient

            NodeClient.from_node(node).files_prepare_runtime(
                server.id,
                {
                    "ensure_dirs": self._blueprint.runtime.ensureDirs,
                    "required_files": self._blueprint.runtime.requiredFiles,
                    "executable_files": executable_files,
                    "seed_files": resolved_seeds,
                    "patches": resolved_patches,
                },
            )
            return

        base = Path(server.install_dir).resolve()
        for rel_path in self._blueprint.runtime.ensureDirs:
            target = (base / rel_path).resolve()
            target.relative_to(base)
            target.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target, 0o777)
            except Exception:
                pass

        # Seed-once before patches so first-start defaults exist to patch.
        for seed in resolved_seeds:
            target = (base / seed["file"]).resolve()
            target.relative_to(base)
            if target.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target.parent, 0o777)
            except Exception:
                pass
            target.write_text(seed["content"], encoding="utf-8")
            try:
                os.chmod(target, 0o666)
            except Exception:
                pass

        for patch, value in local_patches:
            target = (base / patch.file).resolve()
            target.relative_to(base)

            if patch.type == BlueprintConfigPatchType.INI:
                assert patch.section is not None
                assert patch.key is not None
                set_ini_value(str(target), patch.section, patch.key, value)
            elif patch.type == BlueprintConfigPatchType.REGEX:
                assert patch.regex is not None
                if target.exists():
                    try:
                        content = target.read_text(encoding="utf-8")
                        new_content = re.sub(patch.regex, value, content)
                        target.write_text(new_content, encoding="utf-8")
                    except Exception as e:
                        _append_console_log(
                            server.id,
                            f"[MSM] Regex-Patching fuer {patch.file} fehlgeschlagen: {e}\n",
                        )

        missing_required_files: list[str] = []
        for rel_path in self._blueprint.runtime.requiredFiles:
            target = (base / rel_path).resolve()
            target.relative_to(base)
            if not target.is_file():
                missing_required_files.append(rel_path)
        if missing_required_files:
            files = ", ".join(missing_required_files)
            raise RuntimeError(
                f"Runtime-Dateien fehlen: {files}. Installation unvollstaendig; "
                "bitte Server neu installieren und Steam-Account/App-Zugriff pruefen."
            )

        for rel_path in executable_files:
            unresolved = base / rel_path
            if unresolved.is_symlink():
                raise RuntimeError(f"Runtime-Datei darf kein Symlink sein: {rel_path}")
            target = unresolved.resolve()
            target.relative_to(base)
            self._ensure_required_executable_mode(target, rel_path)

        try:
            self.update_modlist(server)
        except Exception as exc:
            _append_console_log(
                server.id,
                f"[MSM] update_modlist vor Start fehlgeschlagen (nicht kritisch): {exc}\n",
            )

    @staticmethod
    def _substitute_port_tokens(template: str, values: dict) -> str | None:
        """Replace ``{GAME_PORT}`` etc. Skip the whole template if a needed port is unset."""
        result = template
        for token, port in values.items():
            placeholder = "{" + token + "}"
            if placeholder not in result:
                continue
            if not port:
                return None
            result = result.replace(placeholder, str(port))
        return result

    def _declared_required_executable_files(self) -> list[str]:
        """Derive executable startup files from the existing blueprint contract."""
        required = set(self._blueprint.runtime.requiredFiles)
        templates = [self._blueprint.runtime.startup]
        templates.extend(profile.startup for profile in self._blueprint.runtime.startupProfiles or [])
        executable_files: list[str] = []
        for template in templates:
            argv = shlex.split(template)
            if not argv:
                continue
            declared = argv[0].replace("\\", "/")
            if declared.startswith("./"):
                declared = declared[2:]
            elif declared.startswith("/") or "/" not in declared:
                continue
            if declared in required and declared not in executable_files:
                executable_files.append(declared)
        return executable_files

    def _ensure_required_executable_mode(self, target: Path, rel_path: str) -> None:
        """Ensure startup files are runnable under rootless Docker ownership.

        SteamCMD/bind-mount files are often owned by a container subuid. The
        panel user cannot chmod foreign UIDs (EPERM) even after a+rwX repair.
        Accept any existing execute bit; for Wine/Proton PE binaries, readable
        is enough because wine opens the file instead of kernel execve.
        """
        try:
            target.chmod(0o750)
        except OSError:
            # Rootless: panel is not the file owner — keep going if already usable.
            pass
        if os.name != "posix":
            return
        mode = stat.S_IMODE(target.stat(follow_symlinks=False).st_mode)
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            return
        if (
            self._uses_windows_compat_runtime()
            and rel_path.lower().endswith((".exe", ".bat", ".cmd"))
            and mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        ):
            return
        raise RuntimeError(f"Runtime-Datei ist nicht ausführbar: {rel_path}")

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
        # die UI nutzt stattdessen den WS-Console-Stream (MSM-Logdatei +
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
        provider = "none"
        if bp_mods.supportsCurseForge:
            provider = "curseforge"
        elif bp_mods.supportsSteamWorkshop:
            provider = "steam"
        return {
            "provider": provider,
            "supports_steam_workshop": bp_mods.supportsSteamWorkshop,
            "supports_curseforge": bp_mods.supportsCurseForge,
            "workshop_id": bp_mods.workshopAppId,
            "curseforge_game_id": bp_mods.curseforgeGameId,
            "curseforge_class_id": bp_mods.curseforgeClassId,
            "curseforge_install_path": bp_mods.curseforgeInstallPath,
            "dependency_resolution": False,
            "required_tags": bp_mods.filterTags,
        }

    def install_mod(self, server, workshop_id: str) -> dict:
        return self.install_mods(server, [workshop_id])

    def _install_curseforge_mods(self, server, workshop_ids: list[str]) -> dict:
        bp_mods = self._blueprint.effective_mods()
        clean_ids = [str(wid).strip() for wid in workshop_ids if str(wid).strip()]
        if not clean_ids:
            return {"ok": True, "applied": 0, "items": {}}

        from services.curseforge_service import get_curseforge_service
        from services.curseforge_api_key_service import resolve_key as resolve_cf_key
        import asyncio
        from database import SessionLocal
        from models import Mod

        # Startup-Arg Injektion (z. B. ASA) - Spiel laedt Mods beim Start selbst ueber -mods=...
        if bp_mods.modInjection == BlueprintModInjection.STARTUP_ARG:
            try:
                async def _resolve_titles():
                    svc = await get_curseforge_service()
                    titles = {}
                    for wid in clean_ids:
                        try:
                            info = await svc.get_mod_details(wid)
                            if info and info.title:
                                titles[wid] = info.title
                        except Exception:
                            pass
                    return titles
                titles = asyncio.run(_resolve_titles())
                if titles:
                    db = SessionLocal()
                    try:
                        for wid, title in titles.items():
                            mod_row = db.query(Mod).filter(Mod.server_id == server.id, Mod.workshop_id == wid).first()
                            if mod_row and not mod_row.name:
                                mod_row.name = title
                        db.commit()
                    finally:
                        db.close()
            except Exception:
                pass
            for wid in clean_ids:
                _append_console_log(server.id, f"[MSM] CurseForge Mod {wid} registriert (Start-Argument)\n")
            self.update_modlist(server)
            return {"ok": True, "applied": len(clean_ids), "items": {wid: {"ok": True} for wid in clean_ids}}

        # Dateibasierte Installation (z. B. Minecraft mods/ oder plugins/)
        import httpx

        cf_key = resolve_cf_key()
        if not cf_key:
            return {"error": "CurseForge API-Key nicht konfiguriert (in Panel-Einstellungen hinterlegen)"}

        base = Path(server.install_dir).resolve()
        target_dir_rel = bp_mods.curseforgeInstallPath or "mods"
        target_dir = (base / target_dir_rel).resolve()
        try:
            target_dir.relative_to(base)
        except ValueError:
            return {"error": "curseforgeInstallPath verlaesst install_dir"}

        target_dir.mkdir(parents=True, exist_ok=True)

        async def _download_cf():
            svc = await get_curseforge_service()
            errors = []
            applied = 0
            items = {}
            for wid in clean_ids:
                try:
                    mod_info = await svc.get_mod_details(wid)
                    if not mod_info or not mod_info.latest_files:
                        errors.append(f"{wid}: Mod-Dateien nicht gefunden")
                        items[wid] = {"ok": False, "error": "Dateien nicht gefunden"}
                        continue
                    file_obj = mod_info.latest_files[0]
                    dl_url = file_obj.get("downloadUrl")
                    if not dl_url:
                        dl_url = await svc.get_file_download_url(wid, file_obj["id"])
                    if not dl_url:
                        errors.append(f"{wid}: Keine Download-URL verfuegbar")
                        items[wid] = {"ok": False, "error": "Keine Download-URL"}
                        continue
                    file_name = file_obj.get("fileName") or f"mod_{wid}.jar"
                    safe_name = Path(file_name).name
                    dest_file_name = safe_name if str(wid) in safe_name else f"cf_{wid}_{safe_name}"
                    dest_file = (target_dir / dest_file_name).resolve()
                    dest_file.relative_to(base)

                    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl_client:
                        resp = await dl_client.get(dl_url, headers={"x-api-key": cf_key})
                        resp.raise_for_status()
                        dest_file.write_bytes(resp.content)
                    self._try_chown_install_path(server, dest_file)
                    # Name in DB aktualisieren falls leer
                    if mod_info.title:
                        db = SessionLocal()
                        try:
                            mod_row = db.query(Mod).filter(Mod.server_id == server.id, Mod.workshop_id == wid).first()
                            if mod_row and not mod_row.name:
                                mod_row.name = mod_info.title
                                db.commit()
                        finally:
                            db.close()
                    applied += 1
                    items[wid] = {"ok": True}
                    _append_console_log(
                        server.id,
                        f"[MSM] CurseForge Mod {mod_info.title} ({dest_file_name}) heruntergeladen nach {target_dir_rel}/\n",
                    )
                except Exception as exc:
                    errors.append(f"{wid}: {exc}")
                    items[wid] = {"ok": False, "error": str(exc)}
            return {
                "ok": len(errors) == 0,
                "applied": applied,
                "errors": errors,
                "items": items,
                "error": "; ".join(errors) if errors else None,
            }

        res = asyncio.run(_download_cf())
        self.update_modlist(server)
        return res

    def install_mods(self, server, workshop_ids: list[str]) -> dict:
        bp_mods = self._blueprint.effective_mods()
        if not bp_mods.supportsMods:
            return {"error": "Mods nicht in dieser Blueprint aktiviert"}
        if bp_mods.supportsCurseForge:
            return self._install_curseforge_mods(server, workshop_ids)
        if not bp_mods.supportsSteamWorkshop or not bp_mods.workshopAppId:
            return {"error": "Steam Workshop nicht in dieser Blueprint aktiviert"}
        workshop_app_id = bp_mods.workshopAppId
        server_id = server.id
        install_dir = server.install_dir
        clean_ids = [str(wid).strip() for wid in workshop_ids if str(wid).strip()]
        if not clean_ids:
            return {"ok": True, "applied": 0, "items": {}}
        requires_login = False
        if self._blueprint.source.type == BlueprintSourceType.STEAM and self._blueprint.source.steam:
            requires_login = self._blueprint.source.steam.requiresLogin

        chunks = [clean_ids[i:i + WORKSHOP_BATCH_SIZE] for i in range(0, len(clean_ids), WORKSHOP_BATCH_SIZE)]

        result: dict = {}

        def _install():
            nonlocal result
            try:
                aggregated_items: dict = {}
                aggregated_errors: list[str] = []
                aggregated_applied = 0
                for chunk in chunks:
                    download_res = run_steamcmd_workshop_download_batch(
                        server_id=server_id,
                        install_dir=install_dir,
                        workshop_app_id=workshop_app_id,
                        workshop_item_ids=chunk,
                        use_authenticated_login=requires_login,
                        # use dedicated tool image, not the runtime one
                        node=getattr(server, "node", None),
                    )
                    items = download_res.get("items") if isinstance(download_res, dict) else {}
                    aggregated_items.update(items if isinstance(items, dict) else {})
                    for wid in chunk:
                        item = items.get(wid, {}) if isinstance(items, dict) else {}
                        if not item.get("ok"):
                            aggregated_errors.append(
                                f"{wid}: {item.get('error') or download_res.get('error') or 'Workshop-Download fehlgeschlagen'}"
                            )
                            continue
                        action_res = self._run_workshop_post_install_actions(server, wid)
                        if "error" in action_res:
                            aggregated_errors.append(f"{wid}: {action_res['error']}")
                            continue
                        aggregated_applied += 1
                        _append_console_log(server.id, f"[MSM] Mod {wid} verarbeitet\n")
                self.update_modlist(server)
                result = {
                    "ok": len(aggregated_errors) == 0,
                    "applied": aggregated_applied,
                    "errors": aggregated_errors,
                    "items": aggregated_items,
                }
                if aggregated_errors:
                    result["error"] = "; ".join(aggregated_errors)
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
        node = getattr(server, "node", None)
        if node is not None and not getattr(node, "is_local", False):
            from services.node_client import NodeClient

            client = NodeClient.from_node(node)
            for mod in mods:
                result = client.files_workshop(
                    server.id,
                    self._workshop_agent_payload(str(mod.workshop_id), mode="inspect"),
                )
                lines.extend(str(name) for name in result.get("target_basenames", []) if name)
            if self._blueprint.meta.id == "conan_exiles_ue5":
                return [f"*{line}" if line and not line.startswith("*") else line for line in lines]
            return lines

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
        if self._blueprint.meta.id == "conan_exiles_ue5":
            return [f"*{line}" if line and not line.startswith("*") else line for line in lines]
        return lines

    def _run_workshop_post_install_actions(self, server, workshop_id: str) -> dict:
        bp_mods = self._blueprint.effective_mods()
        if not bp_mods.postInstall:
            return {}

        node = getattr(server, "node", None)
        if node is not None and not getattr(node, "is_local", False):
            from services.node_client import NodeClient

            return NodeClient.from_node(node).files_workshop(
                server.id,
                self._workshop_agent_payload(workshop_id, mode="apply"),
            )

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
                    if target_rel.replace("\\", "/").startswith("ConanSandbox/Mods/"):
                        purged = _purge_conan_extracted_mod_cache(base, source.name)
                        if purged:
                            _append_console_log(
                                server.id,
                                f"[MSM] Mod {workshop_id}: ExtractedMods-Cache geleert "
                                f"({len(purged)} Datei(en)) vor Pak-Kopie.\n",
                            )
                    try:
                        shutil.copy2(source, target)
                    except PermissionError:
                        # Rootless Docker + Bind-Mount: copy2/copystat kann EPERM werfen,
                        # obwohl die Datei bereits kopiert wurde oder copyfile reicht.
                        shutil.copyfile(source, target)
                    self._try_chown_install_path(server, target)
                    continue

                # Wichtig: unresolved_target (NICHT target.resolve()) fuer
                # Existenz-/Symlink-Checks. Path.resolve() folgt Symlinks,
                # d. h. target.is_symlink() waere auf dem aufgeloesten Pfad
                # immer False, selbst wenn der Pfad ein Symlink ist.
                # Konsequenz (vorheriger Bug): bei jedem Reinstall einer
                # bereits installierten Mod ist der Postinstall-Symlink noch
                # da, und der Code ist faelschlich in den "Ziel existiert
                # bereits"-Zweig gelaufen → Install-Fehler trotz erfolgreichem
                # SteamCMD-Download. Mit unresolved_target wird der bestehende
                # Symlink korrekt unlinkt und neu angelegt.
                unresolved_target = base / target_rel
                if unresolved_target.is_symlink():
                    unresolved_target.unlink()
                elif unresolved_target.exists():
                    return {"error": f"postInstall-Ziel existiert bereits: {target_rel}"}
                os.symlink(source, unresolved_target, target_is_directory=source.is_dir())

        return {}

    def _try_chown_install_path(self, server, path: Path) -> None:
        """Setzt Container-UID/GID auf kopierte Dateien (best effort)."""
        try:
            uid, gid = self.container_uid_gid(server)
            os.chown(path, int(uid), int(gid))
        except (AttributeError, OSError):
            pass

    def workshop_runtime_targets_ready(self, server, workshop_id: str) -> bool:
        """True wenn postInstall-Ziele (z. B. ConanSandbox/Mods/*.pak) existieren."""
        bp_mods = self._blueprint.effective_mods()
        if not bp_mods.postInstall:
            return True
        node = getattr(server, "node", None)
        if node is not None and not getattr(node, "is_local", False):
            from services.node_client import NodeClient

            result = NodeClient.from_node(node).files_workshop(
                server.id,
                self._workshop_agent_payload(workshop_id, mode="inspect"),
            )
            return bool(result.get("ready"))
        base = Path(server.install_dir).resolve()
        for action in bp_mods.postInstall:
            sources = self._resolve_workshop_sources(base, action.source, workshop_id)
            if action.required and not sources:
                return False
            for source in sources:
                target_rel = self._render_workshop_path(
                    action.target,
                    workshop_id,
                    basename=source.name,
                )
                target = base / target_rel
                if not target.exists() and not target.is_symlink():
                    return False
        return True

    def sync_workshop_runtime_artifacts(self, server, workshop_ids: list[str] | None = None) -> dict:
        """Kopiert/symlinkt Workshop-Dateien nach postInstall-Zielen ohne erneuten Steam-Download."""
        bp_mods = self._blueprint.effective_mods()
        if not bp_mods.postInstall:
            return {"ok": True, "synced": 0, "errors": []}

        from database import SessionLocal
        from models import Mod

        db = SessionLocal()
        try:
            if workshop_ids is None:
                rows = (
                    db.query(Mod.workshop_id)
                    .filter(Mod.server_id == server.id, Mod.enabled == True)  # noqa: E712
                    .all()
                )
                workshop_ids = [str(r[0]) for r in rows if r[0]]
        finally:
            db.close()

        synced = 0
        errors: list[str] = []
        for wid in workshop_ids:
            wid = str(wid).strip()
            if not wid:
                continue
            res = self._run_workshop_post_install_actions(server, wid)
            if res.get("error"):
                errors.append(f"{wid}: {res['error']}")
            else:
                synced += 1
        self.update_modlist(server)
        return {"ok": not errors, "synced": synced, "errors": errors}

    def update_modlist(self, server) -> None:
        """Aktualisiert Mod-Status (z. B. .disabled für dateibasierte CurseForge-Mods oder Modlist-Dateien)."""
        bp_mods = self._blueprint.effective_mods()
        if bp_mods.supportsCurseForge and bp_mods.modInjection != "startupArg":
            target_dir_rel = bp_mods.curseforgeInstallPath or "mods"
            from database import SessionLocal
            from models import Mod
            db = SessionLocal()
            try:
                mods = db.query(Mod).filter(Mod.server_id == server.id).all()
                base = Path(server.install_dir).resolve()
                target_dir = (base / target_dir_rel).resolve()
                try:
                    target_dir.relative_to(base)
                    if target_dir.exists() and target_dir.is_dir():
                        for mod in mods:
                            wid = str(mod.workshop_id)
                            matches = list(target_dir.glob(f"*{wid}*"))
                            for p in matches:
                                if not p.is_file():
                                    continue
                                if mod.enabled and p.name.endswith(".disabled"):
                                    new_name = p.name[:-9]
                                    p.rename(p.parent / new_name)
                                elif not mod.enabled and not p.name.endswith(".disabled"):
                                    p.rename(p.parent / f"{p.name}.disabled")
                except Exception as exc:
                    logger.warning("Fehler beim Synchronisieren des CurseForge-Mod-Status für Server %s: %s", server.id, exc)
            finally:
                db.close()
            return

        super().update_modlist(server)

    def cleanup_mod(self, server, workshop_id: str) -> dict:
        bp_mods = self._blueprint.effective_mods()
        if bp_mods.supportsCurseForge:
            target_dir_rel = bp_mods.curseforgeInstallPath or "mods"
            if bp_mods.modInjection != "startupArg":
                base = Path(server.install_dir).resolve()
                target_dir = (base / target_dir_rel).resolve()
                try:
                    target_dir.relative_to(base)
                    if target_dir.exists() and target_dir.is_dir():
                        for p in target_dir.glob(f"*{workshop_id}*"):
                            if p.is_file():
                                p.unlink()
                except Exception:
                    pass
            _append_console_log(server.id, f"[MSM] CurseForge Mod {workshop_id} entfernt\n")
            return {"ok": True, "removed": [workshop_id]}

        if not bp_mods.supportsSteamWorkshop or not bp_mods.workshopAppId:
            return {"ok": True, "removed": []}

        node = getattr(server, "node", None)
        if node is not None and not getattr(node, "is_local", False):
            from services.node_client import NodeClient

            return NodeClient.from_node(node).files_workshop(
                server.id,
                self._workshop_agent_payload(workshop_id, mode="cleanup"),
            )

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

        # Staging-Bereich der laufenden/letzten Downloads ebenfalls bereinigen.
        # Vorherige (teilweise) Downloads können hier "stale" Dateien hinterlassen,
        # die SteamCMD bei Re-Installs verwirren und zu "Workshop-Download nicht
        # verifiziert" trotz erfolgreichem Container-Lauf führen. Per-Item, ohne
        # Seiteneffekte auf andere Workshop-Mods derselben App.
        downloads_cache = (
            base
            / "steamapps"
            / "workshop"
            / "downloads"
            / (bp_mods.workshopAppId or "")
            / workshop_id
        )
        if downloads_cache.exists() or downloads_cache.is_symlink():
            _safe_remove(downloads_cache)

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

    def _workshop_agent_payload(self, workshop_id: str, *, mode: str) -> dict:
        bp_mods = self._blueprint.effective_mods()
        return {
            "workshop_app_id": bp_mods.workshopAppId or "",
            "workshop_id": str(workshop_id),
            "mode": mode,
            "actions": [
                {
                    "operation": action.operation.value,
                    "source": self._render_workshop_path(action.source, str(workshop_id)),
                    "target": self._render_workshop_path(action.target, str(workshop_id)),
                    "required": action.required,
                }
                for action in bp_mods.postInstall
            ],
        }

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
