"""Vorhersagen aus dem eigenen Wetter-Sidecar.

Das Gerät fragt ausschließlich MSM; MSM liest die Vorhersage aus dem lokalen
Sidecar (`127.0.0.1:8890`). Weder Nutzerstandort noch Nutzer-IP gehen an einen
Drittanbieter. Der Sidecar hält die globalen Modelldaten selbst vor.

Die Antwort wird kurz gecacht (Wetter ändert sich stündlich), der Status des
Sidecars fünf Sekunden, damit die Oberfläche nicht bei jedem Aufruf einen
TCP-Verbindungsversuch auslöst.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from config import settings

_CACHE_TTL_SECONDS = 600.0
_CACHE_MAX = 256
_STATUS_TTL_SECONDS = 5.0

_cache: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_status: tuple[float, bool] | None = None
_status_lock = threading.Lock()
_client: httpx.Client | None = None
_client_lock = threading.Lock()


class WetterUnavailable(RuntimeError):
    """Der Sidecar ist nicht eingerichtet oder nicht erreichbar."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def wetter_url() -> str:
    return (settings.wetter_url or "http://127.0.0.1:8890").rstrip("/")


def _http_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(connect=2.0, read=6.0, write=4.0, pool=2.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=4),
            )
        return _client


def shutdown_http_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def is_sidecar_running(timeout: float = 0.2) -> bool:
    """Schneller TCP-Test auf den Sidecar; Ergebnis fünf Sekunden gecacht."""
    global _status
    jetzt = time.monotonic()
    with _status_lock:
        if _status is not None and _status[0] > jetzt:
            return _status[1]
    teile = urlparse(wetter_url())
    host = teile.hostname or "127.0.0.1"
    port = teile.port or 8890
    try:
        with socket.create_connection((host, port), timeout=timeout):
            erreichbar = True
    except OSError:
        erreichbar = False
    with _status_lock:
        _status = (jetzt + _STATUS_TTL_SECONDS, erreichbar)
    return erreichbar


def wetter_konfig() -> dict[str, Any]:
    laeuft = is_sidecar_running()
    bereit = False
    if laeuft:
        try:
            antwort = _http_client().get(f"{wetter_url()}/health")
            if antwort.status_code == 200:
                daten = antwort.json()
                bereit = bool(daten.get("bereit")) if isinstance(daten, dict) else False
        except (httpx.HTTPError, ValueError):
            bereit = False
    return {
        "configured": bool(wetter_url()),
        "sidecar_laeuft": laeuft,
        "bereit": bereit,
        "url": wetter_url(),
    }


def wetter_orte(q: str) -> list[dict[str, Any]]:
    """Lokale Ortssuche aus dem Sidecar; die Eingabe geht nur an den eigenen Server."""
    if not is_sidecar_running():
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR")
    try:
        antwort = _http_client().get(f"{wetter_url()}/orte", params={"q": q})
    except httpx.HTTPError as exc:
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR") from exc
    if antwort.status_code != 200:
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR")
    try:
        daten = antwort.json()
    except ValueError as exc:
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR") from exc
    orte = daten.get("orte") if isinstance(daten, dict) else None
    return orte if isinstance(orte, list) else []


def wetter_fuer_punkt(lat: float, lon: float) -> dict[str, Any]:
    """Vorhersage für einen Punkt. Koordinaten bleiben zwischen MSM und Sidecar."""
    if not is_sidecar_running():
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR")

    schluessel = (round(lat, 2), round(lon, 2))
    jetzt = time.monotonic()
    with _cache_lock:
        treffer = _cache.get(schluessel)
        if treffer is not None and treffer[0] > jetzt:
            return treffer[1]

    try:
        antwort = _http_client().get(
            f"{wetter_url()}/wetter",
            params={"lat": schluessel[0], "lon": schluessel[1]},
        )
    except httpx.HTTPError as exc:
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR") from exc
    if antwort.status_code != 200:
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR")
    try:
        daten = antwort.json()
    except ValueError as exc:
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR") from exc
    if not isinstance(daten, dict) or "jetzt" not in daten:
        raise WetterUnavailable("WETTER_SIDECAR_NICHT_ERREICHBAR")

    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[schluessel] = (jetzt + _CACHE_TTL_SECONDS, daten)
    return daten
