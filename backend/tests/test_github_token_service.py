"""Tests fuer ``services.github_token_service`` (Token-Resolution).

Reihenfolge: ``MSM_GITHUB_CLONE_TOKEN`` ENV schlaegt Panel-Settings.
"""

import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _reset_panel_cache(monkeypatch):
    """Stellt sicher, dass keine echten ENV-/Panel-Werte durchschlagen."""
    monkeypatch.delenv("MSM_GITHUB_CLONE_TOKEN", raising=False)
    monkeypatch.setitem(os.environ, "MSM_GITHUB_CLONE_TOKEN", "")
    from services import github_token_service
    importlib.reload(github_token_service)
    from services.panel_settings_service import PanelSettingsService
    PanelSettingsService.invalidate_cache()
    PanelSettingsService.set("github_clone_token", "")
    yield


def test_no_token_returns_empty():
    from services.github_token_service import resolve_token, status
    assert resolve_token() == ""
    st = status()
    assert st == {"configured": False, "source": "none"}


def test_panel_token_used_when_no_env(monkeypatch):
    monkeypatch.setitem(os.environ, "MSM_GITHUB_CLONE_TOKEN", "")
    from services.github_token_service import set_panel_token, resolve_token, status

    set_panel_token("ghp_test_paneltoken")
    assert resolve_token() == "ghp_test_paneltoken"
    st = status()
    assert st["configured"] is True
    assert st["source"] == "panel"


def test_env_token_wins_over_panel(monkeypatch):
    monkeypatch.setitem(os.environ, "MSM_GITHUB_CLONE_TOKEN", "ghp_test_envtoken")
    from services.github_token_service import set_panel_token, resolve_token, status

    set_panel_token("ghp_test_paneltoken")
    assert resolve_token() == "ghp_test_envtoken"
    assert status()["source"] == "env"


def test_clear_panel_token(monkeypatch):
    monkeypatch.setitem(os.environ, "MSM_GITHUB_CLONE_TOKEN", "")
    from services.github_token_service import (
        clear_panel_token,
        resolve_token,
        set_panel_token,
        status,
    )

    set_panel_token("ghp_t")
    clear_panel_token()
    assert resolve_token() == ""
    assert status() == {"configured": False, "source": "none"}


def test_whitespace_is_trimmed(monkeypatch):
    monkeypatch.setitem(os.environ, "MSM_GITHUB_CLONE_TOKEN", "  ghp_env  ")
    from services.github_token_service import resolve_token

    assert resolve_token() == "ghp_env"
