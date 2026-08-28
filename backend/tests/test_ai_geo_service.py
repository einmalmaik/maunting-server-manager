from __future__ import annotations

import threading
import time

from services import ai_geo_service, ai_regional_connectors_service, ai_satellite_service


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


def test_geocode_retries_without_postcode_when_exact_query_has_no_match(monkeypatch) -> None:
    queries: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, payload: list[dict[str, object]]) -> None:
            self._payload = payload

        def json(self) -> list[dict[str, object]]:
            return self._payload

    class FakeClient:
        @staticmethod
        def get(_url, *, params, **_kwargs):
            queries.append(params["q"])
            if params["q"] == "Testburg 12345":
                return Response([])
            return Response([{
                "lat": "53.5", "lon": "13.7",
                "boundingbox": ["53.4", "53.6", "13.6", "13.8"],
                "display_name": "Testburg, Deutschland",
                "address": {"country": "Deutschland", "postcode": "12345"},
            }])

    monkeypatch.setattr(ai_geo_service, "_geo_cache", {})
    monkeypatch.setattr(ai_geo_service, "_static_geo_keys", frozenset())
    monkeypatch.setattr(ai_geo_service, "_geo_cache_expires_at", {})
    monkeypatch.setattr(ai_geo_service, "_geo_inflight", {})
    monkeypatch.setattr(ai_geo_service, "_external_http_client", lambda: FakeClient())

    result = ai_geo_service.geocode_location(" Testburg   12345 ")

    assert queries == ["Testburg 12345", "Testburg"]
    assert result is not None
    assert result["latitude"] == 53.5
    assert result["longitude"] == 13.7


def test_geocode_rejects_mismatching_postcode_candidates(monkeypatch) -> None:
    class Response:
        status_code = 200

        @staticmethod
        def json() -> list[dict[str, object]]:
            return [{
                "lat": "50.0", "lon": "8.0",
                "display_name": "Other Testburg",
                "address": {"country": "Deutschland", "postcode": "99999"},
            }]

    class FakeClient:
        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    monkeypatch.setattr(ai_geo_service, "_geo_cache", {})
    monkeypatch.setattr(ai_geo_service, "_static_geo_keys", frozenset())
    monkeypatch.setattr(ai_geo_service, "_geo_cache_expires_at", {})
    monkeypatch.setattr(ai_geo_service, "_geo_inflight", {})
    monkeypatch.setattr(ai_geo_service, "_external_http_client", lambda: FakeClient())

    assert ai_geo_service.geocode_location("Testburg 12345") is None


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
    monkeypatch.setattr(
        ai_regional_connectors_service, "traffic", lambda *_args, **_kwargs: {"status": "available"},
    )
    monkeypatch.setattr(
        ai_regional_connectors_service, "public_posts", lambda *_args, **_kwargs: {"status": "available", "reddit": [], "bluesky": []},
    )

    result = ai_geo_service.analyze_region("Example City")

    assert result["status"] == "success"
    assert result["weather"]["temperature_celsius"] == 12.0
    assert result["traffic"] == {"status": "available"}
