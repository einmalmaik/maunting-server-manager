"""Geodaten-, Geocoding- und Regionsanalyse-Dienst für die KI.

Löst Ortsnamen in Koordinaten (WGS84) und Bounding-Boxes auf,
ruft Wetter- und Umweltparameter ab und verknüpft sie mit den
Satellitendaten aus Copernicus/Sentinel.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
from typing import Any
import httpx

from services.ai_redaction import redact_sensitive_text
from services import ai_satellite_service
from services.ai_latency_metrics import measure


logger = logging.getLogger(__name__)

_NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
_OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 3.0
_GEO_CACHE_TTL_SECONDS = 300.0
_GEO_CACHE_MAX_DYNAMIC_ENTRIES = 128
_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()

# In-Memory Cache für Geocoding (Ortsname -> Geo-Objekt)
_geo_cache: dict[str, dict[str, Any]] = {
    "berlin": {
        "name": "Berlin, Deutschland",
        "latitude": 52.5200,
        "longitude": 13.4050,
        "bbox": [13.0883, 52.3382, 13.7611, 52.6755],
        "country": "Deutschland",
    },
    "washington": {
        "name": "Washington, D.C., USA",
        "latitude": 38.8951,
        "longitude": -77.0364,
        "bbox": [-77.1197, 38.7916, -76.9093, 38.9955],
        "country": "USA",
    },
    "tokio": {
        "name": "Tokio, Japan",
        "latitude": 35.6762,
        "longitude": 139.6503,
        "bbox": [138.9427, 35.5288, 139.9213, 35.8984],
        "country": "Japan",
    },
    "paris": {
        "name": "Paris, Frankreich",
        "latitude": 48.8566,
        "longitude": 2.3522,
        "bbox": [2.2241, 48.8155, 2.4699, 48.9021],
        "country": "Frankreich",
    },
    "london": {
        "name": "London, Vereinigtes Königreich",
        "latitude": 51.5074,
        "longitude": -0.1278,
        "bbox": [-0.5103, 51.2867, 0.3340, 51.6918],
        "country": "Vereinigtes Königreich",
    },
    "moscow": {
        "name": "Moscow, Russia",
        "latitude": 55.7558,
        "longitude": 37.6173,
        "bbox": [37.3539, 55.4899, 37.9674, 55.9575],
        "country": "Russia",
    },
    "moskau": {
        "name": "Moscow, Russia",
        "latitude": 55.7558,
        "longitude": 37.6173,
        "bbox": [37.3539, 55.4899, 37.9674, 55.9575],
        "country": "Russia",
    },
}
_static_geo_keys = frozenset(_geo_cache)
_geo_cache_expires_at: dict[str, float] = {}
_geo_inflight: dict[str, threading.Event] = {}
_geo_cache_lock = threading.Lock()


def _external_http_client() -> httpx.Client:
    global _http_client
    with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.Client(
                timeout=httpx.Timeout(connect=3.0, read=_TIMEOUT, write=5.0, pool=3.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=8),
                follow_redirects=False,
            )
        return _http_client


def shutdown_http_client() -> None:
    global _http_client
    with _http_client_lock:
        if _http_client is not None:
            _http_client.close()
            _http_client = None

# WMO Weather Code Übersetzungen
WMO_CODES: dict[int, str] = {
    0: "Klarer Himmel",
    1: "Überwiegend sonnig",
    2: "Teilweise bewölkt",
    3: "Bedeckt",
    45: "Neblig",
    48: "Reifnebel",
    51: "Leichter Nieselregen",
    53: "Mäßiger Nieselregen",
    55: "Dichter Nieselregen",
    61: "Leichter Regen",
    63: "Mäßiger Regen",
    65: "Starker Regen",
    71: "Leichter Schneefall",
    73: "Mäßiger Schneefall",
    75: "Starker Schneefall",
    80: "Leichte Regenschauer",
    81: "Mäßige Regenschauer",
    82: "Heftige Regenschauer",
    95: "Gewitter",
}


def geocode_location(location_name: str) -> dict[str, Any] | None:
    """Löst einen Ortsnamen in Koordinaten und eine Bounding-Box auf."""
    query = (location_name or "").strip()
    if not query:
        return None

    clean_key = query.lower()
    with _geo_cache_lock:
        cached = _geo_cache.get(clean_key)
        expires_at = _geo_cache_expires_at.get(clean_key)
        if cached and (clean_key in _static_geo_keys or (expires_at is not None and expires_at > time.monotonic())):
            return cached
        if cached:
            _geo_cache.pop(clean_key, None)
            _geo_cache_expires_at.pop(clean_key, None)

        pending = _geo_inflight.get(clean_key)
        if pending is None:
            pending = threading.Event()
            _geo_inflight[clean_key] = pending
            owner = True
        else:
            owner = False

    # Die Vorschau und der eigentliche Prefetch können denselben Ort fast
    # gleichzeitig anfragen. Nur der erste Thread darf Nominatim ansprechen;
    # der zweite übernimmt anschließend das gecachte Ergebnis.
    if not owner:
        pending.wait(_TIMEOUT + 0.5)
        with _geo_cache_lock:
            return _geo_cache.get(clean_key)

    try:
        with measure("geo", "geocode_request"):
            resp = _external_http_client().get(
                _NOMINATIM_ENDPOINT,
                params={
                    "q": query[:100],
                    "format": "jsonv2",
                    "limit": 1,
                    "addressdetails": 1,
                },
                headers={"User-Agent": "MSM-Server-Manager/3.0 (RegionalAnalysis)"},
            )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                item = data[0]
                lat = float(item["lat"])
                lon = float(item["lon"])
                bb = item.get("boundingbox", [])  # [min_lat, max_lat, min_lon, max_lon]
                if len(bb) == 4:
                    bbox = [float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])]
                else:
                    bbox = [lon - 0.05, lat - 0.05, lon + 0.05, lat + 0.05]

                address = item.get("address", {})
                country = address.get("country", "")

                result = {
                    "name": redact_sensitive_text(str(item.get("display_name") or query)),
                    "latitude": lat,
                    "longitude": lon,
                    "bbox": bbox,
                    "country": redact_sensitive_text(country),
                }
                with _geo_cache_lock:
                    dynamic_keys = [key for key in _geo_cache if key not in _static_geo_keys]
                    while len(dynamic_keys) >= _GEO_CACHE_MAX_DYNAMIC_ENTRIES:
                        oldest = min(dynamic_keys, key=lambda key: _geo_cache_expires_at.get(key, 0.0))
                        _geo_cache.pop(oldest, None)
                        _geo_cache_expires_at.pop(oldest, None)
                        dynamic_keys.remove(oldest)
                    _geo_cache[clean_key] = result
                    _geo_cache_expires_at[clean_key] = time.monotonic() + _GEO_CACHE_TTL_SECONDS
                return result
    except Exception as exc:
        logger.info("Geocoding fehlgeschlagen error=%s", type(exc).__name__)
    finally:
        with _geo_cache_lock:
            event = _geo_inflight.pop(clean_key, None)
            if event is not None:
                event.set()

    return None


def get_current_weather(latitude: float, longitude: float) -> dict[str, Any] | None:
    """Ruft aktuelle Wetterdaten für die Koordinaten ab (Open-Meteo)."""
    try:
        with measure("geo", "weather_request"):
            resp = _external_http_client().get(
                _OPEN_METEO_ENDPOINT,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                },
            )
        if resp.status_code != 200:
            return None
        data = resp.json()
        current = data.get("current")
        if not isinstance(current, dict):
            return None

        w_code = current.get("weather_code", 0)
        condition = WMO_CODES.get(int(w_code), "Unbekannt")

        return {
            "temperature_celsius": current.get("temperature_2m"),
            "apparent_temperature_celsius": current.get("apparent_temperature"),
            "humidity_percent": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "condition": condition,
        }
    except Exception as exc:
        logger.info("Wetterabfrage fehlgeschlagen lat=%s lon=%s error=%s", latitude, longitude, type(exc).__name__)
        return None


def analyze_region(location_name: str) -> dict[str, Any]:
    """Führt eine kombinierte Analyse für einen Ort durch (Geodaten + Wetter + Satellit)."""
    geo = geocode_location(location_name)
    if not geo:
        return {
            "status": "error",
            "error_code": "LOCATION_NOT_FOUND",
            "message": f"Der Ort '{location_name}' konnte nicht eindeutig geocodiert werden.",
        }

    lat = geo["latitude"]
    lon = geo["longitude"]
    bbox = geo["bbox"]

    satellite_configured = ai_satellite_service.is_configured()

    def satellite_search() -> list[dict[str, Any]]:
        if not satellite_configured:
            return []
        try:
            return ai_satellite_service.search_satellite_imagery(bbox=bbox, limit=2)
        except Exception as exc:
            logger.info("Satellitenbildsuche nicht erfolgreich error=%s", type(exc).__name__)
            return []

    # Wetter und die optionale CDSE-Suche sind unabhängig. Parallelisierung
    # verhindert, dass eine langsame Satellitenantwort die Wetterabfrage noch
    # zusätzlich verlängert.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="msm-geo") as executor:
        weather_future = executor.submit(get_current_weather, lat, lon)
        satellite_future = executor.submit(satellite_search)
        weather = weather_future.result()
        satellite_data = satellite_future.result()
    weather = weather or {}

    min_lon, min_lat, max_lon, max_lat = bbox

    # Ein ehrliches Lagebild statt Filter, die eine aktuelle Aufnahme nur
    # vortäuschen würden. ArcGIS liefert ein Bildmosaik; Zeitpunkt und native
    # Auflösung unterscheiden sich je Kachel.
    true_color_url = (
        f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?"
        f"bbox={min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}&bboxSR=4326&imageSR=4326&size=1600,1200&format=jpg&f=image"
    )
    preview_scene = next((scene for scene in satellite_data if scene.get("preview_url")), None)
    if preview_scene:
        preview_url = str(preview_scene["preview_url"])
        source_name = str(preview_scene.get("mission") or "Copernicus")
        source_resolution = "gemäß Szene"
        source_description = "Neueste in Copernicus gefundene Szene. Zeitpunkt und Bewölkung stehen unten."
    else:
        preview_url = true_color_url
        source_name = "ArcGIS World Imagery"
        source_resolution = "anbieterabhängig"
        source_description = "Aktuelles verfügbares Bildmosaik. Aufnahmezeit und native Auflösung variieren je Kachel."
        satellite_data = [{
            "id": f"world-imagery-{lat:.4f}-{lon:.4f}",
            "mission": source_name,
            "datetime": "",
            "cloud_cover_percent": None,
            "preview_url": preview_url,
        }]

    layers: dict[str, dict[str, Any]] = {
        "latest_imagery": {
            "id": "latest_imagery",
            "name": "Aktuellste verfügbare Bilddaten",
            "url": preview_url,
            "resolution": source_resolution,
            "mission": source_name,
            "description": source_description,
        },
    }
    for scene in satellite_data:
        scene["layers"] = layers

    loc_name = geo["name"]
    loc_country = geo["country"]

    return {
        "status": "success",
        "location": loc_name,
        "country": loc_country,
        "coordinates": {
            "latitude": lat,
            "longitude": lon,
            "bbox": bbox,
        },
        "weather": weather,
        "satellite": {
            "available": len(satellite_data) > 0,
            "scenes": satellite_data,
            "layers": layers,
        },
        # Diese Analyse beschafft keine Nachrichten. Ein leerer Feed ist
        # sicherer als generierte regionale Lageberichte.
        "news": [],
    }
