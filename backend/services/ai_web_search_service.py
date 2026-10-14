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
import os
import re
import urllib.parse
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_web_search_api_key_encrypted"
SEARXNG_SETTINGS_KEY = "ai_searxng_url"
DEFAULT_SEARXNG_URL = "http://127.0.0.1:8888"
_AAD = "msm:settings:ai_web_search_api_key"
_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

MAX_RESULTS = 5
MAX_QUERY_CHARS = 200
MAX_TITLE_CHARS = 160
MAX_SNIPPET_CHARS = 400
MAX_URL_CHARS = 300
_TIMEOUT = 8.0
_CACHE_TTL_SECONDS = 15.0
_BILD_CACHE_MAX = 64
_BILD_MAX_BYTES = 2 * 1024 * 1024
_BILD_TYPEN = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n", "image/webp": b"RIFF", "image/gif": b"GIF8"}
#: So viel Wikitext wird höchstens gelesen — die Infobox steht am Anfang.
_WIKITEXT_MAX = 20000
_client: httpx.Client | None = None
_client_lock = threading.Lock()
_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}
_inflight: dict[tuple[str, str, int], threading.Event] = {}
_cache_lock = threading.Lock()
_sidecar_status_cache: dict[str, tuple[float, bool]] = {}
_sidecar_cache_lock = threading.Lock()
_bild_urls: dict[str, tuple[float, str]] = {}
_bild_daten_cache: dict[str, tuple[float, bytes, str]] = {}
_bild_lock = threading.Lock()


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
                timeout=httpx.Timeout(connect=2.5, read=_TIMEOUT, write=4.0, pool=2.5),
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
    with _sidecar_cache_lock:
        _sidecar_status_cache.clear()


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


def _panel_api_key() -> str | None:
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    stored = PanelSettingsService.get(SETTINGS_KEY, "")
    if not stored:
        return None
    try:
        dec = AuthService.decrypt_secret(stored, aad=_AAD)
        return dec.strip() if dec and dec.strip() else None
    except Exception as exc:
        logger.warning(
            "Websuch-Schluessel nicht lesbar error=%s", type(exc).__name__
        )
        return None


def _env_api_key() -> str | None:
    from config import settings
    return (
        (getattr(settings, "brave_search_api_key", "") or "").strip()
        or os.getenv("MSM_BRAVE_SEARCH_API_KEY", "").strip()
        or os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
        or os.getenv("BRAVE_API_KEY", "").strip()
    ) or None


def api_key() -> str | None:
    """Liest den hinterlegten Brave-Schluessel (Panel-DB zuerst, ENV-Fallback)."""
    return _panel_api_key() or _env_api_key()


def is_sidecar_running(url: str | None = None, timeout: float = 0.2) -> bool:
    """Prueft, ob der SearXNG-Sidecar erreichbar ist (mit kurzem Caching)."""
    target = url or default_searxng_url()
    try:
        parsed = urllib.parse.urlparse(target)
        hostname = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if hostname.lower() not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            return True

        cache_key = f"{hostname}:{port}"
        now = time.monotonic()
        with _sidecar_cache_lock:
            cached = _sidecar_status_cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

        import socket
        running = False
        try:
            with socket.create_connection((hostname, port), timeout=timeout):
                running = True
        except OSError:
            running = False

        with _sidecar_cache_lock:
            _sidecar_status_cache[cache_key] = (now + 5.0, running)
        return running
    except Exception:
        return False


def _panel_searxng_url() -> str | None:
    from services.panel_settings_service import PanelSettingsService

    raw = PanelSettingsService.get(SEARXNG_SETTINGS_KEY, "")
    return str(raw).strip() or None


def _env_searxng_url() -> str | None:
    from config import settings
    return (
        (getattr(settings, "searxng_url", "") or "").strip()
        or os.getenv("MSM_SEARXNG_URL", "").strip()
        or os.getenv("SEARXNG_URL", "").strip()
    ) or None


def custom_searxng_url() -> str | None:
    """Liest die explizit in der Panel-DB gespeicherte benutzerdefinierte URL."""
    return _panel_searxng_url()


def default_searxng_url() -> str:
    """Liest die Standard-URL fuer den Sidecar (Env/Config-Default oder 127.0.0.1:8888)."""
    return _env_searxng_url() or DEFAULT_SEARXNG_URL


