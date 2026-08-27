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
import time
from typing import Any

import httpx

from services.ai_redaction import redact_sensitive_text


logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_satellite_credentials_encrypted"
_AAD = "msm:settings:ai_satellite_credentials"

# Copernicus Data Space Ecosystem (CDSE) Endpunkte
_TOKEN_ENDPOINT = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_STAC_ENDPOINT = "https://catalogue.dataspace.copernicus.eu/stac/search"
_TIMEOUT = 15.0

# In-Memory Token Cache: { "token": str, "expires_at": float }
_token_cache: dict[str, Any] = {}


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
        _token_cache.clear()
        return
    payload = json.dumps({"client_id": cid, "client_secret": csec})
    PanelSettingsService.set(SETTINGS_KEY, AuthService.encrypt_secret(payload, aad=_AAD))
    _token_cache.clear()


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
    now = time.time()
    cached = _token_cache.get("token")
    expires_at = _token_cache.get("expires_at", 0)
    if cached and now < expires_at - 30:
        return str(cached)

    try:
        resp = httpx.post(
            _TOKEN_ENDPOINT,
            data={
                "grant_type": "client_credentials",
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_TIMEOUT,
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
    bbox: list[float],
    limit: int = 3,
    max_cloud_cover: float = 30.0,
) -> list[dict[str, Any]]:
    """Sucht nach den neuesten Sentinel-2 Aufnahmen für eine Bounding-Box [min_lon, min_lat, max_lon, max_lat]."""
    creds = get_credentials()
    if not creds:
        raise SatelliteUnavailable("AI_SATELLITE_NOT_CONFIGURED")

    token = _get_access_token(creds)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    body = {
        "collections": ["SENTINEL-2"],
        "bbox": bbox,
        "limit": limit,
        "query": {
            "cloudCover": {"lte": max_cloud_cover},
        },
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }

    try:
        resp = httpx.post(_STAC_ENDPOINT, json=body, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.info("CDSE STAC-Suche nicht erreichbar error=%s", type(exc).__name__)
        raise SatelliteUnavailable("AI_SATELLITE_UNAVAILABLE") from exc

    if resp.status_code in {401, 403}:
        _token_cache.clear()
        raise SatelliteUnavailable("AI_SATELLITE_AUTH_FAILED")
    if resp.status_code != 200:
        logger.info("CDSE STAC-Suche fehlgeschlagen status=%s", resp.status_code)
        raise SatelliteUnavailable("AI_SATELLITE_UNAVAILABLE")

    try:
        data = resp.json()
    except Exception as exc:
        raise SatelliteUnavailable("AI_SATELLITE_PROTOCOL_ERROR") from exc

    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        return []

    results: list[dict[str, Any]] = []
    for feat in features[:limit]:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        assets = feat.get("assets") or {}
        feat_id = redact_sensitive_text(str(feat.get("id") or ""))
        dt = str(props.get("datetime") or props.get("startDate") or "")
        clouds = props.get("cloudCover")
        preview_url = ""
        for k in ("thumbnail", "rendered_preview", "preview", "visual"):
            if k in assets and isinstance(assets[k], dict) and assets[k].get("href"):
                preview_url = str(assets[k]["href"])
                break

        results.append({
            "id": feat_id[:120],
            "mission": "Sentinel-2 L2A",
            "datetime": dt[:40],
            "cloud_cover_percent": round(float(clouds), 1) if isinstance(clouds, (int, float)) else None,
            "preview_url": preview_url if preview_url.startswith(("http://", "https://")) else "",
            "geometry": feat.get("geometry"),
        })

    return results
