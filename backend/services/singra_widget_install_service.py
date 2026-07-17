"""Singra widget installation ID (public in page, encrypted at rest in panel DB)."""

from __future__ import annotations

import os
from typing import Literal

from config import settings
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

_PANEL_KEY_ENC = "singra_widget_install_id_enc"
_PANEL_KEY_LEGACY = "support_widget_singra_id"
_ENV_KEY = "MSM_SINGRA_WIDGET_INSTALL_ID"
_AAD = "msm:singra:widget_install_id"
Source = Literal["env", "panel", "none"]


def resolve_install_id() -> str:
    env_val = (getattr(settings, "singra_widget_install_id", "") or "").strip()
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
            pass
    return PanelSettingsService.get(_PANEL_KEY_LEGACY, "").strip()


def current_source() -> Source:
    if (getattr(settings, "singra_widget_install_id", "") or "").strip():
        return "env"
    if os.getenv(_ENV_KEY, "").strip():
        return "env"
    if PanelSettingsService.get(_PANEL_KEY_ENC, ""):
        return "panel"
    if PanelSettingsService.get(_PANEL_KEY_LEGACY, "").strip():
        return "panel"
    return "none"


def status() -> dict[str, object]:
    return {"configured": bool(resolve_install_id()), "source": current_source()}


def set_panel_install_id(value: str) -> None:
    plain = value.strip()
    if not plain:
        raise ValueError("empty")
    enc = AuthService.encrypt_secret(plain, aad=_AAD)
    PanelSettingsService.set(_PANEL_KEY_ENC, enc)
    PanelSettingsService.set(_PANEL_KEY_LEGACY, "")


def clear_panel_install_id() -> None:
    PanelSettingsService.set(_PANEL_KEY_ENC, "")
    PanelSettingsService.set(_PANEL_KEY_LEGACY, "")