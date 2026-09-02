"""Read-only regional signals from fixed, public provider endpoints.

The TomTom operator key stays server-side and encrypted at rest.  Reddit and
Bluesky receive only the already redacted location query.  Provider responses
are untrusted input and are reduced before they leave this service.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from typing import Any, Callable
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from services.ai_latency_metrics import measure
from services.ai_redaction import redact_sensitive_text


logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_tomtom_traffic_key_encrypted"
_AAD = "msm:settings:ai_tomtom_traffic_key"
_TOMTOM_FLOW_ENDPOINT = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
_REDDIT_SEARCH_ENDPOINT = "https://www.reddit.com/search.json"
_REDDIT_FEED_ENDPOINT = "https://www.reddit.com/search.rss"
_BLUESKY_SEARCH_ENDPOINT = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
_TIMEOUT = 4.0
_CACHE_TTL_SECONDS = 20.0
_MAX_QUERY_CHARS = 100
_MAX_RESULTS = 3

_TOMTOM_FAILURES = {
    401: "invalid_key",
    403: "traffic_not_enabled",
    404: "no_coverage",
    429: "rate_limited",
}

_client: httpx.Client | None = None
_client_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_inflight: dict[tuple[str, str], threading.Event] = {}
_cache_lock = threading.Lock()


def _http_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(connect=3.0, read=_TIMEOUT, write=3.0, pool=3.0),
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
                follow_redirects=False,
            )
        return _client


def shutdown_http_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None
    with _cache_lock:
        _cache.clear()
        _inflight.clear()


def get_tomtom_key() -> str | None:
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    stored = PanelSettingsService.get(SETTINGS_KEY, "")
    if not stored:
        return None
    try:
        return AuthService.decrypt_secret(stored, aad=_AAD).strip() or None
    except Exception as exc:
        logger.warning("TomTom-Konfiguration nicht lesbar error=%s", type(exc).__name__)
        return None


def store_tomtom_key(plaintext: str) -> None:
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    value = (plaintext or "").strip()
    PanelSettingsService.set(
        SETTINGS_KEY,
        AuthService.encrypt_secret(value, aad=_AAD) if value else "",
    )


def is_tomtom_configured() -> bool:
    return get_tomtom_key() is not None


def _cached(
    provider: str,
    request_key: str,
    cache_scope: str | None,
    fetch: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Cache only inside a caller-owned session and coalesce concurrent calls."""
    if not cache_scope:
        return fetch()
    key = (f"{cache_scope}:{provider}", request_key)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        pending = _inflight.get(key)
        if pending is None:
            pending = threading.Event()
            _inflight[key] = pending
            owner = True
        else:
            owner = False
    if not owner:
        pending.wait(_TIMEOUT + 0.5)
        with _cache_lock:
            cached = _cache.get(key)
            return cached[1] if cached and cached[0] > time.monotonic() else {"status": "unavailable"}

    try:
        result = fetch()
        with _cache_lock:
            _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
        return result
    finally:
        with _cache_lock:
            event = _inflight.pop(key, None)
            if event is not None:
                event.set()


