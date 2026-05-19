from __future__ import annotations

from app import config


def test_production_defaults_https_only_when_not_set(monkeypatch):
    config.get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    monkeypatch.delenv("PANEL_HTTPS_ONLY", raising=False)

    settings = config.get_settings()

    assert settings.https_only is True
    config.get_settings.cache_clear()


def test_non_production_keeps_https_only_disabled_by_default(monkeypatch):
    config.get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    monkeypatch.delenv("PANEL_HTTPS_ONLY", raising=False)

    settings = config.get_settings()

    assert settings.https_only is False
    config.get_settings.cache_clear()
