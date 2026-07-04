"""Panel-weites GitHub Personal Access Token (PAT) für Blueprints mit
``source.type=github``.

Reihenfolge der Auflösung (KISS: ENV schlägt Panel):
    1. ``MSM_GITHUB_CLONE_TOKEN`` (bzw. ``settings.github_clone_token``)
    2. Panel-Settings-DB (über PanelSettingsService, Key ``github_clone_token_enc``)
       — DIS-verschluesselt (AES-256-GCM, AAD ``msm:github:token``)
    3. Fallback: alter plain-text Key ``github_clone_token`` (Migration)

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


def resolve_token() -> str:
    """Liefert den aktuell aktiven GitHub-Token oder ``""``."""
    env_token = (getattr(settings, "github_clone_token", "") or "").strip()
    if env_token:
        return env_token
    env_token = os.getenv("MSM_GITHUB_CLONE_TOKEN", "").strip()
    if env_token:
        return env_token
    # DIS-verschluesselter Wert
    enc = PanelSettingsService.get(_PANEL_KEY_ENC, "")
    if enc:
        try:
            return AuthService.decrypt_secret(enc, aad=_AAD).strip()
        except Exception:
            pass  # Korrupt oder falscher Key — falle auf Legacy zurueck
    # Legacy: alter plain-text Wert (wird bei Migration auf DIS umgestellt)
    return PanelSettingsService.get(_PANEL_KEY_LEGACY, "").strip()


def current_source() -> Source:
    """Woher kommt der aktuell aktive Token?"""
    env_token = (getattr(settings, "github_clone_token", "") or "").strip()
    if env_token:
        return "env"
    env_token = os.getenv("MSM_GITHUB_CLONE_TOKEN", "").strip()
    if env_token:
        return "env"
    if PanelSettingsService.get(_PANEL_KEY_ENC, "").strip():
        return "panel"
    if PanelSettingsService.get(_PANEL_KEY_LEGACY, "").strip():
        return "panel"
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
