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


logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_web_search_api_key_encrypted"
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


def _finish_inflight(cache_key: tuple[str, str, int] | None, pending: threading.Event | None) -> None:
    if cache_key is None or pending is None:
        return
    with _cache_lock:
        _inflight.pop(cache_key, None)
        pending.set()


def api_key() -> str | None:
    """Liest den hinterlegten Schluessel, oder ``None``.

    Ein Entschluesselungsfehler ist hier ausdruecklich kein Absturz: dann ist
    die Suche eben nicht verfuegbar, und der Rest des Panels laeuft weiter.
    """
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
    """Ist ein Schluessel hinterlegt?

    Faengt bewusst **alles** ab. Diese Funktion entscheidet mit, welche
    Werkzeuge der Katalog enthaelt — laeuft sie in einen Fehler, waere nicht
    nur die Suche weg, sondern der gesamte Chat. Ein nicht angebotenes
    Suchwerkzeug ist ein weit kleineres Problem als ein Assistent, der gar
    nicht mehr antwortet.
    """
    try:
        return api_key() is not None
    except Exception as exc:
        logger.warning(
            "Websuch-Konfiguration nicht lesbar error=%s", type(exc).__name__
        )
        return False


def search(query: str, count: int = MAX_RESULTS, *, cache_scope: str | None = None) -> list[dict]:
    """Fuehrt eine Suche aus und liefert minimierte, redigierte Treffer.

    Bewusst wenig je Treffer: Titel, Adresse, Kurztext. Ganze Seiteninhalte
    wuerden das Kontextbudget des Benutzers sprengen und brauchten eine eigene
    Abwaegung — die KI soll nachschlagen koennen, nicht das Web einlesen.
    """
    key = api_key()
    if key is None:
        raise WebSearchUnavailable("AI_WEB_SEARCH_NOT_CONFIGURED")
    safe_query = (query or "").strip()[:MAX_QUERY_CHARS]
    if not safe_query:
        raise WebSearchUnavailable("AI_WEB_SEARCH_QUERY_EMPTY")
    limit = max(1, min(int(count or MAX_RESULTS), MAX_RESULTS))

    # Der Scope wird ausschließlich vom autorisierten Aufrufer gesetzt. Ohne
    # Scope gibt es keinen Cache, damit Suchergebnisse niemals Nutzergrenzen
    # überschreiten.
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
    except httpx.HTTPError as exc:
        logger.info("Websuche nicht erreichbar error=%s", type(exc).__name__)
        _finish_inflight(cache_key, pending)
        raise WebSearchUnavailable("AI_WEB_SEARCH_UNAVAILABLE") from exc

    if response.status_code in {401, 403}:
        logger.warning("Websuche Authentifizierung fehlgeschlagen status=%s (API-Schlüssel prüfen)", response.status_code)
        _finish_inflight(cache_key, pending)
        raise WebSearchUnavailable("AI_WEB_SEARCH_AUTH_FAILED")
    if response.status_code == 429:
        logger.warning("Websuche Rate-Limit erreicht (429)")
        _finish_inflight(cache_key, pending)
        raise WebSearchUnavailable("AI_WEB_SEARCH_RATE_LIMITED")
    if response.status_code != 200:
        logger.warning("Websuche abgelehnt status=%s text=%s", response.status_code, response.text[:200])
        _finish_inflight(cache_key, pending)
        raise WebSearchUnavailable("AI_WEB_SEARCH_REJECTED")

    try:
        payload = response.json()
    except ValueError as exc:
        _finish_inflight(cache_key, pending)
        raise WebSearchUnavailable("AI_WEB_SEARCH_PROTOCOL_ERROR") from exc

    web = payload.get("web") if isinstance(payload, dict) else None
    entries = web.get("results") if isinstance(web, dict) else None
    if not isinstance(entries, list):
        _finish_inflight(cache_key, pending)
        return []

    results: list[dict] = []
    for entry in entries[:limit]:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")[:MAX_URL_CHARS]
        # Nur http(s). Ein `javascript:`- oder `data:`-Ziel hat in einer
        # Trefferliste nichts verloren, die spaeter als Link gerendert wird.
        if not url.startswith(("http://", "https://")):
            continue
        results.append({
            "title": redact_sensitive_text(str(entry.get("title") or ""))[:MAX_TITLE_CHARS],
            "url": url,
            "snippet": redact_sensitive_text(
                str(entry.get("description") or "")
            )[:MAX_SNIPPET_CHARS],
        })
    if cache_key and pending:
        with _cache_lock:
            _cache[cache_key] = (time.monotonic() + _CACHE_TTL_SECONDS, results)
            _inflight.pop(cache_key, None)
            pending.set()
    return results
