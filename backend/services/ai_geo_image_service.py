"""Das Bild einer Regionsanalyse, über das Panel geholt.

Der Browser lädt das Bild nie beim Anbieter selbst. Sonst sähe ArcGIS bzw.
der Copernicus-Katalog die IP-Adresse jedes Betrachters, und die Desktop-App
könnte es gar nicht laden: ein ``<img>`` trägt dort kein Bearer-Token.

Zwei Arten, beide ohne eine Adresse vom Aufrufer (kein SSRF):

- ``map``: das Kartenbild. ArcGIS World Imagery ist ein Mosaik aus vielen
  Aufnahmen — kein einzelner Überflug und ohne Aufnahmezeitpunkt. Es braucht
  keinen Schlüssel und steht deshalb immer zur Verfügung. Die Adresse baut
  dieses Modul aus einer geprüften Bounding-Box.
- ``scene``: die Vorschau einer Sentinel-2-Szene, die die Copernicus-Suche
  gerade gefunden hat. Die Adresse stammt aus dieser Suche
  (``ai_satellite_service.preview_href``) und muss zu Copernicus gehören —
  ebenso jedes Ziel, auf das sie weiterleitet.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict
from typing import Callable, Iterable
from urllib.parse import urlsplit

import httpx

from services import ai_satellite_service
from services.ai_latency_metrics import measure


logger = logging.getLogger(__name__)

ARCGIS_EXPORT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
#: Wem das Kartenbild gehört; Esri verlangt die Nennung neben dem Bild. Der
#: Wortlaut ist das `copyrightText` des Dienstes (`…/World_Imagery/MapServer?f=json`,
#: Stand 23.09.2026) ohne das vorangestellte „Source:" — Maxar heißt dort
#: inzwischen Vantor. Ändert Esri ihn, gehört er hier nachgezogen.
ARCGIS_ATTRIBUTION = "Esri, Vantor, Earthstar Geographics, and the GIS User Community"
#: Drei Formen, damit das Bild seine Fläche füllt, statt die Hälfte davon
#: abzuschneiden: quer im Reiter, hoch in einer schmalen Bühne, quadratisch
#: dazwischen. ArcGIS passt den Ausschnitt an das Seitenverhältnis an, statt
#: das Bild zu verzerren. Nur diese drei — jede weitere Größe wäre ein eigener
#: Eintrag im Zwischenspeicher.
MAP_SHAPES = {"landscape": "1280,720", "portrait": "720,1280", "square": "1024,1024"}

#: Web Mercator reicht nicht bis zu den Polen.
_MAX_LATITUDE = 85.0
#: Größer als ein Kontinent wird ein Kartenbild nicht: ein Land, das über die
#: Datumsgrenze reicht (Russland, USA mit Alaska), hätte sonst eine Box um
#: den halben Globus.
_MAX_SPAN_LON = 60.0
_MAX_SPAN_LAT = 40.0
#: Kleiner auch nicht — ein einzelner Punkt ergäbe kein Bild.
_MIN_SPAN = 0.01

_MAX_BYTES = 4 * 1024 * 1024
_TYPES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
#: Ein Kopf ohne Bildtyp. CREODIAS liefert Szenenvorschauen so aus, als
#: Anhang; dann entscheiden allein die ersten Bytes, was es ist.
_UNTYPED = "application/octet-stream"
#: CREODIAS leitet die Adresse aus dem Katalog (`datahub.creodias.eu`) auf den
#: Server weiter, der die Datei hat (`zipper.creodias.eu`). Eine Weiterleitung
#: genügt; die zweite ist Luft.
_MAX_REDIRECTS = 2
_CACHE_TTL_SECONDS = 900.0
_CACHE_MAX_ENTRIES = 24
_CACHE_MAX_BYTES = 32 * 1024 * 1024

#: Copernicus liefert Vorschauen vom eigenen Katalog oder über CREODIAS aus.
_COPERNICUS_HOST = re.compile(r"(?:[a-z0-9-]+\.)*dataspace\.copernicus\.eu")
_SCENE_HOSTS = (_COPERNICUS_HOST, re.compile(r"(?:[a-z0-9-]+\.)*creodias\.eu"))

_cache: OrderedDict[tuple, tuple[float, bytes, str]] = OrderedDict()
_cache_lock = threading.Lock()
_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()


class GeoImageUnavailable(RuntimeError):
    """Das Bild ist nicht zu haben. Trägt einen stabilen Fehlercode."""

    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _external_http_client() -> httpx.Client:
    global _http_client
    with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=5.0),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                follow_redirects=False,
            )
        return _http_client


def shutdown_http_client() -> None:
    global _http_client
    with _http_client_lock:
        if _http_client is not None:
            _http_client.close()
            _http_client = None


def image_bbox(bbox: Iterable[object], latitude: float, longitude: float) -> tuple[float, float, float, float]:
    """Der Ausschnitt, den das Kartenbild zeigt: die Box des Ortes, begrenzt.

    Eine Box über die Datumsgrenze (``min > max``) oder eine ungültige wird
    zu einem Ausschnitt um den Ort; eine riesige auf Kontinentgröße gekürzt,
    ein einzelner Punkt auf die kleinste Bildgröße geweitet.
    """
    try:
        min_lon, min_lat, max_lon, max_lat = (float(value) for value in bbox)  # type: ignore[arg-type]
        valid = (
            all(math.isfinite(v) for v in (min_lon, min_lat, max_lon, max_lat))
            and -180.0 <= min_lon <= max_lon <= 180.0
            and -90.0 <= min_lat <= max_lat <= 90.0
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        min_lon, max_lon = longitude - 5.0, longitude + 5.0
        min_lat, max_lat = latitude - 3.0, latitude + 3.0

    def fit(lo: float, hi: float, centre: float, most: float, low: float, high: float) -> tuple[float, float]:
        if hi - lo > most:
            lo, hi = centre - most / 2, centre + most / 2
        if hi - lo < _MIN_SPAN:
            lo, hi = centre - _MIN_SPAN / 2, centre + _MIN_SPAN / 2
        # Verschieben statt abschneiden: der Ausschnitt behält seine Größe.
        if lo < low:
            lo, hi = low, min(high, hi + (low - lo))
        if hi > high:
            lo, hi = max(low, lo - (hi - high)), high
        return lo, hi

    centre_lon = min(180.0, max(-180.0, float(longitude))) if math.isfinite(longitude) else (min_lon + max_lon) / 2
    centre_lat = min(_MAX_LATITUDE, max(-_MAX_LATITUDE, float(latitude))) if math.isfinite(latitude) else (min_lat + max_lat) / 2
    min_lon, max_lon = fit(min_lon, max_lon, centre_lon, _MAX_SPAN_LON, -180.0, 180.0)
    min_lat, max_lat = fit(min_lat, max_lat, centre_lat, _MAX_SPAN_LAT, -_MAX_LATITUDE, _MAX_LATITUDE)
    return (round(min_lon, 5), round(min_lat, 5), round(max_lon, 5), round(max_lat, 5))


def map_export_url(bbox: tuple[float, float, float, float], shape: str = "landscape") -> str:
    """Die ArcGIS-Adresse des Kartenbilds. ``imageSR=3857`` wie jede Webkarte:
    in Längen- und Breitengraden (4326) wäre das Bild nach Norden hin breit
    gezogen, Berlin um gut die Hälfte."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        f"{ARCGIS_EXPORT}?bbox={min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}"
        f"&bboxSR=4326&imageSR=3857&size={MAP_SHAPES[shape]}&format=jpg&f=image"
    )


