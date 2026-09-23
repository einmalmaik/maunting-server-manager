"""Copernicus / Sentinel Satellitendaten-Dienst für die KI.

Ermöglicht der KI den Zugriff auf Erdbeobachtungs- und Satellitendaten
des Copernicus Data Space Ecosystems (CDSE) / Sentinel-2 / Sentinel-1.

Sicherheits- & Architekturprinzipien:
1. Keine Ausführung oder Bereitstellung ohne konfigurierte Zugangsdaten (Gating).
2. Verschlüsselte Speicherung der Zugangsdaten mit AAD (AES-GCM).
3. Redaktionelle Schwärzung aller externen Metadaten vor Modellübergabe.
4. Feste API-Endpunkte (kein SSRF-Risiko).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from services.ai_redaction import redact_sensitive_text
from services.ai_latency_metrics import measure


logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_satellite_credentials_encrypted"
_AAD = "msm:settings:ai_satellite_credentials"

# Copernicus Data Space Ecosystem (CDSE) Endpunkte. Der STAC-Katalog liegt
# unter `stac.dataspace.copernicus.eu/v1`. Der alte unter
# `catalogue.dataspace.copernicus.eu/stac` ist seit dem 17.11.2025 abgelöst
# und kennt die Sammlung `SENTINEL-2` nicht mehr: Jede Suche danach endete
# mit 400, mit Copernicus-Zugang erschien nie eine Szene (geprüft 23.09.2026).
_TOKEN_ENDPOINT = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_STAC_ENDPOINT = "https://stac.dataspace.copernicus.eu/v1/search"
_TIMEOUT = 15.0
#: Nur Szenen der letzten 90 Tage. Ohne Zeitfenster sortiert der Katalog das
#: ganze Archiv seit 2015 und brauchte dafür 3 bis 21 s, mit Fenster im Median
#: 1,5 s und höchstens 3,6 s (gemessen 23.09.2026). Eine ältere Aufnahme zeigt ein Gebiet ohnehin
#: nicht mehr, wie es heute ist; dann bleibt es beim Kartenbild.
_SEARCH_WINDOW_DAYS = 90

# In-Memory Token Cache: { "token": str, "expires_at": float }
_token_cache: dict[str, Any] = {}
_token_lock = threading.Lock()
_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()
# Schlüssel: (Breite, Länge, Anzahl, höchste Bewölkung)
_SearchKey = tuple[float, float, int, float]
_search_cache: dict[_SearchKey, tuple[float, list[dict[str, Any]]]] = {}
_search_inflight: dict[_SearchKey, threading.Event] = {}
_search_lock = threading.Lock()
_SEARCH_CACHE_TTL_SECONDS = 60.0
# Vorschau-Adressen gefundener Szenen, nach Szenen-ID. Das Kartenbild holt das
# Panel selbst (`ai_geo_image_service`) und nimmt die Adresse nur von hier —
# nie aus einer Anfrage. Eine Stunde reicht für jedes Gespräch über einen Ort.
_preview_hrefs: dict[str, tuple[float, str]] = {}
_PREVIEW_TTL_SECONDS = 3600.0
_PREVIEW_MAX_ENTRIES = 256


def _finish_search(cache_key: _SearchKey, pending: threading.Event) -> None:
    with _search_lock:
        _search_inflight.pop(cache_key, None)
        pending.set()


def _external_http_client() -> httpx.Client:
    global _http_client
    with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=_TIMEOUT, write=10.0, pool=5.0),
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
                follow_redirects=False,
            )
        return _http_client


def shutdown_http_client() -> None:
    global _http_client
    with _http_client_lock:
        if _http_client is not None:
            _http_client.close()
            _http_client = None


class SatelliteUnavailable(RuntimeError):
    """Die Satellitenanalyse ist nicht durchführbar. Trägt einen stabilen Fehlercode."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def get_credentials() -> dict[str, str] | None:
    """Liest die hinterlegten Copernicus CDSE Zugangsdaten oder None."""
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    stored = PanelSettingsService.get(SETTINGS_KEY, "")
    if not stored:
        return None
    try:
        raw = AuthService.decrypt_secret(stored, aad=_AAD)
        if not raw:
            return None
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("client_id") and data.get("client_secret"):
            return {
                "client_id": str(data["client_id"]).strip(),
                "client_secret": str(data["client_secret"]).strip(),
            }
        return None
    except Exception as exc:
        logger.warning("Satelliten-Zugangsdaten nicht lesbar error=%s", type(exc).__name__)
        return None


