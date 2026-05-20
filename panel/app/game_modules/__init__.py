"""GameModule plugin interface for Maunting Server Manager.

Each supported game lives in a sub-package and implements the GameModule
protocol. The core panel is game-agnostic and delegates all game-specific
operations to the active module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class PortConfig:
    name: str
    port: int
    protocol: str = "udp"
    description: str = ""


@dataclass(frozen=True)
class GameManifest:
    id: str
    name: str
    short_name: str
    steam_app_id: int | None = None
    steamcmd_app_id: int | None = None
    supports_mods: bool = False
    mod_system: str | None = None  # e.g. "workshop", "steam", "custom"
    supports_wine: bool = False
    default_ports: list[PortConfig] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    save_paths: list[str] = field(default_factory=list)
    executable_name: str = ""
    install_method: str = "steamcmd"


@dataclass
class TaskResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerStatus:
    installed: bool
    running: bool
    version: str = ""
    player_count: int = 0
    max_players: int = 0
    uptime_seconds: int = 0
    ports: list[PortConfig] = field(default_factory=list)


class GameModule(Protocol):
    """Protocol that every game module must satisfy."""

    manifest: GameManifest

    # -- Installation & Lifecycle --
    def install(self, server_dir: Path, server_name: str) -> TaskResult: ...
    def update(self, server_dir: Path, server_name: str) -> TaskResult: ...
    def validate(self, server_dir: Path, server_name: str) -> TaskResult: ...
    def start(self, server_dir: Path, server_name: str) -> TaskResult: ...
    def stop(self, server_dir: Path, server_name: str) -> TaskResult: ...
    def restart(self, server_dir: Path, server_name: str) -> TaskResult: ...

    # -- Queries --
    def get_status(self, server_dir: Path, server_name: str) -> ServerStatus: ...
    def get_ports(self, server_dir: Path, server_name: str) -> list[PortConfig]: ...
    def get_config_files(self, server_dir: Path) -> list[Path]: ...

    # -- Data management --
    def wipe(self, server_dir: Path, server_name: str, scope: str) -> TaskResult: ...
    def backup(self, server_dir: Path, server_name: str) -> TaskResult: ...
    def restore(self, server_dir: Path, server_name: str, backup_name: str) -> TaskResult: ...

    # -- Mods (optional) --
    def get_mods(self, server_dir: Path, server_name: str) -> list[dict[str, Any]]: ...
    def add_mod(self, server_dir: Path, server_name: str, mod_id: str, mod_name: str) -> TaskResult: ...
    def remove_mod(self, server_dir: Path, server_name: str, mod_id: str) -> TaskResult: ...
    def update_mods(self, server_dir: Path, server_name: str) -> TaskResult: ...

    # -- Config --
    def read_config(self, server_dir: Path, config_name: str) -> dict[str, Any]: ...
    def write_config(self, server_dir: Path, config_name: str, data: dict[str, Any]) -> TaskResult: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_MODULES: dict[str, GameModule] = {}


def register_module(module: GameModule) -> None:
    _MODULES[module.manifest.id] = module


def get_module(game_id: str) -> GameModule:
    if game_id not in _MODULES:
        raise ValueError(f"Unknown game module: {game_id!r}")
    return _MODULES[game_id]


def list_modules() -> list[GameModule]:
    return list(_MODULES.values())


def load_all_modules() -> None:
    """Auto-discover and register modules in this package."""
    import importlib
    import pkgutil

    for _, modname, ispkg in pkgutil.iter_modules(__path__):
        if ispkg:
            mod = importlib.import_module(f"{__name__}.{modname}")
            # Modules self-register via register_module() on import.
            # If a package exposes a `module` attribute, prefer it.
            if hasattr(mod, "module"):
                register_module(mod.module)
