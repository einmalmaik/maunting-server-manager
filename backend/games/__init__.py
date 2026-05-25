"""Game-Plugin-Registry — native Klassen plus Community-Blueprints.

Native Plugins decken die "schnellen, getesteten" Server-Typen (derzeit DayZ
und Conan Exiles UE5). Community-Blueprints werden ueber die
``BlueprintRegistry`` geladen und ueber ``BlueprintPlugin`` instanziiert.

``get_plugin(game_type)`` versucht zuerst die native Map und faellt sonst auf
einen Blueprint-Lookup zurueck.
"""

from __future__ import annotations

from .base import (
    ConfigField,
    GamePlugin,
    ServerStatus,
    _append_console_log,
    active_mod_ids,
    container_name_for,
    run_steamcmd_install,
    run_steamcmd_workshop_download,
    write_workshop_modlist,
)
from .blueprint_plugin import BlueprintPlugin
from .conan_exiles_ue5.plugin import ConanExilesUE5Plugin
from .dayz.plugin import DayZPlugin


# Native (im Repo committed) — Vorrang bei ID-Konflikt mit Community.
NATIVE_PLUGINS: dict[str, type[GamePlugin]] = {
    "conan_exiles_ue5": ConanExilesUE5Plugin,
    "dayz": DayZPlugin,
}

# Backward-Compat: alter Name wurde im Code mehrfach referenziert.
PLUGINS = NATIVE_PLUGINS


def get_plugin(game_type: str) -> GamePlugin | None:
    """Liefert eine Plugin-Instanz fuer ``game_type`` oder ``None``.

    Reihenfolge:
    1. native Klasse (DayZ / Conan)
    2. Community-Blueprint via :class:`BlueprintRegistry`
    """
    plugin_cls = NATIVE_PLUGINS.get(game_type)
    if plugin_cls:
        return plugin_cls()
    # Lokaler Import vermeidet zirkulaere Importe waehrend des Modul-Loads.
    from blueprints import get_registry
    entry = get_registry().get(game_type)
    if entry is None:
        return None
    return BlueprintPlugin(entry.blueprint)


def list_game_info() -> list[dict]:
    """Liefert die UI-Liste aller Server-Typen (native + Community).

    Jeder Eintrag enthaelt:
    - ``id``, ``name``, ``platform``
    - ``category`` (Blueprint-Kategorie, ``steam_game`` etc.)
    - ``mod_support`` / ``supports_steam_workshop`` (UI-Gates)
    - ``ports``: Liste der Port-Rollen ``{name, protocol}`` aus der Blueprint —
      das Frontend rendert daraus die Port-Felder beim Erstellen.
    - ``source``: ``native`` oder ``community`` (UI-Hinweis)
    """
    from blueprints import get_registry
    out: list[dict] = []
    seen: set[str] = set()

    # Native zuerst, damit ihre Kategorie/Ports aus der Native-Blueprint kommen.
    for game_id, plugin_cls in NATIVE_PLUGINS.items():
        plugin = plugin_cls()
        bp = plugin.get_blueprint()
        out.append({
            "id": game_id,
            "name": plugin.game_name,
            "platform": "linux",
            "category": bp.meta.category.value if bp is not None else "steam_game",
            "mod_support": bool(plugin.supports_mods),
            "supports_steam_workshop": bool(plugin.supports_steam_workshop),
            "ports": [
                {"name": p.name.value, "protocol": p.protocol.value}
                for p in (bp.ports if bp is not None else [])
            ],
            "source": "native",
        })
        seen.add(game_id)

    for entry in get_registry().list():
        bp = entry.blueprint
        if bp.meta.id in seen:
            # Native hat Vorrang — Konflikt loggen wir bereits in der Registry.
            continue
        bp_mods = bp.effective_mods()
        out.append({
            "id": bp.meta.id,
            "name": bp.meta.name,
            "platform": "linux",
            "category": bp.meta.category.value,
            "mod_support": bp_mods.supportsMods,
            "supports_steam_workshop": bp_mods.supportsSteamWorkshop,
            "ports": [
                {"name": p.name.value, "protocol": p.protocol.value}
                for p in bp.ports
            ],
            "source": entry.origin.value,
        })

    return out


__all__ = [
    "GamePlugin",
    "ServerStatus",
    "ConfigField",
    "BlueprintPlugin",
    "get_plugin",
    "list_game_info",
    "PLUGINS",
    "NATIVE_PLUGINS",
    "_append_console_log",
    "container_name_for",
    "active_mod_ids",
    "run_steamcmd_install",
    "run_steamcmd_workshop_download",
    "write_workshop_modlist",
]
