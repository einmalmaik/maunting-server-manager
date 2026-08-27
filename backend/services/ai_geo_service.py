"""Geodaten-, Geocoding- und Regionsanalyse-Dienst für die KI.

Löst Ortsnamen in Koordinaten (WGS84) und Bounding-Boxes auf,
ruft Wetter- und Umweltparameter ab und verknüpft sie mit den
Satellitendaten aus Copernicus/Sentinel.
"""

from __future__ import annotations

import logging
from typing import Any
import httpx

from services.ai_redaction import redact_sensitive_text
from services import ai_satellite_service


logger = logging.getLogger(__name__)

_NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
_OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 10.0

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
}

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
    if clean_key in _geo_cache:
        return _geo_cache[clean_key]

    try:
        resp = httpx.get(
            _NOMINATIM_ENDPOINT,
            params={
                "q": query[:100],
                "format": "jsonv2",
                "limit": 1,
                "addressdetails": 1,
            },
            headers={"User-Agent": "MSM-Server-Manager/3.0 (RegionalAnalysis)"},
            timeout=_TIMEOUT,
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
                _geo_cache[clean_key] = result
                return result
    except Exception as exc:
        logger.info("Geocoding fehlgeschlagen query=%s error=%s", query, type(exc).__name__)

    return None


def get_current_weather(latitude: float, longitude: float) -> dict[str, Any] | None:
    """Ruft aktuelle Wetterdaten für die Koordinaten ab (Open-Meteo)."""
    try:
        resp = httpx.get(
            _OPEN_METEO_ENDPOINT,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            },
            timeout=_TIMEOUT,
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

    weather = get_current_weather(lat, lon)

    satellite_data: list[dict[str, Any]] = []
    satellite_configured = ai_satellite_service.is_configured()
    if satellite_configured:
        try:
            satellite_data = ai_satellite_service.search_satellite_imagery(bbox=bbox, limit=2)
        except Exception as exc:
            logger.info("Satellitenbildsuche nicht erfolgreich error=%s", type(exc).__name__)

    return {
        "status": "success",
        "location": geo["name"],
        "country": geo["country"],
        "coordinates": {
            "latitude": lat,
            "longitude": lon,
            "bbox": bbox,
        },
        "weather": weather,
        "satellite": {
            "available": len(satellite_data) > 0,
            "scenes": satellite_data,
        },
    }
