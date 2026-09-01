"""CurseForge API Router.

Bietet Endpunkte für Mod-Suche, Popular-Listen und Details über die offizielle
CurseForge API v1. Nutzt den vom Betreiber in den Panel-Einstellungen hinterlegten
API-Schlüssel.
"""

from __future__ import annotations

from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Server, User
from dependencies import get_current_user, require_server_permission
from services.curseforge_service import (
    CurseForgeApiUnavailable,
    CurseForgeModInfo,
    get_curseforge_service,
)
from games import get_plugin

router = APIRouter(prefix="/api/curseforge", tags=["curseforge"])


def _curseforge_api_error(exc: CurseForgeApiUnavailable) -> HTTPException:
    code = exc.code or "curseforge_api_unavailable"
    return HTTPException(
        status_code=503,
        detail={"code": code, "message": f"errors.{code}"},
    )


def _mod_to_dict(mod: CurseForgeModInfo) -> dict:
    """Konvertiert CurseForgeModInfo in das einheitliche ModBrowserItem-JSON."""
    return {
        "publishedfileid": mod.publishedfileid,
        "title": mod.title,
        "description": mod.description,
        "creator": mod.creator,
        "file_size": mod.file_size,
        "file_size_mb": round(mod.file_size / (1024 * 1024), 2) if mod.file_size > 0 else 0,
        "created": mod.created.isoformat(),
        "updated": mod.updated.isoformat(),
        "subscriptions": mod.subscriptions,
        "favorites": mod.favorites,
        "tags": mod.tags,
        "preview_url": mod.preview_url,
        "direct_url": mod.direct_url,
        "provider": "curseforge",
    }


def _detect_minecraft_modloader_and_version(server: Server, plugin: Any) -> tuple[int | None, str | None, str | None]:
    """Ermittelt mod_loader_type (1=Forge, 4=Fabric, 5=Quilt, 6=NeoForge), game_version und ggf. class_id."""
    gt = (server.game_type or "").lower()
    mod_loader_type: int | None = None
    override_class_id: str | None = None

    if "fabric" in gt:
        mod_loader_type = 4
    elif "neoforge" in gt:
        mod_loader_type = 6
    elif "forge" in gt:
        mod_loader_type = 1
    elif "quilt" in gt:
        mod_loader_type = 5
    elif any(k in gt for k in ("paper", "spigot", "purpur", "bukkit")):
        override_class_id = "12"

    game_version: str | None = None
    bp = getattr(plugin, "blueprint", None)
    if bp and hasattr(bp, "runtime") and hasattr(bp.runtime, "env"):
        v = str(bp.runtime.env.get("VERSION", "")).strip()
        if v and v.upper() != "LATEST":
            game_version = v

    return mod_loader_type, game_version, override_class_id