def is_default_searxng() -> bool:
    """True, wenn kein benutzerdefinierter Override in den Panel-Einstellungen aktiv ist."""
    custom = _panel_searxng_url()
    return custom is None or custom == default_searxng_url()


def searxng_url() -> str | None:
    """Liest die effektive SearXNG URL (Panel-DB zuerst, ENV-Fallback, dann Default-Sidecar)."""
    custom = _panel_searxng_url()
    if custom:
        return custom

    from config import settings
    configured = (
        (getattr(settings, "searxng_url", "") or "").strip()
        or os.getenv("MSM_SEARXNG_URL", "").strip()
        or os.getenv("SEARXNG_URL", "").strip()
    )
    if configured and configured != DEFAULT_SEARXNG_URL:
        return configured

    if is_sidecar_running(DEFAULT_SEARXNG_URL):
        return DEFAULT_SEARXNG_URL

    if os.getenv("MSM_SEARXNG_URL", "").strip() == DEFAULT_SEARXNG_URL:
        return DEFAULT_SEARXNG_URL

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


def store_searxng_url(url: str) -> None:
    """Speichert oder entfernt die konfigurierte SearXNG-Endpunkt-URL."""
    from services.panel_settings_service import PanelSettingsService

    clean = (url or "").strip()
    PanelSettingsService.set(SEARXNG_SETTINGS_KEY, clean)


def is_configured() -> bool:
    """Ist ein Websuch-Provider oder Schluessel konfiguriert?"""
    try:
        return api_key() is not None or searxng_url() is not None
    except Exception as exc:
        logger.warning(
            "Websuch-Konfiguration nicht lesbar error=%s", type(exc).__name__
        )
        return False


def _merke_bild_url(url: str) -> None:
    if not url or not _is_safe_public_url(url):
        return
    jetzt = time.monotonic()
    with _bild_lock:
        _bild_urls[url] = (jetzt + 900.0, url)
        if len(_bild_urls) > 256:
            aelteste = sorted(_bild_urls.items(), key=lambda kv: kv[1][0])[:64]
            for k, _ in aelteste:
                _bild_urls.pop(k, None)


