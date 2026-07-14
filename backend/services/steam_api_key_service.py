"""Panel-weiter Steam Web API Key (Workshop-Suche, Mod-Metadaten).

Auflösung (ENV schlägt Panel):
    1. ``settings.steam_api_key`` / ``MSM_STEAM_API_KEY`` / ``STEAM_API_KEY``
    2. Panel-DB ``steam_web_api_key_enc`` (DIS-verschlüsselt, AAD ``msm:steam:api_key``)
    3. Legacy plain ``steam_web_api_key`` in panel_settings (Migration)

Speichern über die UI persistiert in DB **und** aktualisiert ``.env`` (best effort),
damit Neustarts und ``install.sh``-Rewrites den Key nicht verlieren.
"""

from __future__ import annotations

import os
from typing import Literal

from config import settings
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

_PANEL_KEY_ENC = "steam_web_api_key_enc"
_PANEL_KEY_LEGACY = "steam_web_api_key"
_AAD = "msm:steam:api_key"
Source = Literal["env", "panel", "none"]


def _env_key() -> str:
    return (
        (getattr(settings, "steam_api_key", "") or "").strip()
        or os.getenv("MSM_STEAM_API_KEY", "").strip()
        or os.getenv("STEAM_API_KEY", "").strip()
    )


def resolve_key() -> str:
    key = _env_key()
    if key:
        return key
    enc = PanelSettingsService.get(_PANEL_KEY_ENC, "")
    if enc:
        try:
            return AuthService.decrypt_secret(enc, aad=_AAD).strip()
        except Exception:
            pass
    return PanelSettingsService.get(_PANEL_KEY_LEGACY, "").strip()


def current_source() -> Source:
    if _env_key():
        return "env"
    if PanelSettingsService.get(_PANEL_KEY_ENC, "").strip():
        return "panel"
    if PanelSettingsService.get(_PANEL_KEY_LEGACY, "").strip():
        return "panel"
    return "none"


def status() -> dict[str, str | bool]:
    key = resolve_key()
    return {"configured": bool(key), "source": current_source()}


def set_panel_key(key: str) -> None:
    key = (key or "").strip()
    if not key:
        PanelSettingsService.set(_PANEL_KEY_ENC, "")
        PanelSettingsService.set(_PANEL_KEY_LEGACY, "")
        return
    enc = AuthService.encrypt_secret(key, aad=_AAD)
    PanelSettingsService.set(_PANEL_KEY_ENC, enc)
    PanelSettingsService.set(_PANEL_KEY_LEGACY, "")


def clear_panel_key() -> None:
    PanelSettingsService.set(_PANEL_KEY_ENC, "")
    PanelSettingsService.set(_PANEL_KEY_LEGACY, "")