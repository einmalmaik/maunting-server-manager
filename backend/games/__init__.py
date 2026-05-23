from .base import GamePlugin, ServerStatus, ConfigField, _run_install_with_logging
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


__all__ = ["GamePlugin", "ServerStatus", "ConfigField", "get_plugin", "PLUGINS", "_run_install_with_logging"]
