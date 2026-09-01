"""Panel-weites GitHub Personal Access Token (PAT) für Blueprints mit
``source.type=github``.

Reihenfolge der Auflösung (Panel-DB schlägt ENV-Fallback):
    1. Panel-Settings-DB (über PanelSettingsService, Key ``github_clone_token_enc``)
       — DIS-verschluesselt (AES-256-GCM, AAD ``msm:github:token``)
    2. Fallback: alter plain-text Key ``github_clone_token`` (Migration)
    3. ENV-Fallback: ``MSM_GITHUB_CLONE_TOKEN`` / ``settings.github_clone_token`` / ``GITHUB_TOKEN``

Token wird **nie** zurückgegeben. Status-Endpoint liefert nur
``{configured, source}``.
"""

from __future__ import annotations

import os
from typing import Literal

from config import settings
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

_PANEL_KEY_ENC = "github_clone_token_enc"
_PANEL_KEY_LEGACY = "github_clone_token"  # Alte plain-text Werte (Migration)
_AAD = "msm:github:token"
Source = Literal["env", "panel", "none"]


def _env_token() -> str:
    return (
        (getattr(settings, "github_clone_token", "") or "").strip()
        or os.getenv("MSM_GITHUB_CLONE_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )


def _panel_token() -> str:
    enc = PanelSettingsService.get(_PANEL_KEY_ENC, "")
    if enc:
        try:
            dec = AuthService.decrypt_secret(enc, aad=_AAD).strip()
            if dec:
                return dec
        except Exception:
            pass
    return PanelSettingsService.get(_PANEL_KEY_LEGACY, "").strip()


def resolve_token() -> str:
    """Liefert den aktuell aktiven GitHub-Token oder ``""``."""
    panel = _panel_token()
    if panel:
        return panel
    return _env_token()


def current_source() -> Source:
    """Woher kommt der aktuell aktive Token?"""
    if _panel_token():
        return "panel"
    if _env_token():
        return "env"
    return "none"


def status() -> dict[str, str | bool]:
    token = resolve_token()
    return {"configured": bool(token), "source": current_source()}


def set_panel_token(token: str) -> None:
    """Persistiert das PAT DIS-verschluesselt in den Panel-Settings (DB).

    Leert den Wert, falls ``token`` leer ist (DELETE-Pfad).
    """
    token = (token or "").strip()
    if token:
        enc = AuthService.encrypt_secret(token, aad=_AAD)
        PanelSettingsService.set(_PANEL_KEY_ENC, enc)
        PanelSettingsService.set(_PANEL_KEY_LEGACY, "")  # Legacy loeschen
    else:
        PanelSettingsService.set(_PANEL_KEY_ENC, "")
        PanelSettingsService.set(_PANEL_KEY_LEGACY, "")


def clear_panel_token() -> None:
    PanelSettingsService.set(_PANEL_KEY_ENC, "")
    PanelSettingsService.set(_PANEL_KEY_LEGACY, "")
