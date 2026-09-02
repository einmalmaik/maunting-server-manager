"""Panel-weiter Steam Web API Key (Workshop-Suche, Mod-Metadaten).

Auflösung (Panel-DB schlägt ENV-Fallback):
    1. Panel-DB ``steam_web_api_key_enc`` (DIS-verschlüsselt, AAD ``msm:steam:api_key``)
    2. Legacy plain ``steam_web_api_key`` in panel_settings (Migration)
    3. ENV-Fallback: ``settings.steam_api_key`` / ``MSM_STEAM_API_KEY`` / ``STEAM_API_KEY``
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


def _panel_key() -> str:
    enc = PanelSettingsService.get(_PANEL_KEY_ENC, "")
    if enc:
        try:
            dec = AuthService.decrypt_secret(enc, aad=_AAD).strip()
            if dec:
                return dec
        except Exception:
            pass
    return PanelSettingsService.get(_PANEL_KEY_LEGACY, "").strip()


def resolve_key() -> str:
    panel = _panel_key()
    if panel:
        return panel
    return _env_key()


def current_source() -> Source:
    if _panel_key():
        return "panel"
    if _env_key():
        return "env"
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