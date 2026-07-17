"""Known third-party support widget embed patterns (KISS, no user HTML for natives)."""

from __future__ import annotations

from services.panel_settings_service import PanelSettingsService
from services.singra_widget_install_service import resolve_install_id

SINGRA_SCRIPT_SRC = "https://singrabot.mauntingstudios.de/widget.js"

NATIVE_PROVIDERS = frozenset({"singra", "crisp", "tawk"})


def get_provider() -> str:
    return PanelSettingsService.get("support_widget_mode", "singra").strip().lower() or "singra"


def public_widget_config() -> dict:
    enabled = PanelSettingsService.get("support_widget_enabled", "false") == "true"
    provider = get_provider()
    if not enabled:
        return {"enabled": False, "provider": provider}

    if provider == "singra":
        install_id = resolve_install_id()
        return {
            "enabled": True,
            "provider": "singra",
            "singra_widget_id": install_id,
            "script_src": SINGRA_SCRIPT_SRC,
        }

    if provider == "crisp":
        site_id = PanelSettingsService.get("support_widget_crisp_website_id", "").strip()
        return {"enabled": True, "provider": "crisp", "crisp_website_id": site_id}

    if provider == "tawk":
        prop = PanelSettingsService.get("support_widget_tawk_property_id", "").strip()
        wid = PanelSettingsService.get("support_widget_tawk_widget_id", "").strip()
        return {"enabled": True, "provider": "tawk", "tawk_property_id": prop, "tawk_widget_id": wid}

    snippet = PanelSettingsService.get("support_widget_custom_snippet", "")
    return {"enabled": True, "provider": "custom", "custom_snippet": snippet}