def hole_such_bild(url: str) -> tuple[bytes, str] | None:
    """Lädt ein bereits durch eine Suche oder ein Wissens-Panel freigegebenes Bild über den Server-Proxy."""
    clean = (url or "").strip()
    if not clean or len(clean) > MAX_URL_CHARS or not _is_safe_public_url(clean):
        return None
    jetzt = time.monotonic()
    with _bild_lock:
        erlaubt = _bild_urls.get(clean)
        if not erlaubt or erlaubt[0] <= jetzt:
            return None
        cached = _bild_daten_cache.get(clean)
        if cached and cached[0] > jetzt:
            return cached[1], cached[2]

    try:
        resp = _http_client().get(
            clean,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        if resp.status_code != 200 or len(resp.content) > _BILD_MAX_BYTES:
            return None
        data = resp.content
        media_type = None
        for mime, magic in _BILD_TYPEN.items():
            if data.startswith(magic):
                media_type = mime
                break
        if not media_type:
            return None
        with _bild_lock:
            _bild_daten_cache[clean] = (jetzt + 900.0, data, media_type)
            if len(_bild_daten_cache) > _BILD_CACHE_MAX:
                aelteste = sorted(_bild_daten_cache.items(), key=lambda kv: kv[1][0])[:16]
                for k, _ in aelteste:
                    _bild_daten_cache.pop(k, None)
        return data, media_type
    except Exception:
        return None


def _infobox_titel(box: dict, id_url: str) -> str:
    """Der Titel einer SearXNG-Infobox.

    SearXNG nennt im Feld ``infobox`` nur die Herkunft („wikipedia“), keinen
    Titel. Der Artikelname steckt stattdessen in der Adresse des Eintrags —
    ``…/wiki/Grand_Theft_Auto_VI`` wird zu „Grand Theft Auto VI“.
    """
    if id_url:
        slug = urllib.parse.unquote(id_url.rstrip("/").split("/")[-1]).replace("_", " ").strip()
        worte = slug.split()
        # Ein echter Artikelname ist kurz — höchstens acht Wörter. Längeres ist
        # ein Pfad oder ein Kodierungsrest und taugt nicht als Überschrift.
        if 1 <= len(worte) <= 8 and len(slug) >= 2:
            return slug[:MAX_TITLE_CHARS]
    urls = box.get("urls")
    if isinstance(urls, list):
        for eintrag in urls:
            if not isinstance(eintrag, dict):
                continue
            name = redact_sensitive_text(str(eintrag.get("title") or "")).strip()
            if name:
                return name[:MAX_TITLE_CHARS]
    return ""


def _parse_infobox(raw_boxes: object) -> dict | None:
    if not isinstance(raw_boxes, list) or not raw_boxes:
        return None
    for box in raw_boxes:
        if not isinstance(box, dict):
            continue
        inhalt = redact_sensitive_text(str(box.get("content") or "")).strip()[:1200]
        id_url = str(box.get("id") or "").strip()
        if id_url and (len(id_url) > MAX_URL_CHARS or not _is_safe_public_url(id_url)):
            id_url = ""
        if not inhalt and not id_url:
            continue
        titel = _infobox_titel(box, id_url)
        bild = str(box.get("img_src") or "").strip()
        sicheres_bild = bild if (bild and len(bild) <= MAX_URL_CHARS and _is_safe_public_url(bild)) else None
        if sicheres_bild:
            _merke_bild_url(sicheres_bild)
        attribute = []
        raw_attrs = box.get("attributes")
        if isinstance(raw_attrs, list):
            for attr in raw_attrs[:10]:
                if not isinstance(attr, dict):
                    continue
                lbl = redact_sensitive_text(str(attr.get("label") or "")).strip()[:80]
                val = redact_sensitive_text(str(attr.get("value") or "")).strip()[:200]
                if lbl and val:
                    attribute.append({"label": lbl, "value": val})
        urls = []
        raw_urls = box.get("urls")
        if isinstance(raw_urls, list):
            for u_obj in raw_urls[:6]:
                if not isinstance(u_obj, dict):
                    continue
                u_str = str(u_obj.get("url") or "").strip()
                u_lbl = redact_sensitive_text(str(u_obj.get("title") or "")).strip()[:80]
                if u_str and len(u_str) <= MAX_URL_CHARS and _is_safe_public_url(u_str):
                    urls.append({"title": u_lbl or u_str, "url": u_str})
        # Die Wikipedia-Adresse selbst steht immer zuerst; ihr Name kommt vom
        # Host, nicht aus einer Liste, die der Betreiber nicht pflegen müsste.
        if id_url:
            host = (urllib.parse.urlsplit(id_url).hostname or "").replace("www.", "")
            name = "Wikipedia" if "wikipedia.org" in host else host
            if not any(u["url"] == id_url for u in urls):
                urls.insert(0, {"title": name, "url": id_url})
            else:
                urls[0] = {"title": name, "url": id_url}
        return {
            "titel": titel,
            "inhalt": inhalt,
            **({"bild": sicheres_bild} if sicheres_bild else {}),
            "attribute": attribute,
            "urls": urls,
        }
    return None


def _wiki_markup_sauber(wert: str) -> str:
    """Wikitext-Auszeichnung zu lesbarem Text; was bleibt, ist geprüfter Text."""
    text = re.sub(r"<ref[^>/]*/>", "", wert)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # [[Ziel|Anzeige]] -> Anzeige, [[Ziel]] -> Ziel
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    # [https://ziel.example Anzeige] -> Anzeige, [https://ziel.example] -> Adresse
    text = re.sub(r"\[(?:https?)://\S+\s+([^\]]*)\]", r"\1", text)
    text = re.sub(r"\[((?:https?)://\S+)\]", r"\1", text)
    text = re.sub(r"'''|''", "", text)
    text = text.replace("&nbsp;", " ").replace("<br>", " ").replace("<br />", " ")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip(" ,;")


def _wiki_wert_ende(block: str, start: int) -> int:
    """Bis zum nächsten Feldzeichen auf oberster Klammer-Ebene.

    Ein schließendes `}}` auf Ebene null endet die ganze Vorlage — dort ist der
    Wert zu Ende, auch wenn danach noch Zeilen folgen.
    """
    tiefe = 0
    i = start
    while i < len(block):
        if block.startswith("{{", i) or block.startswith("[[", i):
            tiefe += 1
            i += 2
            continue
        if block.startswith("}}", i) or block.startswith("]]", i):
            if tiefe == 0:
                return i
            tiefe -= 1
            i += 2
            continue
        if block[i] == "|" and tiefe == 0:
            return i
        i += 1
    return len(block)


def _wiki_infobox_felder(wikitext: str) -> list[dict]:
    """Eckdaten aus der Infobox-Vorlage einer Wikipedia-Seite.

    Absichtlich konservativ: nur Felder, deren Wert ohne verschachtelte Vorlage
    lesbar ist, kommen durch. Ein `{{Geburtsdatum|1971|6|28}}` ist drei
    Zahlen ohne Datumsformat — das zeigt das Panel nicht, statt es zu einem
    Datum zu erfinden. Höchstens zehn Felder, damit die Kachel nicht wächst.
    """
    text = wikitext or ""
    anfang = text.lower().find("{{infobox")
    if anfang < 0:
        return []
    tiefe = 0
    i = anfang
    ende = len(text)
    while i < len(text):
        if text.startswith("{{", i):
            tiefe += 1
            i += 2
            continue
        if text.startswith("}}", i):
            tiefe -= 1
            i += 2
            if tiefe == 0:
                ende = i
                break
            continue
        i += 1
    block = text[anfang:ende]
    felder: list[dict] = []
    for treffer in re.finditer(r"\n\s*\|\s*([^=|{}\[\]]{2,60}?)\s*=\s*", block):
        name = _wiki_markup_sauber(treffer.group(1))
        if not name or len(felder) >= 10:
            continue
        wert_roh = block[treffer.end() : _wiki_wert_ende(block, treffer.end())]
        if "{{" in wert_roh:
            continue
        wert = _wiki_markup_sauber(wert_roh)
        if not wert or len(wert) > 200:
            continue
        felder.append({"label": name[:80], "value": wert})
    return felder


def _wiki_wikitext(lang: str, page_title: str) -> str:
    """Der Wikitext der Seite, höchstens eine Anfrage und nur für die Infobox."""
    try:
        resp = _http_client().get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "revisions",
                "titles": page_title,
                "rvslots": "main",
                "rvprop": "content",
                "format": "json",
            },
            headers={
                "User-Agent": "MauntingSearch/1.0 (https://mauntingstudios.com; privacy-proxy)",
                "Accept": "application/json",
            },
        )
        if resp.status_code != 200:
            return ""
        daten = resp.json()
        pages = daten.get("query", {}).get("pages", {}) if isinstance(daten, dict) else {}
        if not isinstance(pages, dict):
            return ""
        for seite in pages.values():
            if not isinstance(seite, dict):
                continue
            revisionen = seite.get("revisions")
            if isinstance(revisionen, list) and revisionen and isinstance(revisionen[0], dict):
                slots = revisionen[0].get("slots")
                if isinstance(slots, dict) and isinstance(slots.get("main"), dict):
                    text = str(slots["main"].get("*") or "")
                    # Nur der Kopf der Seite: die Infobox steht oben, und mehr
                    # Wikitext brächte nichts als Ladezeit.
                    return text[:_WIKITEXT_MAX]
        return ""
    except Exception:
        return ""


