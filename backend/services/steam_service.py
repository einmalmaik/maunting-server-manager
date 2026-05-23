"""
Steam Web API Service for Mod Search and Details

Uses Steam Web API with optional key. Falls back to community search if no key.
Caches responses to respect rate limits.
"""

import json
import httpx
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os


@dataclass
class SteamModInfo:
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


class SteamService:
    """Steam Web API client for workshop operations."""
    
    API_BASE = "https://api.steampowered.com"
    COMMUNITY_BASE = "https://steamcommunity.com"
    
    def __init__(self):
        from config import settings as app_settings
        self.api_key = app_settings.steam_api_key or os.getenv("STEAM_API_KEY", "")
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                'User-Agent': 'MSM/1.0 (Maunting Server Manager)',
                'Accept': 'application/json',
            }
        )
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = timedelta(minutes=15)
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()
    
    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache:
            return False
        cached_time = self._cache[key]['timestamp']
        return datetime.now(timezone.utc) - cached_time < self._cache_ttl

    def _get_cache(self, key: str) -> Optional[Any]:
        if self._is_cache_valid(key):
            return self._cache[key]['data']
        return None

    def _set_cache(self, key: str, data: Any):
        self._cache[key] = {
            'data': data,
            'timestamp': datetime.now(timezone.utc)
        }
    
    async def search_workshop_mods(
        self, 
        appid: str, 
        query: str = "", 
        page: int = 1,
        per_page: int = 50,
        required_tags: list[str] | None = None,
    ) -> List[SteamModInfo]:
        """Search workshop mods via Steam Web API QueryFiles."""
        if not self.api_key:
            return []
        
        cache_key = f"search_{appid}_{query}_{page}_{per_page}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        try:
            query_data = {
                'query_type': 3 if query else 0,
                'page': page,
                'numperpage': per_page,
                'appid': int(appid),
                'search_text': query,
                'return_short_description': True,
                'return_tags': True,
                'return_previews': True,
                'return_details': True,
                'return_metadata': True,
            }
            if required_tags:
                query_data['requiredtags'] = ','.join(required_tags)

            params = {
                'key': self.api_key,
                'input_json': json.dumps(query_data, separators=(',', ':')),
            }

            response = await self.client.get(
                f"{self.API_BASE}/IPublishedFileService/QueryFiles/v1/",
                params=params
            )
            response.raise_for_status()
            
            data = response.json()
            mods = []
            
            if 'response' in data and 'publishedfiledetails' in data['response']:
                for mod_data in data['response']['publishedfiledetails']:
                    if mod_data.get('result') == 1:
                        mods.append(self._parse_mod_data(mod_data))
            
            self._set_cache(cache_key, mods)
            return mods
            
        except Exception as e:
            print(f"Steam API search error: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    async def get_mod_details(self, appid: str, publishedfileid: str) -> Optional[SteamModInfo]:
        """Get detailed information for a specific mod."""
        if not self.api_key:
            return None
        
        cache_key = f"details_{appid}_{publishedfileid}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        try:
            query_data = {
                'publishedfileids[0]': int(publishedfileid),
                'includevotes': True,
            }

            params = {
                'key': self.api_key,
                'input_json': json.dumps(query_data, separators=(',', ':')),
            }

            response = await self.client.get(
                f"{self.API_BASE}/IPublishedFileService/GetDetails/v1/",
                params=params
            )
            response.raise_for_status()
            
            data = response.json()
            
            if 'response' in data and 'publishedfiledetails' in data['response']:
                mod_data = data['response']['publishedfiledetails'][0]
                if mod_data.get('result') == 1:
                    mod = self._parse_mod_data(mod_data)
                    self._set_cache(cache_key, mod)
                    return mod
            
            return None
            
        except Exception as e:
            print(f"Steam API details error: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _parse_mod_data(self, mod_data: Dict[str, Any]) -> SteamModInfo:
        """Parse Steam API response into SteamModInfo."""
        tags = []
        for tag in mod_data.get('tags', []):
            if isinstance(tag, dict):
                tags.append(tag.get('tag', ''))
            elif isinstance(tag, str):
                tags.append(tag)
        
        preview_url = None
        previews = mod_data.get('previews', [])
        if previews:
            preview = previews[0]
            preview_url = preview.get('url') or preview.get('youtubevideoid')
        
        return SteamModInfo(
            publishedfileid=str(mod_data.get('publishedfileid', '')),
            title=mod_data.get('title', 'Unknown Mod'),
            description=mod_data.get('short_description', mod_data.get('description', '')),
            creator=mod_data.get('creator', 'Unknown'),
            file_size=mod_data.get('file_size', 0),
            created=datetime.fromtimestamp(mod_data.get('time_created', 0)),
            updated=datetime.fromtimestamp(mod_data.get('time_updated', 0)),
            subscriptions=mod_data.get('subscriptions', 0),
            favorites=mod_data.get('favorited', 0),
            tags=tags,
            preview_url=preview_url,
            direct_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_data.get('publishedfileid', '')}"
        )
    
    # query_type constants for Steam QueryFiles
    SORT_TRENDING = 3     # RankedByTrend (last N days)
    SORT_POPULAR = 0      # RankedByVote (all-time most subscribed)
    SORT_NEWEST = 1       # RankedByPublicationDate
    SORT_UPDATED = 12     # RankedByLastUpdatedDate

    async def get_popular_mods(self, appid: str, limit: int = 20, required_tags: list[str] | None = None, sort: str = "trending") -> List[SteamModInfo]:
        """Get mods for an app sorted by the given criteria.

        sort: 'trending' | 'popular' | 'newest' | 'updated'
        """
        if not self.api_key:
            return []

        sort_map = {
            "trending": self.SORT_TRENDING,
            "popular": self.SORT_POPULAR,
            "newest": self.SORT_NEWEST,
            "updated": self.SORT_UPDATED,
        }
        query_type = sort_map.get(sort, self.SORT_TRENDING)

        cache_key = f"popular_{appid}_{limit}_{sort}"
        if required_tags:
            cache_key += "_" + "_".join(required_tags)
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        
        try:
            query_data: dict = {
                'query_type': query_type,
                'page': 1,
                'numperpage': limit,
                'appid': int(appid),
                'return_short_description': True,
                'return_tags': True,
                'return_previews': True,
            }
            if sort == "trending":
                query_data['days'] = 7
            if required_tags:
                query_data['requiredtags'] = ','.join(required_tags)

            params = {
                'key': self.api_key,
                'input_json': json.dumps(query_data, separators=(',', ':')),
            }

            response = await self.client.get(
                f"{self.API_BASE}/IPublishedFileService/QueryFiles/v1/",
                params=params
            )
            response.raise_for_status()
            
            data = response.json()
            mods = []
            
            if 'response' in data and 'publishedfiledetails' in data['response']:
                for mod_data in data['response']['publishedfiledetails']:
                    if mod_data.get('result') == 1:
                        mods.append(self._parse_mod_data(mod_data))
            
            self._set_cache(cache_key, mods)
            return mods
            
        except Exception as e:
            print(f"Steam API popular mods error: {e}")
            import traceback
            traceback.print_exc()
            return []


# Global instance
_steam_service: Optional[SteamService] = None


async def get_steam_service() -> SteamService:
    """Get or create Steam service instance. Recreates if API key changed."""
    global _steam_service
    from config import settings as app_settings
    current_key = app_settings.steam_api_key or os.getenv("STEAM_API_KEY", "")
    if _steam_service is None or _steam_service.api_key != current_key:
        if _steam_service:
            await _steam_service.close()
        _steam_service = SteamService()
    return _steam_service


async def close_steam_service():
    """Close Steam service instance."""
    global _steam_service
    if _steam_service:
        await _steam_service.close()
        _steam_service = None