from __future__ import annotations

import threading
import time

from services import ai_regional_connectors_service as connectors


class _Response:
    status_code = 200

    def __init__(self, payload: dict | None = None, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> dict:
        return self._payload or {}


def test_tomtom_traffic_uses_fixed_endpoint_and_never_returns_key(monkeypatch) -> None:
    requested: list[tuple[str, dict]] = []

    class FakeClient:
        @staticmethod
        def get(url, *, params, **_kwargs):
            requested.append((url, params))
            return _Response({"flowSegmentData": {"currentSpeed": 32, "freeFlowSpeed": 50}})

    monkeypatch.setattr(connectors, "get_tomtom_key", lambda: "synthetic-secret")
    monkeypatch.setattr(connectors, "_http_client", lambda: FakeClient())

    result = connectors.traffic(52.52, 13.405)

    assert requested == [(
        connectors._TOMTOM_FLOW_ENDPOINT,
        {"point": "52.5200,13.4050", "key": "synthetic-secret"},
    )]
    assert result == {
        "status": "available",
        "current_speed_kmh": 32,
        "free_flow_speed_kmh": 50,
        "current_travel_time_seconds": None,
        "free_flow_travel_time_seconds": None,
        "confidence": None,
        "road_closure": False,
    }
    assert "synthetic-secret" not in str(result)


def test_public_posts_session_cache_deduplicates_inflight_requests(monkeypatch) -> None:
    calls = 0
    lock = threading.Lock()

    def reddit(_query: str) -> list[dict[str, str]]:
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.02)
        return []

    monkeypatch.setattr(connectors, "_reddit_posts", reddit)
    monkeypatch.setattr(connectors, "_bluesky_posts", lambda _query: [])
    connectors.shutdown_http_client()

    results: list[dict] = []
    threads = [threading.Thread(target=lambda: results.append(
        connectors.public_posts("Berlin", cache_scope="voice:7:session-a")
    )) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert results[0] == results[1]
    assert results[0]["untrusted"] is True


def test_public_posts_reduce_and_redact_provider_payload(monkeypatch) -> None:
    class FakeClient:
        @staticmethod
        def get(url, **_kwargs):
            if url == connectors._REDDIT_SEARCH_ENDPOINT:
                return _Response({"data": {"children": [{"data": {
                    "title": "Status contact test@example.invalid",
                    "selftext": "details",
                    "permalink": "/r/berlin/comments/example/post/",
                }}]}})
            return _Response({"posts": [{
                "uri": "at://did:plc:example/app.bsky.feed.post/abc123",
                "author": {"handle": "news.example"},
                "record": {"text": "Road update"},
            }]})

    monkeypatch.setattr(connectors, "_http_client", lambda: FakeClient())
    result = connectors.public_posts("Berlin")

    assert result["reddit"][0]["url"] == "https://www.reddit.com/r/berlin/comments/example/post/"
    assert "test@example.invalid" not in result["reddit"][0]["title"]
    assert result["bluesky"][0]["url"] == "https://bsky.app/profile/news.example/post/abc123"


def test_reddit_uses_public_feed_when_json_access_is_blocked(monkeypatch) -> None:
    requested: list[str] = []
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Neue Sperrung im Zentrum</title>
        <content type="html">&lt;p&gt;Details zur aktuellen Verkehrslage.&lt;/p&gt;</content>
        <link rel="alternate" href="https://www.reddit.com/r/berlin/comments/example/lage/" />
      </entry>
    </feed>"""

    class FakeClient:
        @staticmethod
        def get(url, **_kwargs):
            requested.append(url)
            if url == connectors._REDDIT_SEARCH_ENDPOINT:
                return _Response(status_code=403)
            return _Response(text=feed)

    monkeypatch.setattr(connectors, "_http_client", lambda: FakeClient())

    assert connectors._reddit_posts("Berlin") == [{
        "title": "Neue Sperrung im Zentrum",
        "snippet": "Details zur aktuellen Verkehrslage.",
        "url": "https://www.reddit.com/r/berlin/comments/example/lage/",
    }]
    assert requested == [connectors._REDDIT_SEARCH_ENDPOINT, connectors._REDDIT_FEED_ENDPOINT]
