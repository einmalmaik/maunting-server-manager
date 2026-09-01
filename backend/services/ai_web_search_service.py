"""Websuche fuer die KI ueber die Brave Search API.

Das Recht `ai.web_search.use` stand seit Monaten im Katalog und wurde an keiner
Stelle geprueft — ein Schalter, der nichts bewirkt, ist schlimmer als ein
fehlender: der Betreiber haelt etwas fuer freigeschaltet oder begrenzt, was es
nicht ist. Hier bekommt er seine Wirkung.

**Kein SSRF-Risiko.** Das Ziel ist fest verdrahtet. Anders als bei den
KI-Providern, wo der Betreiber eine eigene Basis-URL eintraegt und deshalb
`assert_provider_destination` mit IP-Pinning noetig ist, gibt es hier nichts zu
konfigurieren ausser dem Schluessel. Der Benutzer steuert nur die Suchanfrage,
niemals das Ziel.

**Alles, was zurueckkommt, ist Fremdtext.** Treffer stammen aus dem offenen
Internet und koennen von jedem geschrieben worden sein. Sie werden gekuerzt,
redigiert und vom Aufrufer als `untrusted` gekennzeichnet — dieselbe Behandlung
wie Logzeilen und Anhaenge.

**Ohne Schluessel gibt es das Werkzeug nicht.** Es wird dem Modell gar nicht
erst angeboten. Ein Werkzeug, das immer scheitert, verwirrt ein Modell mehr als
es hilft — es versucht es erneut, formuliert um und verbraucht dabei Tokens.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from services.ai_redaction import redact_sensitive_text
from services.ai_latency_metrics import measure


import html
import ipaddress
import re
import urllib.parse

logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_web_search_api_key_encrypted"
SEARXNG_SETTINGS_KEY = "ai_searxng_url"
_AAD = "msm:settings:ai_web_search_api_key"
_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

MAX_RESULTS = 5
MAX_QUERY_CHARS = 200
MAX_TITLE_CHARS = 160
MAX_SNIPPET_CHARS = 400
MAX_URL_CHARS = 300
_TIMEOUT = 15.0
_CACHE_TTL_SECONDS = 15.0
_client: httpx.Client | None = None
_client_lock = threading.Lock()
_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}
_inflight: dict[tuple[str, str, int], threading.Event] = {}
_cache_lock = threading.Lock()


class WebSearchUnavailable(RuntimeError):
    """Die Suche ist nicht durchfuehrbar. Traegt einen stabilen Code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _http_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=_TIMEOUT, write=10.0, pool=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=8),
                follow_redirects=True,
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


def _finish_inflight(cache_key: tuple[str, str, int] | None, pending: threading.Event | None) -> None:
    if cache_key is None or pending is None:
        return
    with _cache_lock:
        _inflight.pop(cache_key, None)
        pending.set()


