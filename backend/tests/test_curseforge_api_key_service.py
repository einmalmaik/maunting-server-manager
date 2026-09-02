"""Tests für curseforge_api_key_service (DIS-Verschlüsselung, Quellen & Löschung)."""

import pytest

from services.panel_settings_service import PanelSettingsService
from services import curseforge_api_key_service as svc


@pytest.fixture(autouse=True)
def _clear_panel_settings(monkeypatch):
    from config import settings

    monkeypatch.delenv("MSM_CURSEFORGE_API_KEY", raising=False)
    monkeypatch.delenv("CURSEFORGE_API_KEY", raising=False)
    monkeypatch.setattr(settings, "curseforge_api_key", "")
    PanelSettingsService.invalidate_cache()
    PanelSettingsService.set("curseforge_api_key_enc", "")
    PanelSettingsService.set("curseforge_api_key", "")
    yield
    PanelSettingsService.invalidate_cache()


def test_resolve_from_panel_encrypted():
    svc.set_panel_key("CF_TEST_KEY_123456789")
    assert svc.resolve_key() == "CF_TEST_KEY_123456789"
    assert svc.current_source() == "panel"
    stat = svc.status()
    assert stat["configured"] is True
    assert stat["source"] == "panel"


def test_panel_wins_over_env_and_env_is_fallback(monkeypatch):
    from config import settings

    # 1. Panel Key gesetzt, ENV gesetzt -> Panel gewinnt
    svc.set_panel_key("panel_cf_key")
    monkeypatch.setenv("MSM_CURSEFORGE_API_KEY", "env_cf_key_fallback")
    monkeypatch.setattr(settings, "curseforge_api_key", "")
    assert svc.resolve_key() == "panel_cf_key"
    assert svc.current_source() == "panel"
    stat = svc.status()
    assert stat["configured"] is True
    assert stat["source"] == "panel"

    # 2. Panel Key gelöscht -> ENV Fallback greift
    svc.clear_panel_key()
    assert svc.resolve_key() == "env_cf_key_fallback"
    assert svc.current_source() == "env"
    stat = svc.status()
    assert stat["configured"] is True
    assert stat["source"] == "env"


def test_clear_panel_key():
    svc.set_panel_key("some_key")
    assert svc.resolve_key() == "some_key"
    svc.clear_panel_key()
    assert svc.resolve_key() == ""
    assert svc.current_source() == "none"
