"""Tests für steam_api_key_service und Legacy-Account-Migration."""

import pytest

from services.panel_settings_service import PanelSettingsService
from services.steam_account_service import SteamAccountService
from services import steam_api_key_service as svc


@pytest.fixture(autouse=True)
def _clear_panel_settings(monkeypatch):
    from config import settings

    monkeypatch.delenv("MSM_STEAM_API_KEY", raising=False)
    monkeypatch.delenv("STEAM_API_KEY", raising=False)
    settings.__dict__["steam_api_key"] = ""
    PanelSettingsService.invalidate_cache()
    for key in (
        "steam_web_api_key_enc",
        "steam_web_api_key",
        "steam_account_username",
        "steam_account_password_enc",
        "steam_user",
        "steam_password",
    ):
        PanelSettingsService.set(key, "")
    yield
    PanelSettingsService.invalidate_cache()


def test_resolve_from_panel_encrypted(monkeypatch):
    svc.set_panel_key("ABCDEF0123456789ABCD")
    assert svc.resolve_key() == "ABCDEF0123456789ABCD"
    assert svc.current_source() == "panel"


def test_env_wins_over_panel(monkeypatch):
    from config import settings

    svc.set_panel_key("panel_key_only_here")
    monkeypatch.setenv("MSM_STEAM_API_KEY", "env_key_wins_here")
    settings.__dict__["steam_api_key"] = ""
    assert svc.resolve_key() == "env_key_wins_here"
    assert svc.current_source() == "env"


def test_migrate_legacy_steam_account():
    PanelSettingsService.set("steam_user", "legacyuser")
    PanelSettingsService.set("steam_password", "legacypass")
    assert SteamAccountService.migrate_legacy_if_needed() is True
    assert SteamAccountService.is_configured() is True
    assert SteamAccountService.get_username() == "legacyuser"
    assert SteamAccountService.get_decrypted_password() == "legacypass"
    assert PanelSettingsService.get("steam_user", "") == ""
    assert PanelSettingsService.get("steam_password", "") == ""