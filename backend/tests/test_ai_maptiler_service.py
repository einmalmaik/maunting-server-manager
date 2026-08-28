from services import ai_maptiler_service


def test_browser_map_config_is_absent_without_key(monkeypatch) -> None:
    monkeypatch.setattr(ai_maptiler_service, "get_browser_key", lambda: None)
    assert ai_maptiler_service.browser_map_config() is None


def test_browser_map_config_encodes_the_operator_key(monkeypatch) -> None:
    monkeypatch.setattr(ai_maptiler_service, "get_browser_key", lambda: "test key&value")
    config = ai_maptiler_service.browser_map_config()
    assert config is not None
    assert config["style_url"] == "https://api.maptiler.com/maps/hybrid/style.json?key=test+key%26value"
