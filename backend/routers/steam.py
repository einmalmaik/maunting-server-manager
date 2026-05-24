"""
Steam Workshop API Router

Provides endpoints for mod search and details without requiring Steam API key.
Uses public Steam Community endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from database import get_db
from models import Server, User
from dependencies import get_current_user, require_server_permission
from services.steam_service import get_steam_service, SteamModInfo
from games import get_plugin

router = APIRouter(prefix="/api/steam", tags=["steam"])





@router.get("/workshop/search")
async def search_workshop_mods(
    server_id: int,
    query: str = Query("", description="Suchbegriff"),
    page: int = Query(1, ge=1, description="Seitennummer"),
    per_page: int = Query(20, ge=1, le=50, description="Ergebnisse pro Seite"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
) -> List[dict]:
    """Search workshop mods for server's game."""
    require_server_permission(user, server_id, db, "server.mods.read")
    
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    
    plugin = get_plugin(server.game_type)
    if not plugin or not plugin.supports_mods:
        raise HTTPException(status_code=400, detail="Spiel unterstützt keine Mods")
    
    mod_support = plugin.get_mod_support()
    if not mod_support:
        raise HTTPException(status_code=400, detail="Mod-Informationen nicht verfügbar")
    
    workshop_id = mod_support["workshop_id"]
    required_tags = mod_support.get("required_tags", [])
    
    try:
        steam_service = await get_steam_service()
        mods = await steam_service.search_workshop_mods(
            appid=workshop_id,
            query=query,
            page=page,
            per_page=per_page,
            required_tags=required_tags if required_tags else None
        )
        
        return [_mod_to_dict(mod) for mod in mods]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Suche fehlgeschlagen: {str(e)}")


@router.get("/workshop/popular")
async def get_popular_mods(
    server_id: int,
    limit: int = Query(20, ge=1, le=50, description="Anzahl der Mods"),
    sort: str = Query("trending", description="Sortierung: trending | popular | newest | updated"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
) -> List[dict]:
    """Get workshop mods for server's game, sorted by the given criteria."""
    require_server_permission(user, server_id, db, "server.mods.read")
    
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    
    plugin = get_plugin(server.game_type)
    if not plugin or not plugin.supports_mods:
        raise HTTPException(status_code=400, detail="Spiel unterstützt keine Mods")
    
    mod_support = plugin.get_mod_support()
    if not mod_support:
        raise HTTPException(status_code=400, detail="Mod-Informationen nicht verfügbar")
    
    workshop_id = mod_support["workshop_id"]
    required_tags = mod_support.get("required_tags", [])
    
    if sort not in ("trending", "popular", "newest", "updated"):
        sort = "trending"

    try:
        steam_service = await get_steam_service()
        mods = await steam_service.get_popular_mods(
            appid=workshop_id,
            limit=limit,
            required_tags=required_tags if required_tags else None,
            sort=sort,
        )
        
        return [_mod_to_dict(mod) for mod in mods]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Laden fehlgeschlagen: {str(e)}")


@router.get("/workshop/mod/{publishedfileid}")
async def get_mod_details(
    server_id: int,
    publishedfileid: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
) -> dict:
    """Get detailed information for a specific workshop mod."""
    require_server_permission(user, server_id, db, "server.mods.read")
    
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    
    plugin = get_plugin(server.game_type)
    if not plugin or not plugin.supports_mods:
        raise HTTPException(status_code=400, detail="Spiel unterstützt keine Mods")
    
    mod_support = plugin.get_mod_support()
    if not mod_support:
        raise HTTPException(status_code=400, detail="Mod-Informationen nicht verfügbar")
    
    workshop_id = mod_support["workshop_id"]
    
    try:
        steam_service = await get_steam_service()
        mod = await steam_service.get_mod_details(
            appid=workshop_id,
            publishedfileid=publishedfileid
        )
        
        if not mod:
            raise HTTPException(status_code=404, detail="Mod nicht gefunden")
        
        return _mod_to_dict(mod)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Laden fehlgeschlagen: {str(e)}")


def _mod_to_dict(mod: SteamModInfo) -> dict:
    """Convert SteamModInfo to dict for JSON response."""
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
    }