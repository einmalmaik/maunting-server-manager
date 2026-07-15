import asyncio
import re
import subprocess
import time

import httpx
import psutil
from sqlalchemy import text

from fastapi import APIRouter, Depends, HTTPException

from config import settings
from database import SessionLocal
from dependencies import get_current_user, require_global
from games import list_game_info
from models import User
from services import network_interfaces_service
from services.panel_settings_service import PanelSettingsService

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/legal")
def legal_settings() -> dict:
    """Oeffentliche Legal-Metadaten fuer Footer und Hilfe.

    Absichtlich schmal: keine anderen Panel-Settings oeffentlich machen.
    """
    enabled = PanelSettingsService.get("imprint_enabled", "false") == "true"
    url = PanelSettingsService.get("imprint_url", "")
    return {
        "imprint_enabled": enabled,
        "imprint_url": url if enabled else "",
    }


@router.get("/support-widget")
def public_support_widget() -> dict:
    """Oeffentliche Widget-Konfiguration fuer Script-Injektion (keine Secrets)."""
    enabled = PanelSettingsService.get("support_widget_enabled", "false") == "true"
    mode = PanelSettingsService.get("support_widget_mode", "singra")
    singra_id = PanelSettingsService.get("support_widget_singra_id", "").strip()
    custom = PanelSettingsService.get("support_widget_custom_snippet", "")
    if not enabled:
        return {"enabled": False, "mode": mode, "singra_widget_id": "", "custom_snippet": ""}
    if mode == "custom":
        return {"enabled": True, "mode": "custom", "singra_widget_id": "", "custom_snippet": custom}
    return {
        "enabled": True,
        "mode": "singra",
        "singra_widget_id": singra_id,
        "custom_snippet": "",
        "script_src": "https://singrabot.mauntingstudios.de/widget.js",
    }


def _check_docker() -> dict:
    """Prüft ob der Docker-Daemon erreichbar ist."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version = result.stdout.strip() or "unknown"
            return {"status": "ok", "detail": f"v{version}"}
        return {"status": "error", "detail": result.stderr.strip()[:120] or "docker info failed"}
    except FileNotFoundError:
        return {"status": "error", "detail": "docker not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "detail": "docker info timed out"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:120]}


def _check_caddy() -> dict:
    """Prüft ob Caddy installiert ist (kein laufender Prozess nötig — nur Verfügbarkeit)."""
    try:
        result = subprocess.run(
            ["caddy", "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version = result.stdout.strip()[:40] or "unknown"
            return {"status": "ok", "detail": version}
        return {"status": "error", "detail": result.stderr.strip()[:120] or "caddy version failed"}
    except FileNotFoundError:
        return {"status": "degraded", "detail": "caddy not found (optional)"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "detail": "caddy version timed out"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:120]}


def _check_database() -> dict:
    """Prüft ob die Datenbankverbindung funktioniert."""
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return {"status": "ok", "detail": "reachable"}
        finally:
            db.close()
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:120]}


def _strip_version(raw: str) -> str:
    """Normalisiert einen Versions-String fuer den Vergleich.

    Entfernt das optionale 'v'-Prefix und git-describe-Suffixe
    (z.B. 'v1.7.7-2-gabcdef' → '1.7.7').
    """
    v = raw.strip().lstrip("v")
    # git describe haengt '-<commits>-g<hash>' an, wenn HEAD nicht
    # exakt auf dem Tag liegt. Wir brauchen nur den SemVer-Kern.
    match = re.match(r"^(\d+\.\d+\.\d+)", v)
    return match.group(1) if match else v


def _version_newer(latest: str, current: str) -> bool:
    """Prueft, ob 'latest' eine hoehere SemVer-Version als 'current' ist.

    Vergleicht die drei numerischen Segmente (Major.Minor.Patch).
    Gibt False zurueck, wenn eines der Argumente nicht parsbar ist.
    """
    try:
        parts_l = [int(x) for x in latest.split(".")]
        parts_c = [int(x) for x in current.split(".")]
        return parts_l > parts_c
    except (ValueError, AttributeError):
        return False


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
            # SemVer-Vergleich: v-Prefix und git-describe-Suffixe
            # normalisieren, dann numerisch pruefen ob latest > current.
            norm_current = _strip_version(current)
            norm_latest = _strip_version(latest)
            update_available = _version_newer(norm_latest, norm_current)
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


@router.get("/health")
async def system_health(user: User = Depends(get_current_user)) -> dict:
    """Prüft die Verfügbarkeit der kritischen Infrastrukturkomponenten.

    Alle Checks laufen parallel (asyncio.to_thread). Das Ergebnis ist ein
    Objekt pro Service sowie ein aggregierter ``overall``-Status:
    - ``ok``       — alle Pflicht-Services erreichbar
    - ``degraded`` — mindestens ein optionaler Service fehlt (z.B. Caddy)
    - ``error``    — mindestens ein Pflicht-Service nicht erreichbar

    Sicherheit: kein Secret, keine internen Pfade im Response-Objekt.
    """
    t0 = time.monotonic()

    docker_res, caddy_res, db_res = await asyncio.gather(
        asyncio.to_thread(_check_docker),
        asyncio.to_thread(_check_caddy),
        asyncio.to_thread(_check_database),
    )

    services = {
        "docker": docker_res,
        "caddy": caddy_res,
        "database": db_res,
    }

    # Aggregation: error > degraded > ok
    # Docker + DB sind Pflicht; Caddy ist optional (degraded wenn fehlt)
    if docker_res["status"] == "error" or db_res["status"] == "error":
        overall = "error"
    elif any(s["status"] == "degraded" for s in services.values()):
        overall = "degraded"
    else:
        overall = "ok"

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    return {
        "overall": overall,
        "services": services,
        "checked_in_ms": elapsed_ms,
    }