def wikipedia_wissen(query: str, sprache: str = "de") -> dict | None:
    """Holt eine kompakte Wikipedia-Zusammenfassung über das Backend, falls keine SearXNG-Infobox vorlag."""
    q = (query or "").strip()
    if not q or len(q) > 120:
        return None
    lang = sprache if sprache in ("de", "en", "fr", "es", "it", "nl", "pl", "pt") else "de"
    try:
        # 1. Suche den besten Wikipedia-Artikeltitel
        search_resp = _http_client().get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": q,
                "srlimit": 1,
                "format": "json",
            },
            headers={
                "User-Agent": "MauntingSearch/1.0 (https://mauntingstudios.com; privacy-proxy)",
                "Accept": "application/json",
            },
        )
        if search_resp.status_code != 200:
            return None
        s_data = search_resp.json()
        search_hits = s_data.get("query", {}).get("search", []) if isinstance(s_data, dict) else []
        if not search_hits or not isinstance(search_hits[0], dict):
            return None
        page_title = str(search_hits[0].get("title") or "").strip()
        if not page_title:
            return None

        # 2. Lade REST-Summary zu diesem Titel
        encoded_title = urllib.parse.quote(page_title.replace(" ", "_"), safe="")
        sum_resp = _http_client().get(
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded_title}",
            headers={
                "User-Agent": "MauntingSearch/1.0 (https://mauntingstudios.com; privacy-proxy)",
                "Accept": "application/json",
            },
        )
        if sum_resp.status_code != 200:
            return None
        w = sum_resp.json()
        if not isinstance(w, dict):
            return None
        titel = redact_sensitive_text(str(w.get("title") or page_title)).strip()[:MAX_TITLE_CHARS]
        untertitel = redact_sensitive_text(str(w.get("description") or "")).strip()[:200]
        inhalt = redact_sensitive_text(str(w.get("extract") or "")).strip()[:1200]
        if not inhalt:
            return None
        page_url = (
            str(w.get("content_urls", {}).get("desktop", {}).get("page") or "")
            if isinstance(w.get("content_urls"), dict)
            else ""
        )
        if not page_url or not _is_safe_public_url(page_url):
            page_url = f"https://{lang}.wikipedia.org/wiki/{encoded_title}"
        thumb = w.get("thumbnail")
        bild = str(thumb.get("source") or "").strip() if isinstance(thumb, dict) else ""
        sicheres_bild = bild if (bild and len(bild) <= MAX_URL_CHARS and _is_safe_public_url(bild)) else None
        if sicheres_bild:
            _merke_bild_url(sicheres_bild)
        wikitext = _wiki_wikitext(lang, page_title)
        attribute = []
        if untertitel:
            label = "Beschreibung" if lang == "de" else "Description"
            attribute.append({"label": label, "value": untertitel})
        attribute.extend(_wiki_infobox_felder(wikitext))
        return {
            "titel": titel,
            "untertitel": untertitel,
            "inhalt": inhalt,
            **({"bild": sicheres_bild} if sicheres_bild else {}),
            "attribute": attribute,
            "urls": [{"title": "Wikipedia", "url": page_url}],
        }
    except Exception as exc:
        logger.debug("Wikipedia-Wissensabruf fehlgeschlagen: %s", type(exc).__name__)
        return None


