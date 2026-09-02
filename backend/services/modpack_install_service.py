from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def fetch_modpack_download_url(mod_id: int | str, file_id: int | str) -> str | None:
    from services.curseforge_service import get_curseforge_service

    svc = await get_curseforge_service()
    return await svc.get_file_download_url(mod_id, file_id)


def target_install_path(install_dir: str, curseforge_install_path: str | None) -> str:
    base = Path(install_dir)
    sub = (curseforge_install_path or "mods").strip().strip("/")
    return str(base / sub) if sub else str(base)


async def install_modpack_placeholder(server_id: int, modpack_mod_id: int, file_id: int) -> dict[str, Any]:
    url = await fetch_modpack_download_url(modpack_mod_id, file_id)
    if not url:
        return {"ok": False, "error": "download_url_not_found"}
    return {"ok": True, "download_url": url, "note": "Download-URL ermittelt, Installation erfolgt via proposal"}
