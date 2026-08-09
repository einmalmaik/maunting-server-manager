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

import httpx

from services.ai_redaction import redact_sensitive_text


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


class WebSearchUnavailable(RuntimeError):
    """Die Suche ist nicht durchfuehrbar. Traegt einen stabilen Code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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


def search(query: str, count: int = MAX_RESULTS) -> list[dict]:
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

    try:
        response = httpx.get(
            _ENDPOINT,
            params={"q": safe_query, "count": limit},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": key,
            },
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        logger.info("Websuche nicht erreichbar error=%s", type(exc).__name__)
        raise WebSearchUnavailable("AI_WEB_SEARCH_UNAVAILABLE") from exc

    if response.status_code in {401, 403}:
        raise WebSearchUnavailable("AI_WEB_SEARCH_AUTH_FAILED")
    if response.status_code == 429:
        raise WebSearchUnavailable("AI_WEB_SEARCH_RATE_LIMITED")
    if response.status_code != 200:
        logger.info("Websuche abgelehnt status=%s", response.status_code)
        raise WebSearchUnavailable("AI_WEB_SEARCH_REJECTED")

    try:
        payload = response.json()
    except ValueError as exc:
        raise WebSearchUnavailable("AI_WEB_SEARCH_PROTOCOL_ERROR") from exc

    web = payload.get("web") if isinstance(payload, dict) else None
    entries = web.get("results") if isinstance(web, dict) else None
    if not isinstance(entries, list):
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
    return results
