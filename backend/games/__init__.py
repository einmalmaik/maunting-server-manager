from .base import (
    ConfigField,
    GamePlugin,
    ServerStatus,
    _append_console_log,
    container_name_for,
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


def list_game_info() -> list[dict]:
    """Liefert die UI-Liste der unterstuetzten Spiele dynamisch aus den Plugins.

    KISS: ein flaches dict pro Spiel, identisch zu dem, was das alte
    hartcodierte `/api/system/games` lieferte, plus das neue
    `supports_steam_workshop`-Flag. Frontend liest beides direkt.
    """
    out: list[dict] = []
    for game_id, plugin_cls in PLUGINS.items():
        plugin = plugin_cls()
        out.append({
            "id": game_id,
            "name": plugin.game_name,
            "platform": "linux",
            "mod_support": bool(plugin.supports_mods),
            "supports_steam_workshop": bool(plugin.supports_steam_workshop),
        })
    return out


__all__ = [
    "GamePlugin",
    "ServerStatus",
    "ConfigField",
    "get_plugin",
    "list_game_info",
    "PLUGINS",
    "_append_console_log",
    "container_name_for",
    "run_steamcmd_install",
    "run_steamcmd_workshop_download",
]