def store_credentials(client_id: str, client_secret: str) -> None:
    """Legt die Zugangsdaten verschlüsselt ab. Leere Werte entfernen sie."""
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    cid = (client_id or "").strip()
    csec = (client_secret or "").strip()
    if not cid or not csec:
        PanelSettingsService.set(SETTINGS_KEY, "")
        with _token_lock:
            _token_cache.clear()
        with _search_lock:
            _search_cache.clear()
            _preview_hrefs.clear()
        return
    payload = json.dumps({"client_id": cid, "client_secret": csec})
    PanelSettingsService.set(SETTINGS_KEY, AuthService.encrypt_secret(payload, aad=_AAD))
    with _token_lock:
        _token_cache.clear()
    with _search_lock:
        _search_cache.clear()
        _preview_hrefs.clear()


def is_configured() -> bool:
    """Prüft, ob der Satellitendienst konfiguriert und einsatzbereit ist."""
    try:
        creds = get_credentials()
        return creds is not None
    except Exception as exc:
        logger.warning("Satelliten-Konfiguration nicht lesbar error=%s", type(exc).__name__)
        return False


def _get_access_token(creds: dict[str, str]) -> str:
    """Holt ein OAuth2-Access-Token von CDSE mit automatischem Caching."""
    # Gleichzeitige Geo-Prefetches dürfen nur einen OAuth-Abruf auslösen. Das
    # Schloss schützt nur diesen seltenen Kaltstart, nie die STAC-Suche selbst.
    with _token_lock:
        now = time.time()
        cached = _token_cache.get("token")
        expires_at = _token_cache.get("expires_at", 0)
        if cached and now < expires_at - 30:
            return str(cached)

        try:
            with measure("satellite", "token_request"):
                resp = _external_http_client().post(
                    _TOKEN_ENDPOINT,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": creds["client_id"],
                        "client_secret": creds["client_secret"],
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            logger.info("CDSE Token-Endpunkt nicht erreichbar error=%s", type(exc).__name__)
            raise SatelliteUnavailable("AI_SATELLITE_UNAVAILABLE") from exc

        if resp.status_code in {400, 401, 403}:
            raise SatelliteUnavailable("AI_SATELLITE_AUTH_FAILED")
        if resp.status_code != 200:
            raise SatelliteUnavailable("AI_SATELLITE_UNAVAILABLE")

        try:
            data = resp.json()
            token = data.get("access_token")
            expires_in = int(data.get("expires_in", 300))
            if not token:
                raise SatelliteUnavailable("AI_SATELLITE_PROTOCOL_ERROR")
            _token_cache["token"] = token
            _token_cache["expires_at"] = now + expires_in
            return str(token)
        except Exception as exc:
            raise SatelliteUnavailable("AI_SATELLITE_PROTOCOL_ERROR") from exc


def search_satellite_imagery(
    latitude: float,
    longitude: float,
    limit: int = 3,
    max_cloud_cover: float = 30.0,
) -> list[dict[str, Any]]:
    """Sucht die neuesten Sentinel-2-Aufnahmen (L2A), die diesen Ort zeigen.

    Gesucht wird am Punkt, nicht in der Box des Geocoders: Die reicht oft weit
    über den Ort hinaus, und eine Kachel am Rand der Box ist genauso neu wie
    eine über dem Ort. Für Berlin enthielt keiner der beiden neuesten Treffer
    der Box die Stadt, sie lagen östlich und südöstlich von ihr.
    """
    cache_key = (round(float(latitude), 5), round(float(longitude), 5), int(limit), float(max_cloud_cover))
    now = time.monotonic()
    with _search_lock:
        cached = _search_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]
        pending = _search_inflight.get(cache_key)
        if pending is None:
            pending = threading.Event()
            _search_inflight[cache_key] = pending
            owner = True
        else:
            owner = False

    if not owner:
        pending.wait(_TIMEOUT + 0.5)
        with _search_lock:
            cached = _search_cache.get(cache_key)
            return cached[1] if cached and cached[0] > time.monotonic() else []

    creds = get_credentials()
    if not creds:
        _finish_search(cache_key, pending)
        raise SatelliteUnavailable("AI_SATELLITE_NOT_CONFIGURED")

    # Die Suche selbst ist öffentlich; ein Token prüft der Katalog nicht.
    # Gesucht wird trotzdem nur mit gültigem Zugang: Copernicus ist eine
    # Entscheidung des Betreibers, ohne sie bleibt es beim Kartenbild.
    try:
        token = _get_access_token(creds)
    except SatelliteUnavailable:
        _finish_search(cache_key, pending)
        raise
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/geo+json, application/json",
        "Content-Type": "application/json",
    }

    since = datetime.now(timezone.utc) - timedelta(days=_SEARCH_WINDOW_DAYS)
    body = {
        "collections": ["sentinel-2-l2a"],
        "intersects": {"type": "Point", "coordinates": [float(longitude), float(latitude)]},
        "datetime": f"{since:%Y-%m-%dT%H:%M:%SZ}/..",
        "limit": limit,
        "filter-lang": "cql2-json",
        "filter": {"op": "<=", "args": [{"property": "eo:cloud_cover"}, max_cloud_cover]},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        # Nur, was hier gebraucht wird: Ein Treffer trägt sonst 47 Assets
        # (Bänder und Metadateien) und wiegt rund 70 statt 1,5 KB. Ignoriert
        # ein Server die Auswahl, liest sich die volle Antwort genauso.
        "fields": {
            "include": [
                "id", "properties.datetime", "properties.eo:cloud_cover", "assets.thumbnail.href", "geometry",
            ],
        },
    }

    try:
        with measure("satellite", "stac_search"):
            resp = _external_http_client().post(_STAC_ENDPOINT, json=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.info("CDSE STAC-Suche nicht erreichbar error=%s", type(exc).__name__)
        _finish_search(cache_key, pending)
        raise SatelliteUnavailable("AI_SATELLITE_UNAVAILABLE") from exc

    if resp.status_code in {401, 403}:
        with _token_lock:
            _token_cache.clear()
        _finish_search(cache_key, pending)
        raise SatelliteUnavailable("AI_SATELLITE_AUTH_FAILED")
    if resp.status_code != 200:
        logger.info("CDSE STAC-Suche fehlgeschlagen status=%s", resp.status_code)
        _finish_search(cache_key, pending)
        raise SatelliteUnavailable("AI_SATELLITE_UNAVAILABLE")

    try:
        data = resp.json()
    except Exception as exc:
        _finish_search(cache_key, pending)
        raise SatelliteUnavailable("AI_SATELLITE_PROTOCOL_ERROR") from exc

    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        _finish_search(cache_key, pending)
        return []

    results: list[dict[str, Any]] = []
    for feat in features[:limit]:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        assets = feat.get("assets") or {}
        feat_id = redact_sensitive_text(str(feat.get("id") or ""))
        dt = str(props.get("datetime") or "")
        clouds = props.get("eo:cloud_cover")
        # Die Schnellansicht der Kachel (343 × 343 px, JPEG). Die anderen
        # Assets sind Bänder und Metadateien der vollen Szene.
        thumbnail = assets.get("thumbnail")
        preview_url = str(thumbnail.get("href") or "") if isinstance(thumbnail, dict) else ""

        results.append({
            "id": feat_id[:120],
            "mission": "Sentinel-2 L2A",
            "datetime": dt[:40],
            "cloud_cover_percent": round(float(clouds), 1) if isinstance(clouds, (int, float)) else None,
            "preview_url": preview_url if preview_url.startswith(("http://", "https://")) else "",
            "geometry": feat.get("geometry"),
        })

    with _search_lock:
        _search_cache[cache_key] = (time.monotonic() + _SEARCH_CACHE_TTL_SECONDS, results)
        _search_inflight.pop(cache_key, None)
        pending.set()
    _remember_previews(results)
    return results


def _remember_previews(results: list[dict[str, Any]]) -> None:
    now = time.monotonic()
    with _search_lock:
        for scene in results:
            if scene.get("id") and scene.get("preview_url"):
                _preview_hrefs[str(scene["id"])] = (now + _PREVIEW_TTL_SECONDS, str(scene["preview_url"]))
        for stale in [key for key, (expires, _href) in _preview_hrefs.items() if expires <= now]:
            del _preview_hrefs[stale]
        while len(_preview_hrefs) > _PREVIEW_MAX_ENTRIES:
            del _preview_hrefs[min(_preview_hrefs, key=lambda key: _preview_hrefs[key][0])]


def preview_href(scene_id: str) -> str | None:
    """Die Vorschau-Adresse einer zuletzt gefundenen Szene, oder None."""
    with _search_lock:
        entry = _preview_hrefs.get(scene_id)
    if entry is None or entry[0] <= time.monotonic():
        return None
    return entry[1]


def access_token() -> str | None:
    """Ein gültiges CDSE-Token für Copernicus-eigene Adressen, oder None."""
    creds = get_credentials()
    if not creds:
        return None
    try:
        return _get_access_token(creds)
    except SatelliteUnavailable:
        return None
