from __future__ import annotations

import threading
import time

from services import ai_geo_service, ai_satellite_service


class _Response:
    status_code = 200

    @staticmethod
    def json() -> list[dict[str, object]]:
        return [{
            "lat": "55.7558",
            "lon": "37.6173",
            "boundingbox": ["55.4899", "55.9575", "37.3539", "37.9674"],
            "display_name": "Moscow, Russia",
            "address": {"country": "Russia"},
        }]


def test_geocode_reuses_inflight_lookup(monkeypatch) -> None:
    calls = 0
    calls_lock = threading.Lock()

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return _Response()

    monkeypatch.setattr(ai_geo_service, "_geo_cache", {})
    monkeypatch.setattr(ai_geo_service, "_static_geo_keys", frozenset())
    monkeypatch.setattr(ai_geo_service, "_geo_cache_expires_at", {})
    monkeypatch.setattr(ai_geo_service, "_geo_inflight", {})
    class FakeClient:
        get = staticmethod(fake_get)

    monkeypatch.setattr(ai_geo_service, "_external_http_client", lambda: FakeClient())

    results: list[dict | None] = []
    threads = [threading.Thread(target=lambda: results.append(ai_geo_service.geocode_location("Example City"))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert len(results) == 2
    assert results[0] == results[1]


def test_region_analysis_fetches_weather_and_satellite_in_parallel(monkeypatch) -> None:
    monkeypatch.setattr(ai_geo_service, "geocode_location", lambda _location: {
        "name": "Example City", "country": "Exampleland", "latitude": 1.0,
        "longitude": 2.0, "bbox": [1.0, 2.0, 3.0, 4.0],
    })
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: True)

    weather_started = threading.Event()
    satellite_started = threading.Event()

    def weather(_lat, _lon):
        weather_started.set()
        assert satellite_started.wait(0.5)
        return {"temperature_celsius": 12.0, "wind_speed_kmh": 5.0, "condition": "Klar"}

    def satellite(**_kwargs):
        satellite_started.set()
        assert weather_started.wait(0.5)
        return []

    monkeypatch.setattr(ai_geo_service, "get_current_weather", weather)
    monkeypatch.setattr(ai_satellite_service, "search_satellite_imagery", satellite)

    result = ai_geo_service.analyze_region("Example City")

    assert result["status"] == "success"
    assert result["weather"]["temperature_celsius"] == 12.0