def _scene_host_allowed(href: str) -> bool:
    parts = urlsplit(href)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and parts.port in (None, 443) and any(p.fullmatch(host) for p in _SCENE_HOSTS)


def _cached(key: tuple) -> tuple[bytes, str] | None:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if entry[0] <= now:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return entry[1], entry[2]


def _remember(key: tuple, data: bytes, media_type: str) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, data, media_type)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES or sum(len(e[1]) for e in _cache.values()) > _CACHE_MAX_BYTES:
            _cache.popitem(last=False)


def _download(
    url: str,
    headers_for: Callable[[str], dict[str, str]] | None = None,
    may_follow: Callable[[str], bool] | None = None,
) -> tuple[bytes, str]:
    """Holt ein Bild und nimmt nur, was wirklich ein Bild dieser Größe ist.

    Einer Weiterleitung folgt es nur mit ``may_follow`` und nur zu einem Ziel,
    das dort besteht. Das Ziel nennt der Anbieter, nicht das Panel — also wird
    jede Station für sich geprüft. Die Köpfe kommen je Station aus
    ``headers_for``, damit ein Token nie zu einem Host weiterwandert, dem es
    nicht gehört.
    """
    try:
        for _station in range(_MAX_REDIRECTS + 1):
            with measure("geo", "image_request"):
                with _external_http_client().stream(
                    "GET", url, headers=headers_for(url) if headers_for else {},
                ) as resp:
                    if resp.is_redirect and may_follow is not None:
                        url = str(resp.url.join(resp.headers["location"]))
                        if not may_follow(url):
                            logger.warning("Weiterleitung eines Regionsbilds auf fremden Host verworfen")
                            raise GeoImageUnavailable("AI_GEO_IMAGE_UNAVAILABLE", 502)
                        continue
                    return _read_image(resp)
    except httpx.HTTPError as exc:
        logger.info("Regionsbild nicht erreichbar error=%s", type(exc).__name__)
        raise GeoImageUnavailable("AI_GEO_IMAGE_UNAVAILABLE", 502) from exc
    logger.info("Regionsbild nach zu vielen Weiterleitungen verworfen")
    raise GeoImageUnavailable("AI_GEO_IMAGE_UNAVAILABLE", 502)


