from __future__ import annotations

import os
from typing import Literal

from config import settings
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

_AAD = "msm:settings:cloudflare_api_token"
_ENC_KEY = "cloudflare_api_token_enc"
_LEGACY_KEY = "cloudflare_api_token"
Source = Literal["env", "panel", "none"]


def _env_key() -> str:
    return (getattr(settings, "cloudflare_api_token", "") or "").strip() or os.getenv("MSM_CLOUDFLARE_API_TOKEN", "").strip()


def resolve_key() -> str:
    key = _env_key()
    if key:
        return key
    enc = PanelSettingsService.get(_ENC_KEY, "")
    if enc:
        try:
            return AuthService.decrypt_secret(enc, aad=_AAD).strip()
        except Exception:
            pass
    return PanelSettingsService.get(_LEGACY_KEY, "").strip()


def current_source() -> Source:
    if _env_key():
        return "env"
    if PanelSettingsService.get(_ENC_KEY, "").strip():
        return "panel"
    if PanelSettingsService.get(_LEGACY_KEY, "").strip():
        return "panel"
    return "none"


def status() -> dict:
    key = resolve_key()
    return {"configured": bool(key), "source": current_source()}


def set_panel_key(key: str) -> None:
    key = (key or "").strip()
    if not key:
        PanelSettingsService.set(_ENC_KEY, "")
        PanelSettingsService.set(_LEGACY_KEY, "")
        return
    enc = AuthService.encrypt_secret(key, aad=_AAD)
    PanelSettingsService.set(_ENC_KEY, enc)
    PanelSettingsService.set(_LEGACY_KEY, "")


def clear_panel_key() -> None:
    PanelSettingsService.set(_ENC_KEY, "")
    PanelSettingsService.set(_LEGACY_KEY, "")