def traffic(latitude: float, longitude: float, *, cache_scope: str | None = None) -> dict[str, Any]:
    """Return one traffic-flow segment; never expose the operator key."""
    key = get_tomtom_key()
    if not key:
        return {"status": "not_configured"}
    request_key = f"{latitude:.4f},{longitude:.4f}"

    def fetch() -> dict[str, Any]:
        try:
            with measure("regional_connectors", "tomtom_traffic_request"):
                response = _http_client().get(
                    _TOMTOM_FLOW_ENDPOINT,
                    params={"point": request_key, "key": key},
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.info("TomTom-Verkehr nicht erreichbar error=%s", type(exc).__name__)
            return {"status": "unavailable", "reason": "network_error"}
        if response.status_code != 200:
            try:
                body = response.json()
                code = (body.get("detailedError") or {}).get("code") if isinstance(body, dict) else None
            except Exception:
                code = None
            if code == "InvalidReferer":
                try:
                    from config import settings as _settings
                    _referer = str(getattr(_settings, "panel_url", "") or "").strip() or "https://msm.mauntingstudios.de/"
                    with measure("regional_connectors", "tomtom_traffic_request_retry"):
                        response = _http_client().get(
                            _TOMTOM_FLOW_ENDPOINT,
                            params={"point": request_key, "key": key},
                            headers={"Accept": "application/json", "Referer": _referer},
                        )
                    if response.status_code == 200:
                        try:
                            segment = response.json().get("flowSegmentData", {})
                            if isinstance(segment, dict):
                                return {
                                    "status": "available",
                                    "current_speed_kmh": segment.get("currentSpeed"),
                                    "free_flow_speed_kmh": segment.get("freeFlowSpeed"),
                                    "current_travel_time_seconds": segment.get("currentTravelTime"),
                                    "free_flow_travel_time_seconds": segment.get("freeFlowTravelTime"),
                                    "confidence": segment.get("confidence"),
                                    "road_closure": bool(segment.get("roadClosure")),
                                }
                        except Exception:
                            pass
                except Exception:
                    pass
                logger.info("TomTom-Verkehr nicht verfuegbar status=%s reason=invalid_referer", response.status_code)
                return {"status": "unavailable", "reason": "invalid_referer"}
            reason = _TOMTOM_FAILURES.get(response.status_code, "provider_error")
            logger.info("TomTom-Verkehr nicht verfuegbar status=%s reason=%s", response.status_code, reason)
            return {"status": "unavailable", "reason": reason}
        try:
            segment = response.json().get("flowSegmentData", {})
        except (ValueError, AttributeError):
            return {"status": "unavailable", "reason": "invalid_response"}
        if not isinstance(segment, dict):
            return {"status": "unavailable", "reason": "invalid_response"}
        return {
            "status": "available",
            "current_speed_kmh": segment.get("currentSpeed"),
            "free_flow_speed_kmh": segment.get("freeFlowSpeed"),
            "current_travel_time_seconds": segment.get("currentTravelTime"),
            "free_flow_travel_time_seconds": segment.get("freeFlowTravelTime"),
            "confidence": segment.get("confidence"),
            "road_closure": bool(segment.get("roadClosure")),
        }

    return _cached("tomtom", request_key, cache_scope, fetch)


def _safe_text(value: object, limit: int) -> str:
    # Öffentliche Beiträge können trotzdem private Kontaktangaben enthalten.
    # Sie gehören weder in die Oberfläche noch in den Modellkontext.
    text = redact_sensitive_text(str(value or ""))
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[redacted]", text)
    return text[:limit]


def public_posts(location: str, *, cache_scope: str | None = None) -> dict[str, Any]:
    """Fetch a small, explicitly untrusted public conversation sample."""
    query = _safe_text(location, _MAX_QUERY_CHARS).strip()
    if not query:
        return {"status": "unavailable", "reddit": [], "bluesky": []}

    def fetch() -> dict[str, Any]:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="msm-social") as executor:
            reddit_future = executor.submit(_reddit_posts, query)
            bluesky_future = executor.submit(_bluesky_posts, query)
        return {
            "status": "available",
            "reddit": reddit_future.result(),
            "bluesky": bluesky_future.result(),
            "untrusted": True,
        }

    return _cached("public_posts", query.casefold(), cache_scope, fetch)