def _read_image(resp: httpx.Response) -> tuple[bytes, str]:
    if resp.status_code != 200:
        logger.info("Regionsbild nicht geladen status=%s", resp.status_code)
        raise GeoImageUnavailable("AI_GEO_IMAGE_UNAVAILABLE", 502)
    declared = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if declared not in _TYPES and declared != _UNTYPED:
        raise GeoImageUnavailable("AI_GEO_IMAGE_INVALID", 502)
    chunks: list[bytes] = []
    size = 0
    for chunk in resp.iter_bytes():
        size += len(chunk)
        if size > _MAX_BYTES:
            raise GeoImageUnavailable("AI_GEO_IMAGE_TOO_LARGE", 502)
        chunks.append(chunk)
    data = b"".join(chunks)
    # Der Kopf muss zum Inhalt passen: ArcGIS meldet Fehler auch einmal als
    # Bild-Typ mit einer Textseite darin. Nennt der Kopf keinen Bildtyp, zählt
    # nur der Inhalt — und hinaus geht der Typ, den er belegt.
    candidates = [declared] if declared in _TYPES else list(_TYPES)
    media_type = next((kind for kind in candidates if data.startswith(_TYPES[kind])), None)
    if media_type is None:
        raise GeoImageUnavailable("AI_GEO_IMAGE_INVALID", 502)
    return data, media_type


def map_image(bbox: tuple[float, float, float, float], shape: str = "landscape") -> tuple[bytes, str]:
    """Das Kartenbild (Mosaik) für einen schon geprüften Ausschnitt."""
    key = ("map", bbox, shape)
    cached = _cached(key)
    if cached:
        return cached
    data, media_type = _download(map_export_url(bbox, shape))
    _remember(key, data, media_type)
    return data, media_type


def scene_image(scene_id: str) -> tuple[bytes, str]:
    """Die Vorschau einer zuletzt gefundenen Sentinel-2-Szene."""
    key = ("scene", scene_id)
    cached = _cached(key)
    if cached:
        return cached
    href = ai_satellite_service.preview_href(scene_id)
    if not href:
        raise GeoImageUnavailable("AI_GEO_IMAGE_NOT_FOUND", 404)
    if not _scene_host_allowed(href):
        logger.warning("Szenenvorschau auf fremdem Host verworfen")
        raise GeoImageUnavailable("AI_GEO_IMAGE_NOT_FOUND", 404)
    data, media_type = _download(href, _scene_headers, _scene_host_allowed)
    _remember(key, data, media_type)
    return data, media_type


def _scene_headers(href: str) -> dict[str, str]:
    # Das Token gehört Copernicus und geht nur an Copernicus, nie an CREODIAS
    # — auch nicht über eine Weiterleitung dorthin.
    if _COPERNICUS_HOST.fullmatch((urlsplit(href).hostname or "").lower()):
        token = ai_satellite_service.access_token()
        if token:
            return {"Authorization": f"Bearer {token}"}
    return {}