def searxng_autocomplete(query: str, sprache: str = "de", s_url: str | None = None) -> list[str]:
    """Holt Live-Suchvorschläge über SearXNG (/autocompleter) ohne Direktkontakt des Browsers zu Dritten."""
    q = (query or "").strip()[:MAX_QUERY_CHARS]
    if not q:
        return []
    target_url = s_url or searxng_url()
    if not target_url:
        return []
    try:
        resp = _http_client().get(
            f"{target_url.rstrip('/')}/autocompleter",
            params={"q": q, "language": sprache},
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                # SearXNG returns either ["s1", "s2"] or ["query", ["s1", "s2"]]
                items = data[1] if (len(data) >= 2 and isinstance(data[1], list)) else data
                return [
                    redact_sensitive_text(str(s).strip())[:MAX_QUERY_CHARS]
                    for s in items
                    if isinstance(s, str) and s.strip()
                ][:8]
    except Exception:
        pass
    return []


def searxng_ergebnis(
    query: str,
    *,
    limit: int = MAX_RESULTS,
    seite: int = 1,
    sicher: bool = False,
    sprache: str | None = None,
    kategorie: str | None = None,
    zeitraum: str | None = None,
    s_url: str | None = None,
) -> dict:
    """Treffer und Suchvorschlaege der SearXNG-Instanz.

    Nur oeffentliche http(s)-Adressen; eine Adresse ueber ``MAX_URL_CHARS``
    faellt weg, statt gekuerzt auf eine andere Seite zu zeigen. ``sicher``
    verlangt die strenge sichere Suche, ``kategorie`` und ``zeitraum`` gehen
    als ``categories`` und ``time_range`` an SearXNG. Nennt SearXNG ein
    Datum, steht es als ``published`` (ISO, UTC) dabei. Wirft
    ``WebSearchUnavailable``, wenn keine Instanz eingerichtet ist oder sie
    nicht antwortet.
    """
    target_url = s_url or searxng_url()
    if not target_url:
        raise WebSearchUnavailable("AI_WEB_SEARCH_NOT_CONFIGURED")
    params: dict[str, str | int] = {"q": query, "format": "json", "pageno": seite}
    if sicher:
        params["safesearch"] = 2
    if sprache:
        params["language"] = sprache
    if kategorie:
        params["categories"] = kategorie
    if zeitraum:
        params["time_range"] = zeitraum
    try:
        resp = _http_client().get(
            f"{target_url.rstrip('/')}/search",
            params=params,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise WebSearchUnavailable("AI_WEB_SEARCH_REJECTED")
        data = resp.json()
    except WebSearchUnavailable:
        raise
    except Exception as exc:
        logger.debug("SearXNG nicht erreichbar error=%s", type(exc).__name__)
        raise WebSearchUnavailable("AI_WEB_SEARCH_UNAVAILABLE") from exc
    raw = data.get("results") if isinstance(data, dict) else None
    hits = []
    for r in raw if isinstance(raw, list) else []:
        if len(hits) >= limit:
            break
        if not isinstance(r, dict):
            continue
        u = str(r.get("url") or "")
        if len(u) <= MAX_URL_CHARS and _is_safe_public_url(u):
            hit = {
                "title": redact_sensitive_text(str(r.get("title") or ""))[:MAX_TITLE_CHARS],
                "url": u,
                "snippet": redact_sensitive_text(str(r.get("content") or ""))[:MAX_SNIPPET_CHARS],
            }
            img = str(r.get("thumbnail") or r.get("img_src") or "")
            if img and len(img) <= MAX_URL_CHARS and _is_safe_public_url(img):
                hit["bild"] = img
                _merke_bild_url(img)
            published = _zeitpunkt(r.get("publishedDate"))
            if published:
                hit["published"] = published
            hits.append(hit)

    raw_sug = data.get("suggestions") if isinstance(data, dict) else []
    suggestions = [
        str(s).strip()
        for s in raw_sug
        if isinstance(s, str) and s.strip() and len(s) <= MAX_QUERY_CHARS
    ][:8] if isinstance(raw_sug, list) else []

    infobox = _parse_infobox(data.get("infoboxes") if isinstance(data, dict) else None)
    ergebnis_dict: dict = {"treffer": hits, "vorschlaege": suggestions}
    if infobox:
        ergebnis_dict["infobox"] = infobox
    return ergebnis_dict


def searxng_treffer(
    query: str,
    *,
    limit: int = MAX_RESULTS,
    seite: int = 1,
    sicher: bool = False,
    sprache: str | None = None,
    kategorie: str | None = None,
    zeitraum: str | None = None,
    s_url: str | None = None,
) -> list[dict]:
    """Treffer der SearXNG-Instanz, fuer die KI und den Browser (Suche, Nachrichten)."""
    return searxng_ergebnis(
        query,
        limit=limit,
        seite=seite,
        sicher=sicher,
        sprache=sprache,
        kategorie=kategorie,
        zeitraum=zeitraum,
        s_url=s_url,
    )["treffer"]


def _zeitpunkt(wert: object) -> str | None:
    """Ein Datum aus SearXNG als ISO in UTC; was sich nicht lesen laesst, faellt weg."""
    if not isinstance(wert, str) or not wert or len(wert) > 40:
        return None
    try:
        zeit = datetime.fromisoformat(wert.replace("Z", "+00:00"))
    except ValueError:
        return None
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=timezone.utc)
    return zeit.astimezone(timezone.utc).isoformat()


def _search_searxng(query: str, limit: int = MAX_RESULTS, s_url: str | None = None) -> list[dict]:
    try:
        return searxng_treffer(query, limit=limit, s_url=s_url)
    except WebSearchUnavailable:
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
    if s_url:
        results = _search_searxng(safe_query, limit, s_url=s_url)

    # 2. Brave Search API Abfrage, falls Key hinterlegt und noch keine Treffer
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

    # 3. DuckDuckGo Fallback, falls konfiguriert aber kein Ergebnis
    if not results and not key and s_url:
        results = _search_duckduckgo(safe_query, limit)

    if cache_key and pending:
        with _cache_lock:
            _cache[cache_key] = (time.monotonic() + _CACHE_TTL_SECONDS, results)
            _inflight.pop(cache_key, None)
            pending.set()
    return results