def _reddit_posts(query: str) -> list[dict[str, str]]:
    try:
        with measure("regional_connectors", "reddit_search_request"):
            response = _http_client().get(
                _REDDIT_SEARCH_ENDPOINT,
                params={"q": query, "limit": _MAX_RESULTS, "sort": "new", "raw_json": 1},
                headers={"Accept": "application/json", "User-Agent": "MSM-Server-Manager/3.0"},
            )
        if response.status_code != 200:
            return _reddit_feed_posts(query)
        children = response.json().get("data", {}).get("children", [])
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.info("Reddit-Suche nicht verfuegbar error=%s", type(exc).__name__)
        return _reddit_feed_posts(query)
    results: list[dict[str, str]] = []
    for child in children[:_MAX_RESULTS] if isinstance(children, list) else []:
        data = child.get("data", {}) if isinstance(child, dict) else {}
        permalink = data.get("permalink") if isinstance(data, dict) else ""
        if not isinstance(permalink, str) or not permalink.startswith("/r/"):
            continue
        results.append({
            "title": _safe_text(data.get("title"), 160),
            "snippet": _safe_text(
                data.get("selftext") or data.get("subreddit_name_prefixed"),
                300,
            ),
            "url": f"https://www.reddit.com{permalink[:300]}",
        })
    return results or _reddit_feed_posts(query)


def _reddit_feed_posts(query: str) -> list[dict[str, str]]:
    """Use Reddit's public Atom feed when anonymous JSON access is blocked."""
    try:
        with measure("regional_connectors", "reddit_feed_request"):
            response = _http_client().get(
                _REDDIT_FEED_ENDPOINT,
                params={"q": query, "limit": _MAX_RESULTS, "sort": "new"},
                headers={
                    "Accept": "application/atom+xml, application/rss+xml",
                    "User-Agent": "MSM-Server-Manager/3.0",
                },
            )
        if response.status_code != 200:
            return []
        root = ElementTree.fromstring(response.text)
    except (httpx.HTTPError, ElementTree.ParseError, AttributeError) as exc:
        logger.info("Reddit-Feed nicht verfuegbar error=%s", type(exc).__name__)
        return []

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    results: list[dict[str, str]] = []
    for entry in root.findall("atom:entry", namespace)[:_MAX_RESULTS]:
        title = _safe_text(entry.findtext("atom:title", default="", namespaces=namespace), 160)
        content = entry.findtext("atom:content", default="", namespaces=namespace)
        summary = entry.findtext("atom:summary", default="", namespaces=namespace)
        snippet = re.sub(r"<[^>]+>", " ", unescape(content or summary or ""))
        snippet = _safe_text(re.sub(r"\s+", " ", snippet).strip(), 300)
        link_element = entry.find("atom:link[@rel='alternate']", namespace)
        if link_element is None:
            link_element = entry.find("atom:link", namespace)
        url = link_element.get("href", "") if link_element is not None else ""
        parsed = urlparse(url)
        if (
            not title
            or parsed.scheme != "https"
            or parsed.hostname not in {"reddit.com", "www.reddit.com"}
            or not parsed.path.startswith("/r/")
        ):
            continue
        results.append({"title": title, "snippet": snippet, "url": url[:300]})
    return results


def _bluesky_posts(query: str) -> list[dict[str, str]]:
    try:
        with measure("regional_connectors", "bluesky_search_request"):
            response = _http_client().get(
                _BLUESKY_SEARCH_ENDPOINT,
                params={"q": query, "limit": _MAX_RESULTS},
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            return []
        posts = response.json().get("posts", [])
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.info("Bluesky-Suche nicht verfuegbar error=%s", type(exc).__name__)
        return []
    results: list[dict[str, str]] = []
    for post in posts[:_MAX_RESULTS] if isinstance(posts, list) else []:
        if not isinstance(post, dict):
            continue
        record = post.get("record", {})
        author = post.get("author", {})
        uri = post.get("uri", "")
        handle = author.get("handle", "") if isinstance(author, dict) else ""
        parts = uri.rsplit("/", 1) if isinstance(uri, str) else []
        if not isinstance(handle, str) or not handle or len(parts) != 2 or not parts[1]:
            continue
        results.append({
            "author": _safe_text(handle, 100),
            "text": _safe_text(record.get("text") if isinstance(record, dict) else "", 300),
            "url": f"https://bsky.app/profile/{handle[:100]}/post/{parts[1][:100]}",
        })
    return results
