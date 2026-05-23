from .base import (
    ConfigField,
    GamePlugin,
    ServerStatus,
    _append_console_log,
    container_name_for,
    query_a2s_info,
    run_steamcmd_install,
    run_steamcmd_workshop_download,
)
from .conan_exiles_ue5.plugin import ConanExilesUE5Plugin
from .dayz.plugin import DayZPlugin

PLUGINS: dict[str, type[GamePlugin]] = {
    "conan_exiles_ue5": ConanExilesUE5Plugin,
    "dayz": DayZPlugin,
}


def get_plugin(game_type: str) -> GamePlugin | None:
    plugin_cls = PLUGINS.get(game_type)
    if plugin_cls:
        return plugin_cls()
    return None


__all__ = [
    "GamePlugin",
    "ServerStatus",
    "ConfigField",
    "get_plugin",
    "PLUGINS",
    "_append_console_log",
    "container_name_for",
    "query_a2s_info",
    "run_steamcmd_install",
    "run_steamcmd_workshop_download",
]