def _is_safe_public_url(url: str) -> bool:
    """Verhindert SSRF: Erlaubt nur oeffentliche http(s)-Ziele."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False


def html_to_clean_text(html_content: str, max_len: int = 3000) -> str:
    """Extrahiert sauberen Text/Markdown aus HTML ohne Bloat."""
    if not html_content:
        return ""
    cleaned = re.sub(
        r"<(script|style|svg|noscript|header|footer|nav)[^>]*>.*?</\1>",
        " ",
        html_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(r"<(h[1-6]|p|div|li|tr|br)[^>]*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
    return cleaned.strip()[:max_len]


def scrape_url(url: str, max_chars: int = 3500) -> dict:
    """Liest eine URL direkt per HTTP ein und liefert bereinigten Inhalt."""
    clean_url = str(url or "").strip()
    if not _is_safe_public_url(clean_url):
        return {"error": "invalid_or_private_url", "url": clean_url}
    try:
        resp = _http_client().get(
            clean_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,text/plain",
            },
        )
        if resp.status_code >= 400:
            return {"error": f"http_status_{resp.status_code}", "url": clean_url}
        title_m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
        title = html.unescape(title_m.group(1)).strip() if title_m else clean_url
        content = html_to_clean_text(resp.text, max_len=max_chars)
        return {
            "title": redact_sensitive_text(title)[:MAX_TITLE_CHARS],
            "url": clean_url[:MAX_URL_CHARS],
            "content": redact_sensitive_text(content),
        }
    except Exception as exc:
        logger.info("Scrape URL fehlgeschlagen url=%s error=%s", clean_url, exc)
        return {"error": str(exc), "url": clean_url}


def api_key() -> str | None:
    """Liest den hinterlegten Brave-Schluessel, oder ``None``."""
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    stored = PanelSettingsService.get(SETTINGS_KEY, "")
    if not stored:
        return None
    try:
        return AuthService.decrypt_secret(stored, aad=_AAD) or None
    except Exception as exc:
        logger.warning(
            "Websuch-Schluessel nicht lesbar error=%s", type(exc).__name__
        )
        return None


def searxng_url() -> str | None:
    """Liest die hinterlegte SearXNG URL (oder None)."""
    from services.panel_settings_service import PanelSettingsService

    raw = PanelSettingsService.get(SEARXNG_SETTINGS_KEY, "")
    return str(raw).strip() or None


def store_api_key(plaintext: str) -> None:
    """Legt den Schluessel verschluesselt ab. Leerer Wert entfernt ihn."""
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    value = (plaintext or "").strip()
    if not value:
        PanelSettingsService.set(SETTINGS_KEY, "")
        return
    PanelSettingsService.set(SETTINGS_KEY, AuthService.encrypt_secret(value, aad=_AAD))


def is_configured() -> bool:
    """Ist ein Websuch-Provider oder Schluessel konfiguriert?"""
    try:
        return api_key() is not None or searxng_url() is not None
    except Exception as exc:
        logger.warning(
            "Websuch-Konfiguration nicht lesbar error=%s", type(exc).__name__
        )
        return False


def _search_searxng(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    s_url = searxng_url()
    if not s_url:
        return []
    try:
        resp = _http_client().get(
            f"{s_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        raw = data.get("results") or []
        hits = []
        for r in raw[:limit]:
            u = str(r.get("url") or "")
            if _is_safe_public_url(u):
                hits.append({
                    "title": redact_sensitive_text(str(r.get("title") or ""))[:MAX_TITLE_CHARS],
                    "url": u[:MAX_URL_CHARS],
                    "snippet": redact_sensitive_text(str(r.get("content") or ""))[:MAX_SNIPPET_CHARS],
                })
        return hits
    except Exception as exc:
        logger.debug("SearXNG query failed: %s", exc)
        return []


def _search_duckduckgo(query: str, limit: int = MAX_RESULTS) -> list[dict]:
    try:
        resp = _http_client().post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        if resp.status_code != 200:
            return []
        matches = re.findall(
            r'<a[^>]+class="result__snippet"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            resp.text,
            flags=re.DOTALL,
        )
        hits = []
        for url_match, snip_raw in matches[:limit]:
            clean_snip = html_to_clean_text(snip_raw, MAX_SNIPPET_CHARS)
            actual_url = url_match
            if "uddg=" in actual_url:
                m = re.search(r"uddg=([^&]+)", actual_url)
                if m:
                    actual_url = urllib.parse.unquote(m.group(1))
            if _is_safe_public_url(actual_url):
                hits.append({
                    "title": redact_sensitive_text(clean_snip[:MAX_TITLE_CHARS]),
                    "url": actual_url[:MAX_URL_CHARS],
                    "snippet": redact_sensitive_text(clean_snip)[:MAX_SNIPPET_CHARS],
                })
        return hits
    except Exception as exc:
        logger.debug("DuckDuckGo fallback failed: %s", exc)
        return []


def search(query: str, count: int = MAX_RESULTS, *, cache_scope: str | None = None) -> list[dict]:
    """Fuehrt eine Websuche oder direktes Web-Scraping aus."""
    safe_query = (query or "").strip()[:MAX_QUERY_CHARS]
    if not safe_query:
        raise WebSearchUnavailable("AI_WEB_SEARCH_QUERY_EMPTY")
    limit = max(1, min(int(count or MAX_RESULTS), MAX_RESULTS))

    key = api_key()
    s_url = searxng_url()

    if key is None and s_url is None:
        # Falls kein Schluessel und kein SearXNG hinterlegt ist, aber eine direkte URL abgefragt wird:
        url_match = re.search(r"^https?://[^\s]+$", safe_query)
        if url_match and _is_safe_public_url(url_match.group(0)):
            scraped = scrape_url(url_match.group(0))
            if "error" not in scraped:
                return [{
                    "title": scraped["title"],
                    "url": scraped["url"],
                    "snippet": scraped["content"][:MAX_SNIPPET_CHARS],
                }]
        raise WebSearchUnavailable("AI_WEB_SEARCH_NOT_CONFIGURED")

    cache_key = (cache_scope, safe_query.casefold(), limit) if cache_scope else None
    owner = False
    pending: threading.Event | None = None
    if cache_key:
        with _cache_lock:
            cached = _cache.get(cache_key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            pending = _inflight.get(cache_key)
            if pending is None:
                pending = threading.Event()
                _inflight[cache_key] = pending
                owner = True
        if not owner:
            pending.wait(_TIMEOUT + 0.5)
            with _cache_lock:
                cached = _cache.get(cache_key)
                return cached[1] if cached and cached[0] > time.monotonic() else []

    results: list[dict] = []

    # 1. SearXNG Abfrage, falls konfiguriert
    if s_url and not key:
        results = _search_searxng(safe_query, limit)

    # 2. Brave Search API Abfrage, falls Key hinterlegt
    if not results and key:
        try:
            with measure("web_search", "provider_request"):
                response = _http_client().get(
                    _ENDPOINT,
                    params={"q": safe_query, "count": limit},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": key,
                    },
                )
            if response.status_code in {401, 403}:
                logger.warning("Websuche Authentifizierung fehlgeschlagen status=%s", response.status_code)
                _finish_inflight(cache_key, pending)
                raise WebSearchUnavailable("AI_WEB_SEARCH_AUTH_FAILED")
            if response.status_code == 429:
                logger.warning("Websuche Rate-Limit erreicht (429)")
                _finish_inflight(cache_key, pending)
                raise WebSearchUnavailable("AI_WEB_SEARCH_RATE_LIMITED")
            if response.status_code == 200:
                payload = response.json()
                web = payload.get("web") if isinstance(payload, dict) else None
                entries = web.get("results") if isinstance(web, dict) else None
                if isinstance(entries, list):
                    for entry in entries[:limit]:
                        if not isinstance(entry, dict):
                            continue
                        url = str(entry.get("url") or "")[:MAX_URL_CHARS]
                        if not url.startswith(("http://", "https://")):
                            continue
                        results.append({
                            "title": redact_sensitive_text(str(entry.get("title") or ""))[:MAX_TITLE_CHARS],
                            "url": url,
                            "snippet": redact_sensitive_text(str(entry.get("description") or ""))[:MAX_SNIPPET_CHARS],
                        })
            elif response.status_code != 200:
                logger.warning("Websuche abgelehnt status=%s text=%s", response.status_code, response.text[:200])
                _finish_inflight(cache_key, pending)
                raise WebSearchUnavailable("AI_WEB_SEARCH_REJECTED")
        except WebSearchUnavailable:
            raise
        except Exception as exc:
            logger.info("Brave API nicht erreichbar error=%s", type(exc).__name__)
            _finish_inflight(cache_key, pending)
            raise WebSearchUnavailable("AI_WEB_SEARCH_UNAVAILABLE") from exc

    if cache_key and pending:
        with _cache_lock:
            _cache[cache_key] = (time.monotonic() + _CACHE_TTL_SECONDS, results)
            _inflight.pop(cache_key, None)
            pending.set()
    return results

