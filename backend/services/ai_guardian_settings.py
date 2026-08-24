"""Verwaltung der Guardian-Engine & KI-Integration.

Maunting Studios Grundsatz: „Sicherheit braucht Vertrauen“ / Datensparsamkeit & Kostenkontrolle.

Ist diese Einstellung deaktiviert (Standard), arbeitet die Guardian Engine
vollkommen autark und isoliert ohne KI — es werden keine KI-Token verbraucht,
keine automatischen Hintergrundläufe gestartet und keine Guardian-Werkzeuge
im KI-Chat angeboten.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_guardian_enabled"
DEFAULT_GUARDIAN_AI_ENABLED = False


def is_guardian_ai_enabled(db: Session | None = None) -> bool:
    """Prüft, ob die KI mit der Guardian Engine interagieren darf.

    Standard: False (vollständige Isolation ohne Tokenverbrauch).
    """
    from services.panel_settings_service import PanelSettingsService

    default_str = "true" if DEFAULT_GUARDIAN_AI_ENABLED else "false"
    val = PanelSettingsService.get(SETTINGS_KEY, default_str, db=db)
    return val.strip().lower() in ("true", "1", "yes")


def set_guardian_ai_enabled(enabled: bool, db: Session | None = None) -> bool:
    """Aktiviert oder deaktiviert die Guardian-KI-Integration panelweit."""
    from services.panel_settings_service import PanelSettingsService

    PanelSettingsService.set(SETTINGS_KEY, "true" if enabled else "false", db=db)
    logger.info("Guardian-KI-Integration aktualisiert: enabled=%s", enabled)
    return enabled
