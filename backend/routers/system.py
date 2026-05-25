import asyncio
import subprocess

import httpx
import psutil

from fastapi import APIRouter, Depends, HTTPException

from config import settings
from dependencies import get_current_user, require_global
from games import list_game_info
from models import User
from services import network_interfaces_service

router = APIRouter(prefix="/api/system", tags=["system"])


def _get_current_version() -> str:
    """Liest die aktuelle Version aus Git-Tags oder einer Datei."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=5, cwd="/opt/msm",
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    try:
        with open("/opt/msm/.version", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        pass
    return "unknown"


@router.get("/resources")
async def system_resources(user: User = Depends(require_global("system.view"))) -> dict:

    cpu = await asyncio.to_thread(psutil.cpu_percent, interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    return {
        "cpu_percent": cpu,
        "cpu_count": psutil.cpu_count(),
        "ram_total_mb": memory.total // (1024 * 1024),
        "ram_used_mb": memory.used // (1024 * 1024),
        "ram_free_mb": memory.available // (1024 * 1024),
        "ram_percent": memory.percent,
        "disk_total_gb": disk.total // (1024 * 1024 * 1024),
        "disk_used_gb": disk.used // (1024 * 1024 * 1024),
        "disk_free_gb": disk.free // (1024 * 1024 * 1024),
        "disk_percent": disk.percent,
    }


@router.get("/games")
def supported_games(user: User = Depends(get_current_user)) -> list[dict]:
    """Dynamische Plugin-Liste inkl. Capability-Flags.

    `mod_support` und `supports_steam_workshop` kommen direkt vom Plugin und
    sind die Quelle der Wahrheit fuer die UI-Sichtbarkeit (z.B. ob der
    Mod-Manager-Tab im Server-Detail erscheint). Backend-Routen bleiben
    zusaetzlich Defensiv-Layer (`require_server_permission` + Plugin-Check).
    """
    return list_game_info()


@router.get("/interfaces")
def host_interfaces(user: User = Depends(require_global("system.view"))) -> dict:
    """Liefert alle IPv4-Host-Interfaces fuer die Bind-IP-Auswahl im UI.

    Erfordert `system.view` — die Liste enthaelt Topologie-Information
    (LAN-Layout) und soll nicht an Standard-Benutzer geraten.
    """
    interfaces = [h.to_dict() for h in network_interfaces_service.list_host_interfaces()]
    return {
        "interfaces": interfaces,
        "default_bind_ip": network_interfaces_service.default_bind_ip(),
    }


@router.get("/version")
def system_version(user: User = Depends(get_current_user)) -> dict:
    """Aktuelle Version + Update-Status (GitHub Releases).

    Für Tauri: derselbe Endpunkt kann als Update-Quelle genutzt werden.
    """
    current = _get_current_version()
    latest = None
    update_available = False
    release_url = None

    try:
        url = (
            f"https://api.github.com/repos/"
            f"{settings.github_owner}/{settings.github_repo}/releases/latest"
        )
        resp = httpx.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            latest = data.get("tag_name", "unknown")
            release_url = data.get("html_url", "")
            # Einfacher String-Vergleich (Tags sollten SemVer nutzen)
            update_available = current != latest and latest != "unknown"
    except Exception:
        pass

    return {
        "current_version": current,
        "latest_version": latest,
        "update_available": update_available,
        "release_url": release_url,
        "auto_update_enabled": settings.auto_update,
        "github_repo": f"{settings.github_owner}/{settings.github_repo}",
    }
