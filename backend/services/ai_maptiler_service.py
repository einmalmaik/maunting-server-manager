"""Optionale MapTiler-Konfiguration fuer die hochaufgeloeste Kartenansicht.

Der gespeicherte Wert ist ausschliesslich ein auf die Panel-Origin beschraenkter
MapTiler-Browser-Key. Er wird verschluesselt abgelegt; die Karte erhaelt ihn
nur zur Laufzeit, weil ein Browser-Kartenanbieter den Key technisch benoetigt.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode


logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_maptiler_browser_key_encrypted"
_AAD = "msm:settings:ai_maptiler_browser_key"
_STYLE_BASE_URL = "https://api.maptiler.com/maps/hybrid/style.json"


def get_browser_key() -> str | None:
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    stored = PanelSettingsService.get(SETTINGS_KEY, "")
    if not stored:
        return None
    try:
        value = AuthService.decrypt_secret(stored, aad=_AAD).strip()
        return value or None
    except Exception as exc:
        logger.warning("MapTiler-Konfiguration nicht lesbar error=%s", type(exc).__name__)
        return None


def store_browser_key(plaintext: str) -> None:
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    value = (plaintext or "").strip()
    if not value:
        PanelSettingsService.set(SETTINGS_KEY, "")
        return
    PanelSettingsService.set(SETTINGS_KEY, AuthService.encrypt_secret(value, aad=_AAD))


def is_configured() -> bool:
    return get_browser_key() is not None


def browser_map_config() -> dict[str, str] | None:
    """Gibt nur die Laufzeitkonfiguration fuer die Kartenbibliothek aus."""
    key = get_browser_key()
    if not key:
        return None
    return {"style_url": f"{_STYLE_BASE_URL}?{urlencode({'key': key})}"}
