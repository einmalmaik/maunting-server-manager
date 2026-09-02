"""Mod-Updatepruefung und Workshop-Suche als gemeinsamer, synchroner Pfad.

Beides lag bisher ausschliesslich in `routers/mods.py` bzw. hinter dem
asynchronen `steam_service`. Die AI-Werkzeugschicht laeuft synchron innerhalb
eines bereits laufenden Event-Loops und kann den asynchronen Client deshalb
nicht aufrufen, ohne dessen Lebenszyklus zu zerreissen.

Statt einen zweiten Pfad neben dem Router zu bauen, wandert die Updatepruefung
hierher — Router und AI-Tool rufen ab jetzt dieselbe Funktion. Die Suche nutzt
`httpx.Client` genauso synchron wie `games/updater.py` es fuer denselben
Steam-Endpunkt bereits tut.
"""

from __future__ import annotations

import json
import logging
import time

import httpx
from sqlalchemy.orm import Session

from models import Mod, Server
from services.mod_install_status_service import INSTALL_RUNNING


logger = logging.getLogger(__name__)

API_BASE = "https://api.steampowered.com"
SEARCH_TIMEOUT_SECONDS = 12.0
# Bewusst klein: jedes Ergebnis landet als unvertrauenswuerdiger Text im
# Modellkontext und damit im Kostenbudget des Benutzers.
MAX_SEARCH_RESULTS = 10
MAX_TITLE_CHARS = 120

_UPDATE_CHECK_CACHE: dict[int, float] = {}
UPDATE_CHECK_TTL_SECONDS = 300


class ModSearchUnavailable(RuntimeError):
    """Die Workshop-Suche ist nicht nutzbar — mit stabilem Grund."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def mark_update_candidates(db: Session, server_id: int, updates: list[dict]) -> None:
    """Vermerkt gefundene Updatekandidaten an den Mod-Zeilen."""
    changed = False
    for update in updates:
        workshop_id = str(update.get("workshop_id") or "")
        action = str(update.get("action") or "update")
        if not workshop_id or action not in {"install", "update"}:
            continue
        mod = (
            db.query(Mod)
            .filter(Mod.server_id == server_id, Mod.workshop_id == workshop_id)
            .first()
        )
        if not mod or mod.install_status == INSTALL_RUNNING:
            continue
        mod.install_status = "pending"
        mod.install_action = action
        mod.install_progress = 0
        mod.install_eta_seconds = None
        mod.install_error = None
        mod.update_status = "missing" if action == "install" else "outdated"
        mod.update_reason = str(update.get("reason") or action)
        changed = True
    if changed:
        db.commit()


def refresh_update_availability(
    db: Session, server: Server, plugin, *, force: bool = False
) -> list[dict]:
    """Prueft ausstehende Modupdates, hoechstens alle fuenf Minuten je Server.

    Der Cache ist bewusst prozesslokal und nicht persistent: er soll nur
    verhindern, dass ein haeufig aufgerufener Endpunkt Steam in Serie befragt.
    """
    if not plugin or not getattr(plugin, "supports_mods", False):
        return []
    now = time.time()
    if not force and now - _UPDATE_CHECK_CACHE.get(server.id, 0) < UPDATE_CHECK_TTL_SECONDS:
        return []
    _UPDATE_CHECK_CACHE[server.id] = now
    try:
        updates = plugin.check_for_mod_updates(server)
    except Exception as exc:
        logger.warning("Mod-Update-Check fehlgeschlagen fuer Server %s: %s", server.id, exc)
        return []
    if updates:
        mark_update_candidates(db, server.id, updates)
    return updates


def _minimized(mod_data: dict) -> dict:
    """Reduziert einen Steam-Treffer auf das, was zur Auswahl noetig ist."""
    tags = [
        str(tag.get("tag"))[:32]
        for tag in (mod_data.get("tags") or [])
        if isinstance(tag, dict) and tag.get("tag")
    ]
    raw_desc = str(mod_data.get("short_description") or "").strip()
    return {
        "workshop_id": str(mod_data.get("publishedfileid") or ""),
        "title": str(mod_data.get("title") or "")[:MAX_TITLE_CHARS],
        "description": raw_desc[:256] if raw_desc else None,
        "updated": mod_data.get("time_updated"),
        "subscriptions": mod_data.get("subscriptions"),
        "tags": tags[:8],
    }


def search_workshop(
    *, appid: str, query: str, page: int = 1, required_tags: list[str] | None = None
) -> list[dict]:
    """Durchsucht den Steam Workshop synchron und minimiert das Ergebnis.

    `games/updater.py` spricht denselben Anbieter bereits mit einem synchronen
    `httpx.Client` an; hier gilt dasselbe Muster. Der asynchrone `steam_service`
    haelt einen langlebigen Client an genau einem Event-Loop und laesst sich
    deshalb nicht aus einem synchronen Werkzeugaufruf heraus mitbenutzen.
    """
    from services.steam_api_key_service import resolve_key

    api_key = resolve_key()
    if not api_key:
        raise ModSearchUnavailable("steam_api_key_missing")

    query_data = {
        "query_type": 3 if query else 0,
        "page": max(1, min(int(page), 50)),
        "numperpage": MAX_SEARCH_RESULTS,
        "appid": int(appid),
        "search_text": query[:128],
        "return_short_description": True,
        "return_tags": True,
        "return_previews": False,
        "return_details": True,
        "return_metadata": False,
    }
    if required_tags:
        query_data["requiredtags"] = ",".join(required_tags)

    try:
        with httpx.Client(
            timeout=SEARCH_TIMEOUT_SECONDS,
            headers={"User-Agent": "MSM/1.0 (Maunting Service Manager)"},
            follow_redirects=False,
        ) as client:
            response = client.get(
                f"{API_BASE}/IPublishedFileService/QueryFiles/v1/",
                params={
                    "key": api_key,
                    "input_json": json.dumps(query_data, separators=(",", ":")),
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        # Der Fehlertext von httpx enthaelt die vollstaendige URL inklusive des
        # Steam-Web-API-Keys. Nur der Typ wird protokolliert.
        logger.warning("Workshop-Suche fehlgeschlagen: %s", type(exc).__name__)
        raise ModSearchUnavailable("steam_api_unavailable") from exc

    details = (data.get("response") or {}).get("publishedfiledetails") or []
    results = [
        _minimized(entry)
        for entry in details
        if isinstance(entry, dict) and entry.get("result") == 1
    ]
    return [item for item in results if item["workshop_id"]][:MAX_SEARCH_RESULTS]
