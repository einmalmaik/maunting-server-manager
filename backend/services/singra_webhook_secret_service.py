"""Singra support-widget webhook HMAC secret (panel DB or env)."""

from __future__ import annotations

import os
import secrets
from typing import Literal

from config import settings
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

_PANEL_KEY_ENC = "singra_webhook_secret_enc"
_ENV_KEY = "MSM_SINGRA_WEBHOOK_SECRET"
_AAD = "msm:singra:webhook_secret"
Source = Literal["env", "panel", "none"]


def resolve_secret() -> str:
    env_val = (getattr(settings, "singra_webhook_secret", "") or "").strip()
    if env_val:
        return env_val
    env_val = os.getenv(_ENV_KEY, "").strip()
    if env_val:
        return env_val
    enc = PanelSettingsService.get(_PANEL_KEY_ENC, "")
    if enc:
        try:
            return AuthService.decrypt_secret(enc, aad=_AAD).strip()
        except Exception:
            return ""
    return ""


def current_source() -> Source:
    if (getattr(settings, "singra_webhook_secret", "") or "").strip():
        return "env"
    if os.getenv(_ENV_KEY, "").strip():
        return "env"
    if PanelSettingsService.get(_PANEL_KEY_ENC, ""):
        return "panel"
    return "none"


def status() -> dict[str, object]:
    return {"configured": bool(resolve_secret()), "source": current_source()}


def rotate_panel_secret() -> str:
    """Generate, persist (encrypted), and return a new plaintext secret."""
    plain = secrets.token_hex(32)
    enc = AuthService.encrypt_secret(plain, aad=_AAD)
    PanelSettingsService.set(_PANEL_KEY_ENC, enc)
    return plain


def clear_panel_secret() -> None:
    PanelSettingsService.set(_PANEL_KEY_ENC, "")