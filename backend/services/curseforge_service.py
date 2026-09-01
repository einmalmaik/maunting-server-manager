"""CurseForge Web API Service für Mod-Suche, Metadaten und Downloads.

Nutzt die offizielle CurseForge for Studios / Core API (v1) mit x-api-key.
Cacht Antworten zur Entlastung von Rate-Limits.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class CurseForgeApiUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_game_id(game_id: int | str | None) -> int:
    """Parst numerische Game-IDs sicher. Fallback auf 432 wenn leer oder nicht-numerisch."""
    if not game_id:
        return 432
    if isinstance(game_id, int):
        return game_id
    raw = str(game_id).strip()
    if raw.isdigit():
        return int(raw)
    return 432


@dataclass
class CurseForgeModInfo:
    publishedfileid: str
    title: str
    description: str
    creator: str
    file_size: int
    created: datetime
    updated: datetime
    subscriptions: int
    favorites: int
    tags: List[str]
    preview_url: Optional[str] = None
    direct_url: str = ""
    main_file_id: Optional[int] = None
    latest_files: List[Dict[str, Any]] = field(default_factory=list)
    has_server_pack: bool = False
    server_pack_file_id: Optional[int] = None


class CurseForgeService:
    """CurseForge v1 API-Client."""

    API_BASE = "https://api.curseforge.com/v1"

    # ModsSearchSortField enum values:
    # 1 = Featured, 2 = Popularity, 3 = LastUpdated, 4 = Name, 5 = TotalDownloads
    SORT_FEATURED = 1
    SORT_POPULAR = 2
    SORT_UPDATED = 3
    SORT_NAME = 4
    SORT_TOTAL_DOWNLOADS = 5

    def __init__(self) -> None:
        from services.curseforge_api_key_service import resolve_key

        self.api_key = resolve_key()
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "MSM/1.0 (Maunting Service Manager)",
                "Accept": "application/json",
                "x-api-key": self.api_key or "",
            },
        )
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = timedelta(minutes=15)

    async def close(self) -> None:
        """HTTP-Client schließen."""
        await self.client.aclose()

    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache:
            return False
        cached_time = self._cache[key]["timestamp"]
        return datetime.now(timezone.utc) - cached_time < self._cache_ttl

    def _get_cache(self, key: str) -> Optional[Any]:
        if self._is_cache_valid(key):
            return self._cache[key]["data"]
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        self._cache[key] = {
            "data": data,
            "timestamp": datetime.now(timezone.utc),
        }

    def _parse_datetime(self, date_str: str | None) -> datetime:
        if not date_str:
            return datetime.now(timezone.utc)
        try:
            # ISO 8601 parsing (z.B. "2024-01-01T12:00:00Z" oder "+00:00")
            cleaned = date_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime.now(timezone.utc)

    def _parse_mod_data(self, item: dict) -> CurseForgeModInfo:
        mod_id = str(item.get("id") or "")
        title = item.get("name") or f"Mod {mod_id}"
        description = item.get("summary") or ""
        authors = item.get("authors") or []
        creator = authors[0].get("name") if authors else "Unknown"
        
        categories = item.get("categories") or []
        tags = [c.get("name") for c in categories if c.get("name")]

        logo = item.get("logo") or {}
        preview_url = logo.get("thumbnailUrl") or logo.get("url") or None

        links = item.get("links") or {}
        direct_url = links.get("websiteUrl") or f"https://www.curseforge.com"

        subscriptions = int(item.get("downloadCount") or 0)
        favorites = int(item.get("thumbsUpCount") or 0)

        created = self._parse_datetime(item.get("dateCreated"))
        updated = self._parse_datetime(item.get("dateModified"))

        latest_files = item.get("latestFiles") or []
        file_size = 0
        has_server_pack = False
        server_pack_file_id = None
        if latest_files:
            file_size = int(latest_files[0].get("fileLength") or 0)
            for f in latest_files:
                if isinstance(f, dict):
                    if f.get("isServerPack") is True:
                        has_server_pack = True
                        server_pack_file_id = f.get("id")
                        break
                    if f.get("serverPackFileId"):
                        has_server_pack = True
                        server_pack_file_id = f.get("serverPackFileId")
                        break
                    fname = str(f.get("fileName") or "").casefold()
                    if "server" in fname:
                        has_server_pack = True
                        server_pack_file_id = f.get("id")
                        break

        return CurseForgeModInfo(
            publishedfileid=mod_id,
            title=title,
            description=description,
            creator=creator,
            file_size=file_size,
            created=created,
            updated=updated,
            subscriptions=subscriptions,
            favorites=favorites,
            tags=tags,
            preview_url=preview_url,
            direct_url=direct_url,
            main_file_id=item.get("mainFileId"),
            latest_files=latest_files,
            has_server_pack=has_server_pack,
            server_pack_file_id=server_pack_file_id,
        )

    async def search_mods(
        self,
        game_id: int | str,
        query: str = "",
        page: int = 1,
        per_page: int = 24,
        class_id: int | str | None = None,
        category_id: int | str | None = None,
        sort_field: int | None = None,
        sort_order: str = "desc",
        mod_loader_type: int | None = None,
        game_version: str | None = None,
        slug: str | None = None,
    ) -> List[CurseForgeModInfo]:
        """Sucht Mods über GET /v1/mods/search."""
        if not self.api_key:
            raise CurseForgeApiUnavailable("curseforge_api_key_missing")

        page = max(1, page)
        per_page = max(1, min(50, per_page))
        index = (page - 1) * per_page

        norm_game_id = normalize_game_id(game_id)
        cache_key = f"cf_search_{norm_game_id}_{query}_{slug}_{page}_{per_page}_{class_id}_{category_id}_{sort_field}_{sort_order}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        params: dict[str, Any] = {
            "gameId": norm_game_id,
            "index": index,
            "pageSize": per_page,
            "sortOrder": sort_order,
        }
        if query:
            params["searchFilter"] = query
        if slug:
            params["slug"] = slug
        if class_id is not None and str(class_id).strip():
            params["classId"] = int(class_id)
        if category_id is not None and str(category_id).strip():
            params["categoryId"] = int(category_id)
        if sort_field is not None:
            params["sortField"] = sort_field
        if mod_loader_type is not None:
            params["modLoaderType"] = mod_loader_type
        if game_version:
            params["gameVersion"] = game_version

        try:
            response = await self.client.get(
                f"{self.API_BASE}/mods/search",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("data") or []
            mods = [self._parse_mod_data(item) for item in items]
            self._set_cache(cache_key, mods)
            return mods
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401 or e.response.status_code == 403:
                raise CurseForgeApiUnavailable("curseforge_api_key_invalid") from e
            logger.warning("CurseForge search failed with HTTP status %s", e.response.status_code)
            raise CurseForgeApiUnavailable("curseforge_api_unavailable") from e
        except Exception as e:
            logger.warning("CurseForge search failed: %s", type(e).__name__)
            raise CurseForgeApiUnavailable("curseforge_api_unavailable") from e

    async def get_popular_mods(
        self,
        game_id: int | str,
        limit: int = 24,
        page: int = 1,
        sort: str = "trending",
        class_id: int | str | None = None,
        category_id: int | str | None = None,
    ) -> List[CurseForgeModInfo]:
        """Liefert Mods nach Sortierkriterium (trending, popular, newest, updated)."""
        sort_map = {
            "trending": self.SORT_FEATURED,
            "popular": self.SORT_POPULAR,
            "newest": self.SORT_TOTAL_DOWNLOADS,
            "updated": self.SORT_UPDATED,
        }
        sort_field = sort_map.get(sort, self.SORT_FEATURED)
        return await self.search_mods(
            game_id=game_id,
            query="",
            page=page,
            per_page=limit,
            class_id=class_id,
            category_id=category_id,
            sort_field=sort_field,
            sort_order="desc",
        )

    async def get_mod_details(self, mod_id: int | str) -> Optional[CurseForgeModInfo]:
        """Lädt Details für eine einzelne Mod via GET /v1/mods/{modId}."""
        if not self.api_key:
            raise CurseForgeApiUnavailable("curseforge_api_key_missing")

        mod_id_int = int(mod_id)
        cache_key = f"cf_details_{mod_id_int}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        try:
            response = await self.client.get(f"{self.API_BASE}/mods/{mod_id_int}")
            response.raise_for_status()
            data = response.json()
            item = data.get("data")
            if not item:
                return None
            mod = self._parse_mod_data(item)
            self._set_cache(cache_key, mod)
            return mod
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            if e.response.status_code in (401, 403):
                raise CurseForgeApiUnavailable("curseforge_api_key_invalid") from e
            raise CurseForgeApiUnavailable("curseforge_api_unavailable") from e
        except Exception as e:
            logger.warning("CurseForge details failed: %s", type(e).__name__)
            raise CurseForgeApiUnavailable("curseforge_api_unavailable") from e

    async def get_mod_files(
        self,
        mod_id: int | str,
        game_version: str | None = None,
        mod_loader_type: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Listet Dateien für eine Mod auf via GET /v1/mods/{modId}/files."""
        if not self.api_key:
            raise CurseForgeApiUnavailable("curseforge_api_key_missing")

        mod_id_int = int(mod_id)
        params: dict[str, Any] = {"pageSize": 50}
        if game_version:
            params["gameVersion"] = game_version
        if mod_loader_type is not None:
            params["modLoaderType"] = mod_loader_type

        try:
            response = await self.client.get(
                f"{self.API_BASE}/mods/{mod_id_int}/files",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("data") or []
        except Exception as e:
            logger.warning("CurseForge get_files failed: %s", type(e).__name__)
            raise CurseForgeApiUnavailable("curseforge_api_unavailable") from e

    async def get_file_download_url(
        self,
        mod_id: int | str,
        file_id: int | str,
    ) -> Optional[str]:
        """Ermittelt die Download-URL einer Datei via GET /v1/mods/{modId}/files/{fileId}/download-url."""
        if not self.api_key:
            raise CurseForgeApiUnavailable("curseforge_api_key_missing")

        mod_id_int = int(mod_id)
        file_id_int = int(file_id)

        try:
            response = await self.client.get(
                f"{self.API_BASE}/mods/{mod_id_int}/files/{file_id_int}/download-url"
            )
            response.raise_for_status()
            data = response.json()
            return data.get("data")
        except Exception as e:
            logger.warning("CurseForge download URL query failed: %s", type(e).__name__)
            return None

    async def search_modpacks(
        self,
        game_id: int | str,
        query: str = "",
        page: int = 1,
        per_page: int = 24,
    ) -> List[CurseForgeModInfo]:
        """Sucht Modpacks (classId 4471 bei Minecraft oder Fallback auf Suche ohne Class-Filter)."""
        clean_query = str(query or "").strip()
        slug: str | None = None
        if "curseforge.com" in clean_query:
            # URL Format: https://www.curseforge.com/minecraft/modpacks/cobblemon-gg
            parts = [p.strip() for p in clean_query.split("?")[0].split("/") if p.strip()]
            if parts:
                clean_query = parts[-1]
                slug = clean_query

        norm_game_id = normalize_game_id(game_id)
        # 1. Versuch mit classId=4471 (Minecraft Modpacks)
        res = await self.search_mods(
            game_id=norm_game_id,
            query=clean_query if not slug else "",
            slug=slug,
            page=page,
            per_page=per_page,
            class_id=4471 if norm_game_id == 432 else None,
            sort_field=self.SORT_POPULAR,
        )
        if not res and clean_query:
            # Fallback 2: Volltext-Suche mit Filter
            res = await self.search_mods(
                game_id=norm_game_id,
                query=clean_query,
                page=page,
                per_page=per_page,
                class_id=4471 if norm_game_id == 432 else None,
                sort_field=self.SORT_POPULAR,
            )
        if not res and clean_query:
            # Fallback 3: Ohne class_id Filter
            res = await self.search_mods(
                game_id=norm_game_id,
                query=clean_query,
                page=page,
                per_page=per_page,
                class_id=None,
                sort_field=self.SORT_POPULAR,
            )
        return res

    async def test_connection(self) -> dict[str, Any]:
        """Testet die Gültigkeit des hinterlegten API-Keys gegen die API."""
        if not self.api_key:
            return {"ok": False, "error": "curseforge_api_key_missing"}

        try:
            response = await self.client.get(f"{self.API_BASE}/games", params={"pageSize": 1})
            if response.status_code in (401, 403):
                return {"ok": False, "error": "curseforge_api_key_invalid"}
            response.raise_for_status()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


_curseforge_service: Optional[CurseForgeService] = None


async def get_curseforge_service() -> CurseForgeService:
    """Liefert die globale CurseForge-Service-Instanz (reinitialisiert bei Key-Änderung)."""
    global _curseforge_service
    from services.curseforge_api_key_service import resolve_key

    current_key = resolve_key()
    if _curseforge_service is None or _curseforge_service.api_key != current_key:
        if _curseforge_service:
            await _curseforge_service.close()
        _curseforge_service = CurseForgeService()
    return _curseforge_service


async def close_curseforge_service() -> None:
    """Schließt die CurseForge-Service-Instanz."""
    global _curseforge_service
    if _curseforge_service:
        await _curseforge_service.close()
        _curseforge_service = None
