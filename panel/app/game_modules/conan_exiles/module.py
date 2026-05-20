"""Conan Exiles UE5 Linux Native game module implementation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.shell import (
    fetch_action_log,
    fetch_action_task,
    get_server_dir,
    invoke_core_action,
    invoke_core_action_async,
    PanelCommandError,
    run_game_command,
    run_manager_command,
)
from app.game_modules import (
    GameManifest,
    GameModule,
    PortConfig,
    ServerStatus,
    TaskResult,
    register_module,
)
from app.config import get_settings

_MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"
_manifest_raw = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
_manifest_raw["default_ports"] = [PortConfig(**p) for p in _manifest_raw.get("default_ports", [])]
_MANIFEST = GameManifest(**_manifest_raw)


def _manager_path() -> str:
    return get_settings().conan_manager_path


class ConanExilesModule:
    manifest = _MANIFEST

    # -- Installation & Lifecycle --
    def install(self, server_dir: Path, server_name: str) -> TaskResult:
        try:
            invoke_core_action("install", server_name=server_name)
            return TaskResult(ok=True, message="Installation started.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def update(self, server_dir: Path, server_name: str) -> TaskResult:
        try:
            invoke_core_action("update", server_name=server_name)
            return TaskResult(ok=True, message="Update started.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def validate(self, server_dir: Path, server_name: str) -> TaskResult:
        try:
            invoke_core_action("validate", server_name=server_name)
            return TaskResult(ok=True, message="Validate started.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def start(self, server_dir: Path, server_name: str) -> TaskResult:
        try:
            invoke_core_action("start", server_name=server_name)
            return TaskResult(ok=True, message="Server start triggered.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def stop(self, server_dir: Path, server_name: str) -> TaskResult:
        try:
            invoke_core_action("stop", server_name=server_name)
            return TaskResult(ok=True, message="Server stop triggered.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def restart(self, server_dir: Path, server_name: str) -> TaskResult:
        try:
            invoke_core_action("restart", server_name=server_name)
            return TaskResult(ok=True, message="Server restart triggered.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    # -- Queries --
    def get_status(self, server_dir: Path, server_name: str) -> ServerStatus:
        try:
            data = run_game_command(
                _manager_path(),
                "panel", "bridge", "status",
                server_name=server_name,
                expect_json=True,
            )
            return ServerStatus(
                installed=data.get("server_installed", False),
                running=data.get("server_running", False),
                ports=[
                    PortConfig(name="game", port=data.get("port", 7777), protocol="udp"),
                    PortConfig(name="query", port=data.get("queryport", 27015), protocol="udp"),
                    PortConfig(name="rcon", port=data.get("rconport", 25575), protocol="tcp"),
                ],
            )
        except Exception:
            return ServerStatus(installed=False, running=False)

    def get_ports(self, server_dir: Path, server_name: str) -> list[PortConfig]:
        try:
            data = run_game_command(
                _manager_path(),
                "panel", "bridge", "status",
                server_name=server_name,
                expect_json=True,
            )
            return [
                PortConfig(name="game", port=data.get("port", 7777), protocol="udp"),
                PortConfig(name="query", port=data.get("queryport", 27015), protocol="udp"),
                PortConfig(name="rcon", port=data.get("rconport", 25575), protocol="tcp"),
            ]
        except Exception:
            return list(_MANIFEST.default_ports)

    def get_config_files(self, server_dir: Path) -> list[Path]:
        return [server_dir / p for p in _MANIFEST.config_files]

    # -- Data management --
    def wipe(self, server_dir: Path, server_name: str, scope: str) -> TaskResult:
        try:
            invoke_core_action("wipe", scope, server_name=server_name)
            return TaskResult(ok=True, message="Wipe completed.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def backup(self, server_dir: Path, server_name: str) -> TaskResult:
        try:
            invoke_core_action("backup", "create", server_name=server_name)
            return TaskResult(ok=True, message="Backup started.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def restore(self, server_dir: Path, server_name: str, backup_name: str) -> TaskResult:
        try:
            invoke_core_action("backup", "restore", backup_name, server_name=server_name)
            return TaskResult(ok=True, message="Restore started.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    # -- Mods --
    def get_mods(self, server_dir: Path, server_name: str) -> list[dict[str, Any]]:
        try:
            data = run_game_command(
                _manager_path(),
                "panel", "bridge", "mods", "list",
                server_name=server_name,
                expect_json=True,
            )
            return data.get("mods", [])
        except Exception:
            return []

    def add_mod(self, server_dir: Path, server_name: str, mod_id: str, mod_name: str) -> TaskResult:
        try:
            run_game_command(
                _manager_path(),
                "panel", "bridge", "mods", "add", mod_id, mod_name,
                server_name=server_name,
                expect_json=True,
            )
            return TaskResult(ok=True, message=f"Mod {mod_name} added.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def remove_mod(self, server_dir: Path, server_name: str, mod_id: str) -> TaskResult:
        try:
            run_game_command(
                _manager_path(),
                "panel", "bridge", "mods", "remove", mod_id,
                server_name=server_name,
                expect_json=True,
            )
            return TaskResult(ok=True, message=f"Mod {mod_id} removed.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    def update_mods(self, server_dir: Path, server_name: str) -> TaskResult:
        try:
            run_game_command(
                _manager_path(),
                "workshop", "update",
                server_name=server_name,
                expect_json=False,
            )
            return TaskResult(ok=True, message="Mods update started.")
        except PanelCommandError as exc:
            return TaskResult(ok=False, message=str(exc))

    # -- Config --
    def read_config(self, server_dir: Path, config_name: str) -> dict[str, Any]:
        path = server_dir / config_name
        if not path.exists():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
            return {"raw": text}
        except Exception:
            return {}

    def write_config(self, server_dir: Path, config_name: str, data: dict[str, Any]) -> TaskResult:
        path = server_dir / config_name
        try:
            raw = data.get("raw", "")
            path.write_text(raw, encoding="utf-8")
            return TaskResult(ok=True, message=f"{config_name} saved.")
        except Exception as exc:
            return TaskResult(ok=False, message=str(exc))


module = ConanExilesModule()
register_module(module)
