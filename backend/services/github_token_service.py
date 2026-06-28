"""Panel-weites GitHub Personal Access Token (PAT) für Blueprints mit
``source.type=github``.

Reihenfolge der Auflösung (KISS: ENV schlägt Panel):
    1. ``MSM_GITHUB_CLONE_TOKEN`` (bzw. ``settings.github_clone_token``)
    2. Panel-Settings-DB (über PanelSettingsService, Key ``github_clone_token``)

Token wird **nie** zurückgegeben. Status-Endpoint liefert nur
``{configured, source}``.
"""

from __future__ import annotations

import os
from typing import Literal

from config import settings
from services.panel_settings_service import PanelSettingsService

_PANEL_KEY = "github_clone_token"
Source = Literal["env", "panel", "none"]


def resolve_token() -> str:
    """Liefert den aktuell aktiven GitHub-Token oder ``""``."""
    env_token = (getattr(settings, "github_clone_token", "") or "").strip()
    if env_token:
        return env_token
    env_token = os.getenv("MSM_GITHUB_CLONE_TOKEN", "").strip()
    if env_token:
        return env_token
    return PanelSettingsService.get(_PANEL_KEY, "").strip()


def current_source() -> Source:
    """Woher kommt der aktuell aktive Token?"""
    env_token = (getattr(settings, "github_clone_token", "") or "").strip()
    if env_token:
        return "env"
    env_token = os.getenv("MSM_GITHUB_CLONE_TOKEN", "").strip()
    if env_token:
        return "env"
    if PanelSettingsService.get(_PANEL_KEY, "").strip():
        return "panel"
    return "none"


def status() -> dict[str, str | bool]:
    token = resolve_token()
    return {"configured": bool(token), "source": current_source()}


def set_panel_token(token: str) -> None:
    """Persistiert das PAT in den Panel-Settings (DB).

    Leert den Wert, falls ``token`` leer ist (DELETE-Pfad).
    """
    PanelSettingsService.set(_PANEL_KEY, (token or "").strip())


def clear_panel_token() -> None:
    PanelSettingsService.set(_PANEL_KEY, "")