@router.get("/search")
async def search_curseforge_mods(
    server_id: int,
    query: str = Query("", description="Suchbegriff"),
    page: int = Query(1, ge=1, description="Seitennummer"),
    per_page: int = Query(24, ge=1, le=50, description="Ergebnisse pro Seite"),
    class_id: Optional[str] = Query(None, description="Optionaler CurseForge class_id Filter (z. B. '6' für Mods, '4471' für Modpacks)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[dict]:
    """Sucht CurseForge-Mods für das Spiel dieses Servers."""
    require_server_permission(user, server_id, db, "server.mods.read")

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    plugin = get_plugin(server.game_type)
    if not plugin or not plugin.supports_mods:
        raise HTTPException(status_code=400, detail="Spiel unterstützt keine Mods")

    mod_support = plugin.get_mod_support()
    if not mod_support or not mod_support.get("curseforge_game_id"):
        raise HTTPException(status_code=400, detail="CurseForge für dieses Spiel nicht konfiguriert")

    game_id = mod_support["curseforge_game_id"]
    if class_id is not None:
        resolved_class_id = None if class_id.strip().lower() in ("all", "0", "none", "") else class_id.strip()
    else:
        resolved_class_id = mod_support.get("curseforge_class_id")

    mod_loader_type = None
    game_version = None
    if str(game_id) == "432":
        auto_loader, auto_ver, auto_class = _detect_minecraft_modloader_and_version(server, plugin)
        mod_loader_type = auto_loader
        game_version = auto_ver
        if auto_class and resolved_class_id in (None, "6"):
            resolved_class_id = auto_class

    try:
        cf_service = await get_curseforge_service()
        mods = await cf_service.search_mods(
            game_id=game_id,
            query=query,
            page=page,
            per_page=per_page,
            class_id=resolved_class_id,
            mod_loader_type=mod_loader_type,
            game_version=game_version,
        )
        return [_mod_to_dict(mod) for mod in mods]
    except CurseForgeApiUnavailable as e:
        raise _curseforge_api_error(e)
    except Exception as e:
        raise HTTPException(status_code=500, detail="errors.curseforge_search_failed") from e


@router.get("/popular")
async def get_popular_mods(
    server_id: int,
    limit: int = Query(24, ge=1, le=50, description="Anzahl der Mods"),
    page: int = Query(1, ge=1, description="Seitennummer (Pagination)"),
    sort: str = Query("trending", description="Sortierung: trending | popular | newest | updated"),
    class_id: Optional[str] = Query(None, description="Optionaler CurseForge class_id Filter (z. B. '6' für Mods, '4471' für Modpacks)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[dict]:
    """Liefert CurseForge-Mods nach Sortierkriterium."""
    require_server_permission(user, server_id, db, "server.mods.read")

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    plugin = get_plugin(server.game_type)
    if not plugin or not plugin.supports_mods:
        raise HTTPException(status_code=400, detail="Spiel unterstützt keine Mods")

    mod_support = plugin.get_mod_support()
    if not mod_support or not mod_support.get("curseforge_game_id"):
        raise HTTPException(status_code=400, detail="CurseForge für dieses Spiel nicht konfiguriert")

    game_id = mod_support["curseforge_game_id"]
    if class_id is not None:
        resolved_class_id = None if class_id.strip().lower() in ("all", "0", "none", "") else class_id.strip()
    else:
        resolved_class_id = mod_support.get("curseforge_class_id")

    mod_loader_type = None
    game_version = None
    if str(game_id) == "432":
        auto_loader, auto_ver, auto_class = _detect_minecraft_modloader_and_version(server, plugin)
        mod_loader_type = auto_loader
        game_version = auto_ver
        if auto_class and resolved_class_id in (None, "6"):
            resolved_class_id = auto_class

    if sort not in ("trending", "popular", "newest", "updated"):
        sort = "trending"

    try:
        cf_service = await get_curseforge_service()
        mods = await cf_service.get_popular_mods(
            game_id=game_id,
            limit=limit,
            page=page,
            sort=sort,
            class_id=resolved_class_id,
            mod_loader_type=mod_loader_type,
            game_version=game_version,
        )
        return [_mod_to_dict(mod) for mod in mods]
    except CurseForgeApiUnavailable as e:
        raise _curseforge_api_error(e)
    except Exception as e:
        raise HTTPException(status_code=500, detail="errors.curseforge_load_failed") from e


@router.get("/mod/{mod_id}")
async def get_mod_details(
    server_id: int,
    mod_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Liefert Detailinformationen zu einer bestimmten CurseForge-Mod."""
    require_server_permission(user, server_id, db, "server.mods.read")

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    if not str(mod_id).strip().isdigit():
        raise HTTPException(status_code=400, detail={"code": "invalid_workshop_id", "message": "errors.invalid_workshop_id"})

    plugin = get_plugin(server.game_type)
    if not plugin or not plugin.supports_mods:
        raise HTTPException(status_code=400, detail="Spiel unterstützt keine Mods")

    try:
        cf_service = await get_curseforge_service()
        mod = await cf_service.get_mod_details(mod_id=mod_id)
        if not mod:
            raise HTTPException(status_code=404, detail="Mod nicht gefunden")
        return _mod_to_dict(mod)
    except HTTPException:
        raise
    except CurseForgeApiUnavailable as e:
        raise _curseforge_api_error(e)
    except Exception as e:
        raise HTTPException(status_code=500, detail="errors.curseforge_load_failed") from e
