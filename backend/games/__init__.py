"""Game-Plugin-Registry — alle Server-Typen laufen ueber Blueprints.

Native Unterstuetzung bedeutet: MSM liefert eine Blueprint-Datei unter
``backend/blueprints/native`` mit. Community-Server nutzen dieselbe Runtime
ueber importierte Blueprints. Es gibt keine spiel-spezifischen Python-Plugins
mehr als bevorzugte Ausfuehrungsschicht.
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
    run_steamcmd_workshop_download_batch,
    write_workshop_modlist,
)
from blueprints.schema import BlueprintSourceType
from .blueprint_plugin import BlueprintPlugin
from services.port_role_service import blueprint_port_requirements


PLUGINS: dict[str, type[GamePlugin]] = {}


def get_plugin(game_type: str) -> GamePlugin | None:
    """Liefert den generischen BlueprintPlugin fuer ``game_type`` oder ``None``."""
    from blueprints import get_registry
    entry = get_registry().get(game_type)
    if entry is None:
        return None
    return BlueprintPlugin(entry.blueprint)


def list_game_info() -> list[dict]:
    """Liefert die UI-Liste aller Server-Typen aus der Blueprint-Registry.

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

    for entry in get_registry().list():
        bp = entry.blueprint
        if bp.meta.id in seen:
            # Konflikte loggt die Registry bereits.
            continue
        bp_mods = bp.effective_mods()
        port_requirements = blueprint_port_requirements(bp.ports)
        src_type = bp.source.type
        supports_file_updates = src_type in (
            BlueprintSourceType.STEAM,
            BlueprintSourceType.HTTP,
            BlueprintSourceType.GITHUB,
        )
        out.append({
            "id": bp.meta.id,
            "name": bp.meta.name,
            "platform": "linux",
            "category": bp.meta.category.value,
            "mod_support": bp_mods.supportsMods,
            "supports_steam_workshop": bp_mods.supportsSteamWorkshop,
            "supports_server_file_updates": supports_file_updates,
            # v1.4.7+: Exec-Tab-Opt-in pro Blueprint. UI nutzt das, um den
            # Tab nur dann anzuzeigen, wenn der Server-Blueprint das Feature
            # aktiviert hat (Default: False).
            "enable_exec": bool(getattr(bp.runtime, "enableExec", False)),
            "ports": [
                {"name": p.name.value, "protocol": p.protocol.value, "role": role}
                for p, (role, _protocol) in zip(bp.ports, port_requirements)
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
    "_append_console_log",
    "container_name_for",
    "active_mod_ids",
    "run_steamcmd_install",
    "run_steamcmd_workshop_download",
    "run_steamcmd_workshop_download_batch",
    "write_workshop_modlist",
]
