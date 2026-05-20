"""API endpoints for game modules."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..game_modules import list_modules

router = APIRouter()


@router.get("/games")
def list_games() -> Any:
    return {
        "games": [
            {
                "id": m.manifest.id,
                "name": m.manifest.name,
                "short_name": m.manifest.short_name,
                "supports_mods": m.manifest.supports_mods,
                "mod_system": m.manifest.mod_system,
                "default_ports": [
                    {"name": p.name, "port": p.port, "protocol": p.protocol}
                    for p in m.manifest.default_ports
                ],
            }
            for m in list_modules()
        ]
    }
