"""Zentraler Suchmaschinen-Dienst fuer Maunting Search und Singra (`ai_web_search_service.py`).

Eine gemeinsame Suchschicht fuer den Secure Browser (`POST /api/browser/suche`)
und das Werkzeug `web_search` der KI:

1. **Lokaler SearXNG-Sidecar oder eigener SearXNG-Endpunkt** als private
   Metasuche (Web, Bilder, Videos, News, Autocomplete, Infoboxen und
   Direktantworten `answers`).
2. **Optionale offizielle Such-APIs** (Brave Search API, Google Custom Search
   JSON API), verschlüsselt im Panel hinterlegt (`AuthService.encrypt_secret`
   mit gebundener AAD). Ist ein Schlüssel hinterlegt und die API-Nutzung nicht
   bei externer Instanz abgeschaltet, bereichert er **sowohl Maunting Search
   im Browser als auch die KI-Websuche**.
3. **Schalter fuer die KI (`ai_web_search_enabled`)**: Unter den KI-Einstellungen
   wird kein eigener Schlüssel und keine zweite SearXNG-Adresse mehr gepflegt,
   sondern nur noch festgelegt, ob Singra auf Maunting Search zugreifen darf
   (zusätzlich zum Rollenrecht `ai.web_search.use`).
4. **Intelligentes Wikipedia-Wissens-Panel**: Natürlichsprachliche Fragen
   („Was ist Eisen?“, „Wie koche ich Gulasch?“) werden vor der Wikipedia-Abfrage
   auf ihren Kernbegriff reduziert, damit rechts verlässlich der passende
   Wissensgraph samt aufgelösten Infobox-Eckdaten erscheint.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import html
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.parse

import httpx

from services.ai_latency_metrics import measure
from services.ai_redaction import redact_sensitive_text

logger = logging.getLogger(__name__)

SETTINGS_KEY = "ai_web_search_api_key_encrypted"
SEARXNG_SETTINGS_KEY = "ai_searxng_url"
AI_WEB_SEARCH_ENABLED_KEY = "ai_web_search_enabled"
EXTERNAL_APIS_ON_CUSTOM_KEY = "search_external_apis_on_custom"
GOOGLE_CSE_SETTINGS_KEY = "search_google_cse_credentials_encrypted"

DEFAULT_SEARXNG_URL = "http://127.0.0.1:8888"
_AAD = "msm:settings:ai_web_search_api_key"
_GOOGLE_CSE_AAD = "msm:settings:search_google_cse_credentials"

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_BRAVE_IMAGES_ENDPOINT = "https://api.search.brave.com/res/v1/images/search"
_BRAVE_VIDEOS_ENDPOINT = "https://api.search.brave.com/res/v1/videos/search"
_BRAVE_NEWS_ENDPOINT = "https://api.search.brave.com/res/v1/news/search"
_GOOGLE_CSE_ENDPOINT = "https://customsearch.googleapis.com/customsearch/v1"

MAX_RESULTS = 15
DEFAULT_SEARCH_COUNT = 10
MAX_BROWSER_RESULTS = 20
MAX_QUERY_CHARS = 200
MAX_TITLE_CHARS = 160
MAX_SNIPPET_CHARS = 400
MAX_URL_CHARS = 300
_TIMEOUT = 8.0
_CACHE_TTL_SECONDS = 15.0
_BILD_CACHE_MAX = 64
_BILD_MAX_BYTES = 2 * 1024 * 1024
_BILD_TYPEN = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",
    "image/gif": b"GIF8",
}
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
        host_lower = hostname.lower()
        if host_lower in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False
        if host_lower.endswith((".local", ".localhost", ".internal", ".lan", ".home")):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
                return False
            return True
        except ValueError:
            pass

        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for _, _, _, _, sockaddr in addr_info:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
                    return False
        except socket.gaierror:
            pass
        return True
    except Exception:
        return False


def _clean_table_cell(cell_html: str) -> str:
    """Bereinigt den Inhalt einer Tabellenzelle fuer Markdown."""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", cell_html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("|", r"\|")
    return s


def _parse_single_table_to_markdown(table_html: str, max_rows: int = 50) -> str:
    """Wandelt ein einzelnes <table>...</table> HTML-Fragment in eine GitHub-Flavored Markdown-Tabelle um."""
    # Zeilen finden: sowohl geschlossene <tr>...</tr> als auch ungeschlossene <tr>...(?=<tr|$)
    raw_rows = re.findall(r"<tr[^>]*>(.*?)(?:</tr>|(?=<tr[^>]*>)|$)", table_html, re.DOTALL | re.IGNORECASE)
    raw_rows = [r for r in raw_rows if r.strip()]
    if not raw_rows:
        return ""

    table_data: list[list[tuple[str, str]]] = []
    for r in raw_rows:
        cells = re.findall(r"<(th|td)[^>]*>(.*?)(?:</\1>|(?=<(?:th|td)[^>]*>)|$)", r, re.DOTALL | re.IGNORECASE)
        row_cells = [(tag.lower(), _clean_table_cell(c)) for tag, c in cells]
        if any(txt for _, txt in row_cells):
            table_data.append(row_cells)

    if not table_data:
        return ""

    first_row_is_th = any(tag == "th" for tag, _ in table_data[0])
    has_header = first_row_is_th or (len(table_data) > 1 and not all(tag == "td" for tag, _ in table_data[0]))

    if has_header or len(table_data) >= 2:
        header_cells = [txt or f"Spalte {i+1}" for i, (_, txt) in enumerate(table_data[0])]
        body_rows = [[txt for _, txt in row] for row in table_data[1:]]
    else:
        header_cells = [f"Spalte {i+1}" for i in range(len(table_data[0]))]
        body_rows = [[txt for _, txt in row] for row in table_data]

    num_cols = max(len(header_cells), max((len(r) for r in body_rows), default=0))
    if num_cols == 0:
        return ""

    while len(header_cells) < num_cols:
        header_cells.append(f"Spalte {len(header_cells)+1}")

    md_lines: list[str] = [
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join(["---"] * num_cols) + " |",
    ]

    gekuerzt = 0
    if len(body_rows) > max_rows:
        gekuerzt = len(body_rows) - max_rows
        body_rows = body_rows[:max_rows]

    for row in body_rows:
        while len(row) < num_cols:
            row.append("")
        if len(row) > num_cols:
            row = row[:num_cols]
        md_lines.append("| " + " | ".join(row) + " |")

    if gekuerzt > 0:
        placeholder_row = ["..."] * num_cols
        placeholder_row[0] = f"... ({gekuerzt} weitere Zeilen gekürzt)"
        md_lines.append("| " + " | ".join(placeholder_row) + " |")

    return "\n\n" + "\n".join(md_lines) + "\n\n"


def _convert_tables_to_markdown(html_content: str) -> str:
    """Ersetzt alle HTML-Tabellen (auch verschachtelte) durch saubere Markdown-Tabellen."""
    if "<table" not in html_content.lower():
        return html_content

    pattern = re.compile(r"<table[^>]*>((?:(?!<table).)*?)</table>", re.DOTALL | re.IGNORECASE)
    res = html_content
    for _ in range(25):
        match = pattern.search(res)
        if not match:
            break
        full_table = match.group(0)
        md_table = _parse_single_table_to_markdown(full_table)
        res = res[:match.start()] + md_table + res[match.end():]

    return res


def _convert_definition_lists(html_content: str) -> str:
    """Konvertiert <dl><dt>Schluessel</dt><dd>Wert</dd></dl> in Markdown-Listen."""
    if "<dl" not in html_content.lower():
        return html_content

    def _replace_dl(match: re.Match) -> str:
        dl_body = match.group(1)
        items: list[tuple[str, str]] = []
        current_dt = ""
        for tag, val in re.findall(
            r"<(dt|dd)[^>]*>(.*?)(?:</\1>|(?=<(?:dt|dd)[^>]*>|$))",
            dl_body,
            re.DOTALL | re.IGNORECASE,
        ):
            clean_val = re.sub(r"<[^>]+>", " ", val)
            clean_val = html.unescape(clean_val)
            clean_val = re.sub(r"\s+", " ", clean_val).strip()
            if tag.lower() == "dt":
                current_dt = clean_val
            elif tag.lower() == "dd":
                if current_dt:
                    items.append((current_dt, clean_val))
                    current_dt = ""
                elif clean_val:
                    items.append(("Eigenschaft", clean_val))
        if not items:
            return ""
        lines = [f"- **{k}**: {v}" for k, v in items if k and v]
        return "\n\n" + "\n".join(lines) + "\n\n"

    return re.sub(r"<dl[^>]*>(.*?)(?:</dl>|(?=<dl[^>]*>)|$)", _replace_dl, html_content, flags=re.DOTALL | re.IGNORECASE)


def _extract_json_ld_and_meta(html_content: str) -> str:
    """Liest strukturierte Daten (<script type="application/ld+json">) vor der Bereinigung aus."""
    if "application/ld+json" not in html_content:
        return ""

    script_blocks = re.findall(
        r"""<script[^>]*type=["']application/ld\+json["'][^>]*>(.*?)</script>""",
        html_content,
        re.DOTALL | re.IGNORECASE,
    )
    if not script_blocks:
        return ""

    structured_items: list[str] = []

    def _parse_entity(entity: dict[str, Any]) -> None:
        if not isinstance(entity, dict):
            return
        etype = entity.get("@type")
        if isinstance(etype, list):
            etype = "/".join(str(x) for x in etype)
        name = entity.get("name") or entity.get("headline") or entity.get("title")
        desc = entity.get("description")
        lines: list[str] = []
        if name:
            label = f"{etype}: " if etype else ""
            lines.append(f"**{label}{html.unescape(str(name)).strip()}**")
        if desc and len(str(desc).strip()) > 10:
            clean_desc = re.sub(r"\s+", " ", html.unescape(str(desc))).strip()
            lines.append(f"- Beschreibung: {clean_desc[:300]}")

        rating = entity.get("aggregateRating")
        if isinstance(rating, dict):
            val = rating.get("ratingValue")
            best = rating.get("bestRating", 5)
            count = rating.get("reviewCount") or rating.get("ratingCount")
            if val is not None:
                lines.append(f"- Wertung: {val}/{best}" + (f" ({count} Bewertungen)" if count else ""))

        offers = entity.get("offers")
        if isinstance(offers, dict):
            price = offers.get("price")
            curr = offers.get("priceCurrency", "")
            if price:
                lines.append(f"- Preis: {price} {curr}".strip())
        elif isinstance(offers, list) and offers and isinstance(offers[0], dict):
            price = offers[0].get("price")
            curr = offers[0].get("priceCurrency", "")
            if price:
                lines.append(f"- Preis: {price} {curr}".strip())

        props = entity.get("additionalProperty")
        if isinstance(props, list):
            for p in props[:15]:
                if isinstance(p, dict) and p.get("name") and p.get("value"):
                    pname = html.unescape(str(p["name"])).strip()
                    pval = html.unescape(str(p["value"])).strip()
                    lines.append(f"- **{pname}**: {pval}")

        if lines:
            structured_items.append("\n".join(lines))

    for block in script_blocks:
        block_clean = block.strip()
        if not block_clean:
            continue
        try:
            data = json.loads(block_clean)
        except Exception:
            continue
        if isinstance(data, list):
            for item in data[:5]:
                _parse_entity(item)
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                for item in data["@graph"][:5]:
                    _parse_entity(item)
            else:
                _parse_entity(data)

    if not structured_items:
        return ""

    return "### Strukturdaten & Spezifikationen:\n" + "\n\n".join(structured_items[:6]) + "\n\n"


def _parse_xml_content(raw_text: str, max_chars: int = 8000) -> dict[str, str]:
    """Wandelt XML-Inhalte (RSS, Atom, Feeds, XML-Schnittstellen) in lesbaren Markdown-Text um."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw_text)
    except Exception:
        cleaned = re.sub(r"<[^>]+>", " ", raw_text)
        cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
        return {
            "title": "XML-Dokument",
            "content": cleaned[:max_chars],
        }

    tag_lower = root.tag.lower()
    title = ""
    lines: list[str] = []

    if "rss" in tag_lower:
        channel = root.find("channel")
        if channel is not None:
            c_title = channel.findtext("title") or ""
            title = c_title.strip()
            c_desc = channel.findtext("description") or ""
            if c_desc:
                lines.append(f"Feed-Beschreibung: {c_desc.strip()}\n")
            for item in channel.findall("item")[:20]:
                i_title = item.findtext("title") or ""
                i_link = item.findtext("link") or ""
                i_desc = item.findtext("description") or ""
                clean_desc = re.sub(r"<[^>]+>", " ", i_desc)
                clean_desc = re.sub(r"\s+", " ", html.unescape(clean_desc)).strip()
                lines.append(f"### {i_title.strip()}\nLink: {i_link.strip()}\n{clean_desc[:500]}\n")
    elif "feed" in tag_lower:
        title = (root.findtext("{http://www.w3.org/2005/Atom}title") or root.findtext("title") or "").strip()
        entries = root.findall("{http://www.w3.org/2005/Atom}entry") or root.findall("entry")
        for entry in entries[:20]:
            e_title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or entry.findtext("title") or "").strip()
            summary = (entry.findtext("{http://www.w3.org/2005/Atom}summary") or entry.findtext("summary") or entry.findtext("content") or "").strip()
            clean_sum = re.sub(r"<[^>]+>", " ", summary)
            clean_sum = re.sub(r"\s+", " ", html.unescape(clean_sum)).strip()
            lines.append(f"### {e_title}\n{clean_sum[:500]}\n")
    else:
        title = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        for elem in root.iter():
            text = (elem.text or "").strip()
            if text and len(text) > 1 and elem != root:
                tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                lines.append(f"- **{tag_name}**: {text}")

    content = "\n".join(lines).strip()
    return {
        "title": title or "XML-Dokument",
        "content": content[:max_chars],
    }


def _extract_youtube_video_id(url: str) -> str | None:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if "youtu.be" in host:
            path = parsed.path.strip("/").split("/")[0]
            return path if path else None
        if "youtube.com" in host or "youtube-nocookie.com" in host:
            if parsed.path.startswith("/watch"):
                qs = urllib.parse.parse_qs(parsed.query)
                v = qs.get("v")
                return v[0] if v else None
            for prefix in ("/shorts/", "/embed/", "/v/", "/live/"):
                if parsed.path.startswith(prefix):
                    parts = parsed.path[len(prefix):].strip("/").split("/")
                    return parts[0] if parts and parts[0] else None
    except Exception:
        pass
    return None


def _format_seconds(seconds_val: str | int | float) -> str:
    try:
        sec = int(float(seconds_val))
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
    except Exception:
        return str(seconds_val)


def _extract_youtube_content(url: str, max_chars: int = 8000) -> dict:
    """Extrahiert Titel, Metadaten, Beschreibung, Kapitel und Untertitel/Transkripte eines YouTube-Videos."""
    video_id = _extract_youtube_video_id(url)
    canonical_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else url

    client = _http_client()
    title = ""
    author = ""
    duration_str = ""
    description = ""
    transcript_blocks: list[str] = []

    # 1. HTML der YouTube-Watch-Seite abrufen
    try:
        resp = client.get(
            canonical_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        raw_html = resp.text if resp.status_code == 200 else ""
    except Exception as exc:
        logger.info("YouTube Watch-Seite nicht erreichbar url=%s error=%s", url, exc)
        raw_html = ""

    player_data: dict[str, Any] | None = None
    if raw_html:
        idx = raw_html.find("ytInitialPlayerResponse")
        if idx != -1:
            eq_idx = raw_html.find("=", idx)
            if eq_idx != -1:
                brace_idx = raw_html.find("{", eq_idx)
                if brace_idx != -1:
                    try:
                        player_data, _ = json.JSONDecoder().raw_decode(raw_html[brace_idx:])
                    except Exception:
                        player_data = None

    if isinstance(player_data, dict):
        details = player_data.get("videoDetails", {})
        title = str(details.get("title") or "").strip()
        author = str(details.get("author") or "").strip()
        length_sec = details.get("lengthSeconds")
        if length_sec:
            duration_str = _format_seconds(length_sec)
        description = str(details.get("shortDescription") or "").strip()

        # Untertitel / Captions auslesen
        captions_obj = player_data.get("captions", {}).get("playerCaptionsTracklistRenderer", {})
        caption_tracks = captions_obj.get("captionTracks", [])
        chosen_track = None
        for t in caption_tracks:
            lang = t.get("languageCode", "").lower()
            if lang in ("de", "de-de"):
                chosen_track = t
                break
        if not chosen_track:
            for t in caption_tracks:
                lang = t.get("languageCode", "").lower()
                if lang in ("en", "en-us", "en-gb"):
                    chosen_track = t
                    break
        if not chosen_track and caption_tracks:
            chosen_track = caption_tracks[0]

        if chosen_track and chosen_track.get("baseUrl"):
            try:
                cap_resp = client.get(chosen_track["baseUrl"], headers={"Accept": "text/xml,application/xml"})
                if cap_resp.status_code == 200 and cap_resp.text:
                    import xml.etree.ElementTree as ET
                    parsed_captions = False
                    current_bucket_min = -1
                    bucket_text: list[str] = []

                    try:
                        root = ET.fromstring(cap_resp.text)
                        for elem in root.findall(".//text"):
                            start_str = elem.attrib.get("start", "0")
                            try:
                                start_float = float(start_str)
                            except Exception:
                                start_float = 0.0
                            minute = int(start_float // 60)
                            txt = "".join(elem.itertext())
                            clean_line = html.unescape(re.sub(r"\s+", " ", txt)).strip()
                            if not clean_line:
                                continue
                            if minute != current_bucket_min:
                                if bucket_text:
                                    transcript_blocks.append(f"[{_format_seconds(int(current_bucket_min * 60))}] {' '.join(bucket_text)}")
                                    bucket_text = []
                                current_bucket_min = minute
                            bucket_text.append(clean_line)
                        parsed_captions = True
                    except Exception:
                        parsed_captions = False

                    if not parsed_captions:
                        matches = re.finditer(
                            r"""<text\b[^>]*?\bstart=["'](?P<start>[^"']+)["'][^>]*>(?P<txt>.*?)</text>""",
                            cap_resp.text,
                            re.DOTALL | re.IGNORECASE,
                        )
                        current_bucket_min = -1
                        bucket_text = []
                        for m in matches:
                            try:
                                start_float = float(m.group("start"))
                            except Exception:
                                start_float = 0.0
                            minute = int(start_float // 60)
                            clean_line = html.unescape(re.sub(r"<[^>]+>", " ", m.group("txt")))
                            clean_line = re.sub(r"\s+", " ", clean_line).strip()
                            if not clean_line:
                                continue
                            if minute != current_bucket_min:
                                if bucket_text:
                                    transcript_blocks.append(f"[{_format_seconds(int(current_bucket_min * 60))}] {' '.join(bucket_text)}")
                                    bucket_text = []
                                current_bucket_min = minute
                            bucket_text.append(clean_line)

                    if bucket_text:
                        transcript_blocks.append(f"[{_format_seconds(int(current_bucket_min * 60))}] {' '.join(bucket_text)}")
            except Exception as cap_exc:
                logger.debug("YouTube Captions Abruf fehlgeschlagen: %s", cap_exc)

    # Fallback via oEmbed, falls Titel oder Autor noch fehlen
    if not title or not author:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(canonical_url)}&format=json"
            oembed_resp = client.get(oembed_url)
            if oembed_resp.status_code == 200:
                oe_data = oembed_resp.json()
                if not title:
                    title = oe_data.get("title", "")
                if not author:
                    author = oe_data.get("author_name", "")
        except Exception:
            pass

    if not title:
        title = f"YouTube Video ({video_id})" if video_id else "YouTube Video"

    sections: list[str] = [
        f"# [YouTube-Video & Transkript]: {title}",
        f"- **Kanal / Ersteller**: {author or 'Unbekannt'}",
    ]
    if duration_str:
        sections.append(f"- **Dauer**: {duration_str}")
    sections.append(f"- **Video-URL**: {canonical_url}\n")

    if description:
        sections.append("### Beschreibung & Kapitel:\n" + description.strip() + "\n")

    if transcript_blocks:
        sections.append("### Video-Transkript / Gesprochener Inhalt:\n" + "\n".join(transcript_blocks) + "\n")

    final_content = "\n".join(sections).strip()[:max_chars]

    return {
        "title": redact_sensitive_text(title)[:MAX_TITLE_CHARS],
        "url": canonical_url[:MAX_URL_CHARS],
        "content": redact_sensitive_text(final_content),
    }


def html_to_clean_text(html_content: str, max_len: int = 3000) -> str:
    """Extrahiert sauberen Text/Markdown aus HTML ohne Bloat, inklusive Tabellen und Listen."""
    if not html_content:
        return ""
    # 1. Definition-Lists in Markdown-Listen wandeln
    content = _convert_definition_lists(html_content)
    # 2. HTML-Tabellen in GitHub-Flavored-Markdown Tabellen wandeln
    content = _convert_tables_to_markdown(content)
    # 3. Skripte, Styles, Navigation etc. entfernen
    cleaned = re.sub(
        r"<(script|style|svg|noscript|header|footer|nav)[^>]*>.*?</\1>",
        " ",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(r"<(h[1-6]|p|div|li|tr|br)[^>]*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
    return cleaned.strip()[:max_len]


def scrape_url(url: str, max_chars: int = 3500, deep: bool = False) -> dict:
    """Liest eine URL direkt per HTTP ein und liefert bereinigten Inhalt.

    Unterstuetzt HTML-Tabellen (GitHub-Flavored-Markdown), Definitionslisten,
    JSON-LD Strukturdaten, XML/Feeds sowie YouTube-Videos samt Transkripten.
    Bei `deep=True` wird das Leselimit auf bis zu 25.000 Zeichen erweitert.
    """
    clean_url = str(url or "").strip()
    if not _is_safe_public_url(clean_url):
        return {"error": "invalid_or_private_url", "url": clean_url}

    effective_max = max(max_chars, 25000) if deep else max_chars

    # 1. YouTube-Erkennung
    if _extract_youtube_video_id(clean_url):
        return _extract_youtube_content(clean_url, max_chars=effective_max)

    try:
        current_url = clean_url
        client = _http_client()
        resp = None
        for _ in range(4):
            if not _is_safe_public_url(current_url):
                return {"error": "invalid_or_private_url", "url": current_url}
            resp = client.get(
                current_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml,text/plain",
                },
                follow_redirects=False,
            )
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    break
                current_url = urllib.parse.urljoin(current_url, location)
                continue
            break

        if resp is None:
            return {"error": "redirect_loop", "url": clean_url}
        if resp.status_code >= 400:
            return {"error": f"http_status_{resp.status_code}", "url": current_url}

        content_type = (resp.headers.get("content-type") or "").lower()
        if content_type and not any(t in content_type for t in ("text/", "html", "xml", "json")):
            return {"error": "unsupported_content_type", "url": current_url}

        raw_text = resp.text
        max_download_len = 1_000_000 if deep else 500_000
        if len(raw_text) > max_download_len:
            raw_text = raw_text[:max_download_len]

        # 2. XML / Feed Erkennung
        if "xml" in content_type or raw_text.lstrip().startswith("<?xml"):
            parsed_xml = _parse_xml_content(raw_text, max_chars=effective_max)
            return {
                "title": redact_sensitive_text(parsed_xml["title"])[:MAX_TITLE_CHARS],
                "url": current_url[:MAX_URL_CHARS],
                "content": redact_sensitive_text(parsed_xml["content"]),
            }

        # 3. HTML-Verarbeitung: JSON-LD vorab sichern, Tabellen & Definition-Lists wandeln
        json_ld_header = _extract_json_ld_and_meta(raw_text)
        title_m = re.search(r"<title[^>]*>(.*?)</title>", raw_text, re.IGNORECASE | re.DOTALL)
        title = html.unescape(title_m.group(1)).strip() if title_m else current_url

        clean_body = html_to_clean_text(raw_text, max_len=effective_max)
        full_content = (json_ld_header + clean_body).strip()[:effective_max]

        return {
            "title": redact_sensitive_text(title)[:MAX_TITLE_CHARS],
            "url": current_url[:MAX_URL_CHARS],
            "content": redact_sensitive_text(full_content),
        }
    except Exception as exc:
        logger.info("Scrape URL fehlgeschlagen url=%s error=%s", clean_url, exc)
        return {"error": str(exc), "url": clean_url}


def scrape_top_urls_parallel(
    urls: list[str],
    max_chars: int = 3500,
    limit: int = 8,
    deep: bool = False,
) -> dict[str, str]:
    """Liest mehrere Webseiten parallel im Thread-Pool ein und liefert bereinigten Text."""
    gueltige_urls: list[str] = []
    gesehen: set[str] = set()
    for u in urls:
        if len(gueltige_urls) >= limit:
            break
        u_clean = str(u or "").strip()
        if u_clean and u_clean not in gesehen and _is_safe_public_url(u_clean):
            gesehen.add(u_clean)
            gueltige_urls.append(u_clean)

    if not gueltige_urls:
        return {}

    ergebnisse: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(limit, len(gueltige_urls))) as executor:
        if deep:
            future_zu_url = {
                executor.submit(scrape_url, u, max_chars, deep=True): u
                for u in gueltige_urls
            }
        else:
            future_zu_url = {
                executor.submit(scrape_url, u, max_chars): u
                for u in gueltige_urls
            }
        for future in concurrent.futures.as_completed(future_zu_url):
            u = future_zu_url[future]
            try:
                scraped = future.result()
                if isinstance(scraped, dict) and "error" not in scraped:
                    text = (scraped.get("content") or "").strip()
                    if text:
                        ergebnisse[u] = text
            except Exception as exc:
                logger.debug("Paralleles Scraping fehlgeschlagen url=%s: %s", u, exc)
    return ergebnisse


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
        logger.warning("Websuch-Schluessel nicht lesbar error=%s", type(exc).__name__)
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


def google_cse_credentials() -> tuple[str, str] | None:
    """Liest den hinterlegten Google Custom Search API-Key samt Search Engine ID (cx)."""
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    stored = PanelSettingsService.get(GOOGLE_CSE_SETTINGS_KEY, "")
    if stored:
        try:
            dec = AuthService.decrypt_secret(stored, aad=_GOOGLE_CSE_AAD)
            if dec:
                daten = json.loads(dec)
                if isinstance(daten, dict):
                    g_key = str(daten.get("api_key") or "").strip()
                    g_cx = str(daten.get("cx") or "").strip()
                    if g_key and g_cx:
                        return g_key, g_cx
        except Exception as exc:
            logger.warning("Google-CSE-Zugangsdaten nicht lesbar error=%s", type(exc).__name__)

    env_key = (
        os.getenv("MSM_GOOGLE_CSE_API_KEY", "").strip()
        or os.getenv("GOOGLE_CSE_API_KEY", "").strip()
    )
    env_cx = (
        os.getenv("MSM_GOOGLE_CSE_CX", "").strip()
        or os.getenv("GOOGLE_CSE_CX", "").strip()
    )
    if env_key and env_cx:
        return env_key, env_cx
    return None


def has_google_cse() -> bool:
    """Ist Google Programmable Search (API-Key + CX) hinterlegt?"""
    try:
        return google_cse_credentials() is not None
    except Exception:
        return False


def google_cse_cx() -> str | None:
    """Gibt die hinterlegte Search Engine ID (cx) zurück (kein Geheimnis, hilft im Formular)."""
    creds = google_cse_credentials()
    return creds[1] if creds else None


def store_google_cse_credentials(api_key_val: str | None, cx_val: str | None) -> None:
    """Speichert oder entfernt Google CSE API-Key und CX verschlüsselt."""
    from services.auth_service import AuthService
    from services.panel_settings_service import PanelSettingsService

    k = (api_key_val or "").strip()
    c = (cx_val or "").strip()
    if not k and not c:
        PanelSettingsService.set(GOOGLE_CSE_SETTINGS_KEY, "")
        return
    # Falls nur CX geändert wird und schon ein Key existiert, alten Key behalten
    if not k and c:
        vorhanden = google_cse_credentials()
        if vorhanden:
            k = vorhanden[0]
    if not k or not c:
        PanelSettingsService.set(GOOGLE_CSE_SETTINGS_KEY, "")
        return
    payload = json.dumps({"api_key": k, "cx": c})
    PanelSettingsService.set(
        GOOGLE_CSE_SETTINGS_KEY,
        AuthService.encrypt_secret(payload, aad=_GOOGLE_CSE_AAD),
    )


def ai_web_search_enabled() -> bool:
    """Darf Singra (die KI) das Werkzeug `web_search` über Maunting Search nutzen?"""
    from services.panel_settings_service import PanelSettingsService

    raw = (PanelSettingsService.get(AI_WEB_SEARCH_ENABLED_KEY, "true") or "true").strip().lower()
    return raw not in ("false", "0", "no", "off")


def store_ai_web_search_enabled(enabled: bool) -> None:
    """Schaltet die Websuche für Singra global ein oder aus."""
    from services.panel_settings_service import PanelSettingsService

    PanelSettingsService.set(AI_WEB_SEARCH_ENABLED_KEY, "true" if enabled else "false")


def external_apis_on_custom_searxng() -> bool:
    """Sollen hinterlegte API-Schlüssel (Brave/Google) auch dann zusätzlich abgefragt
    werden, wenn eine fremde/externe SearXNG-URL eingetragen ist? Standard: False,
    damit bei fremder Instanz nicht ungewollt eigenes API-Guthaben verbraucht wird."""
    from services.panel_settings_service import PanelSettingsService

    raw = (PanelSettingsService.get(EXTERNAL_APIS_ON_CUSTOM_KEY, "false") or "false").strip().lower()
    return raw in ("true", "1", "yes", "on")


def store_external_apis_on_custom_searxng(enabled: bool) -> None:
    from services.panel_settings_service import PanelSettingsService

    PanelSettingsService.set(EXTERNAL_APIS_ON_CUSTOM_KEY, "true" if enabled else "false")


def _apis_aktiv_fuer_instanz(s_url: str | None = None) -> bool:
    """Entscheidet, ob die Panel-API-Keys (Brave/Google) bei dieser Suche aktiv sind.

    Bei lokalem Sidecar (oder wenn gar kein SearXNG läuft) sind sie immer aktiv.
    Bei einer fremden SearXNG-Adresse kann die fremde Instanz mit den Panel-Keys
    nichts anfangen — deshalb werden sie dort standardmäßig nicht verbraucht,
    außer der Betreiber schaltet `external_apis_on_custom_searxng` explizit ein.
    """
    eff = s_url or searxng_url()
    if not eff or eff == default_searxng_url():
        return True
    try:
        parsed = urllib.parse.urlparse(eff)
        if (parsed.hostname or "").lower() in ("127.0.0.1", "localhost", "::1"):
            return True
    except Exception:
        pass
    return external_apis_on_custom_searxng()


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


def is_search_engine_configured() -> bool:
    """Ist Maunting Search überhaupt einsatzbereit (SearXNG oder direkte Such-APIs)?"""
    try:
        return (
            api_key() is not None
            or searxng_url() is not None
            or has_google_cse()
        )
    except Exception as exc:
        logger.warning("Suchmaschinen-Konfiguration nicht lesbar error=%s", type(exc).__name__)
        return False


def is_configured() -> bool:
    """Ist die Websuche für Singra (KI) aktiviert und eine Suchquelle vorhanden?"""
    try:
        if not ai_web_search_enabled():
            return False
        return (
            api_key() is not None
            or searxng_url() is not None
            or has_google_cse()
        )
    except Exception as exc:
        logger.warning("Websuch-Konfiguration nicht lesbar error=%s", type(exc).__name__)
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


def _parse_answers(raw_answers: object) -> list[str]:
    """Liest Direktantworten (`answers`) aus SearXNG (Rechner, Einheiten, Hashes usw.)."""
    if not isinstance(raw_answers, list) or not raw_answers:
        return []
    antworten: list[str] = []
    for eintrag in raw_answers[:4]:
        if isinstance(eintrag, str):
            text = redact_sensitive_text(eintrag).strip()
        elif isinstance(eintrag, dict):
            text = redact_sensitive_text(str(eintrag.get("answer") or eintrag.get("content") or "")).strip()
        else:
            continue
        if text and text not in antworten:
            antworten.append(text[:600])
    return antworten


_FRAGE_PRAEFIXE_DE = re.compile(
    r"^(?:sag\s+mir\s+|erkläre\s+mir\s+|erklaere\s+mir\s+|bitte\s+)?"
    r"(?:"
    r"was\s+(?:ist|sind|bedeutet|heißt|heisst|macht|bringt|passiert\s+bei)\s+(?:eigentlich\s+|denn\s+)?(?:ein|eine|einer|einen|der|die|das)?\s*|"
    r"wer\s+(?:ist|war|sind|waren)\s+(?:eigentlich\s+|denn\s+)?|"
    r"wie\s+(?:koche|backe|mache|bereite|baue|installiere|funktioniert|funktionieren|geht|alt\s+ist|groß\s+ist|gross\s+ist|hoch\s+ist|tief\s+ist|weit\s+ist|viel\s+kostet)\s+(?:ich\s+|man\s+)?(?:eigentlich\s+)?(?:ein|eine|einen|der|die|das)?\s*|"
    r"wann\s+(?:kommt|kam|erscheint|erschien|startet|findet|wurde|ist)\s+(?:eigentlich\s+)?(?:der|die|das)?\s*|"
    r"wo\s+(?:liegt|ist|befindet\s+sich|lebt|wohnt|spielt)\s+(?:eigentlich\s+)?(?:der|die|das)?\s*|"
    r"warum\s+(?:ist|sind|hat|haben)\s+(?:der|die|das)?\s*"
    r")",
    re.IGNORECASE,
)

_FRAGE_PRAEFIXE_EN = re.compile(
    r"^(?:tell\s+me\s+|explain\s+|please\s+)?"
    r"(?:"
    r"what\s+(?:is|are|does|means?)\s+(?:a|an|the)?\s*|"
    r"who\s+(?:is|was|are|were)\s+(?:the)?\s*|"
    r"how\s+(?:to\s+(?:cook|make|bake|build|install|use)|does|do|old\s+is|tall\s+is|big\s+is)\s+(?:a|an|the)?\s*|"
    r"when\s+(?:does|did|will|is)\s+(?:the)?\s*|"
    r"where\s+(?:is|are|does)\s+(?:the)?\s*"
    r")",
    re.IGNORECASE,
)

_FRAGE_SUFFIXE = re.compile(
    r"\s+(?:"
    r"und\s+(?:wofür|wofuer|warum|wie|wann|was|welche|welcher|welches)\b.*|"
    r"oder\s+wie\b.*|"
    r"(?:heraus|raus|auf\s+den\s+markt|in\s+deutschland|für\s+pc|fuer\s+pc|zubereiten|kochen|backen|machen|erklärt|erklaert|einfach\s+erklärt)\s*|"
    r"(?:come\s+out|release\s+date|explained|recipe)\s*"
    r")$",
    re.IGNORECASE,
)


def extrahiere_wissens_begriff(query: str) -> str:
    """Extrahiert aus natürlichsprachlichen Suchfragen den Kernbegriff für Wikipedia.

    Beispiele:
    - „Was ist Eisen und wofür braucht man das?“ -> „Eisen“
    - „Wie koche ich Gulasch?“ -> „Gulasch“
    - „Wann kommt GTA 6 raus?“ -> „GTA 6“
    - „Wer ist Elon Musk?“ -> „Elon Musk“
    - „spacex“ -> „spacex“
    """
    roh = (query or "").strip()
    if not roh:
        return ""
    # Satzzeichen am Rand abstreifen
    text = re.sub(r"[?!.;,]+$", "", roh).strip()
    # Falls mehrere Teilfragen in einer Zeile stecken („Was ist Eisen? Wie koche ich Gulasch?“),
    # nimmt das Wissens-Panel die erste klare Hauptentität.
    if "?" in text:
        erster_teil = text.split("?", 1)[0].strip()
        if len(erster_teil) >= 2:
            text = erster_teil

    kandidat = _FRAGE_PRAEFIXE_DE.sub("", text).strip()
    if kandidat == text:
        kandidat = _FRAGE_PRAEFIXE_EN.sub("", text).strip()

    kandidat = _FRAGE_SUFFIXE.sub("", kandidat).strip()
    # Artikel am Anfang noch einmal abstreifen, falls nach dem Kürzen übrig
    kandidat = re.sub(r"^(?:ein|eine|einen|der|die|das|dem|den|a|an|the)\s+", "", kandidat, flags=re.IGNORECASE).strip()
    kandidat = kandidat.strip(" \"'„“”‚‘?!.,;:")
    return kandidat if len(kandidat) >= 2 else text


def _wiki_markup_sauber(wert: str) -> str:
    """Wikitext-Auszeichnung zu lesbarem Text; was bleibt, ist geprüfter Text."""
    text = re.sub(r"<ref[^>/]*/>", "", wert)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[(?:https?)://\S+\s+([^\]]*)\]", r"\1", text)
    text = re.sub(r"\[((?:https?)://\S+)\]", r"\1", text)
    text = re.sub(r"'''|''", "", text)
    text = text.replace("&nbsp;", " ").replace("<br>", " ").replace("<br />", " ")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip(" ,;")


def _wiki_wert_ende(block: str, start: int) -> int:
    """Bis zum nächsten Feldzeichen auf oberster Klammer-Ebene."""
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
    lesbar ist, kommen durch. Höchstens zehn Felder, damit die Kachel nicht wächst.
    """
    text = wikitext or ""
    # Unterstützt sowohl `{{Infobox ...` als auch `{{Chemische Elemente` / `{{Taxobox`
    anfang = -1
    for muster in ("{{infobox", "{{chemobox", "{{elementbox", "{{taxobox", "{{unternehmen"):
        pos = text.lower().find(muster)
        if pos >= 0 and (anfang < 0 or pos < anfang):
            anfang = pos
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
                    return text[:_WIKITEXT_MAX]
        return ""
    except Exception:
        return ""


def wikipedia_wissen(query: str, sprache: str = "de") -> dict | None:
    """Holt eine kompakte Wikipedia-Zusammenfassung über das Backend, falls keine SearXNG-Infobox vorlag."""
    q_roh = (query or "").strip()
    if not q_roh or len(q_roh) > 200:
        return None
    q = extrahiere_wissens_begriff(q_roh)[:120]
    if not q:
        return None
    lang = sprache if sprache in ("de", "en", "fr", "es", "it", "nl", "pl", "pt") else "de"
    try:
        # 1. Suche den besten Wikipedia-Artikeltitel (erst mit Kernbegriff, sonst mit Originalfrage)
        page_title = ""
        for suchbegriff in ([q, q_roh[:120]] if q != q_roh[:120] else [q]):
            search_resp = _http_client().get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": suchbegriff,
                    "srlimit": 1,
                    "format": "json",
                },
                headers={
                    "User-Agent": "MauntingSearch/1.0 (https://mauntingstudios.com; privacy-proxy)",
                    "Accept": "application/json",
                },
            )
            if search_resp.status_code != 200:
                continue
            s_data = search_resp.json()
            search_hits = s_data.get("query", {}).get("search", []) if isinstance(s_data, dict) else []
            if search_hits and isinstance(search_hits[0], dict):
                page_title = str(search_hits[0].get("title") or "").strip()
                if page_title:
                    break
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
                items = data[1] if (len(data) >= 2 and isinstance(data[1], list)) else data
                return [
                    redact_sensitive_text(str(s).strip())[:MAX_QUERY_CHARS]
                    for s in items
                    if isinstance(s, str) and s.strip()
                ][:8]
    except Exception:
        pass
    return []


def _brave_kategorie_treffer(
    query: str,
    *,
    key: str,
    limit: int = 10,
    sicher: bool = False,
    sprache: str | None = None,
    kategorie: str | None = None,
) -> list[dict]:
    """Fragt die offizielle Brave Search API (Web, Bilder, Videos, News) ab."""
    kat = (kategorie or "general").lower()
    if kat == "images":
        endpunkt = _BRAVE_IMAGES_ENDPOINT
        params: dict[str, str | int] = {
            "q": query,
            "count": max(1, min(limit, 20)),
            "safesearch": "strict" if sicher else "off",
        }
    elif kat == "videos":
        endpunkt = _BRAVE_VIDEOS_ENDPOINT
        params = {
            "q": query,
            "count": max(1, min(limit, 20)),
            "safesearch": "strict" if sicher else "off",
        }
    elif kat == "news":
        endpunkt = _BRAVE_NEWS_ENDPOINT
        params = {
            "q": query,
            "count": max(1, min(limit, 20)),
            "safesearch": "strict" if sicher else "off",
        }
    else:
        endpunkt = _ENDPOINT
        params = {"q": query, "count": max(1, min(limit, 20))}
        if sicher:
            params["safesearch"] = "strict"
    if sprache and endpunkt != _ENDPOINT:
        params["search_lang"] = sprache[:2].lower()

    resp = _http_client().get(
        endpunkt,
        params=params,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
    )
    if resp.status_code in {401, 403}:
        raise WebSearchUnavailable("AI_WEB_SEARCH_AUTH_FAILED")
    if resp.status_code == 429:
        raise WebSearchUnavailable("AI_WEB_SEARCH_RATE_LIMITED")
    if resp.status_code != 200:
        raise WebSearchUnavailable("AI_WEB_SEARCH_REJECTED")

    payload = resp.json()
    if not isinstance(payload, dict):
        return []

    if kat in ("images", "videos", "news"):
        entries = payload.get("results")
    else:
        web = payload.get("web")
        entries = web.get("results") if isinstance(web, dict) else payload.get("results")

    hits: list[dict] = []
    for entry in entries if isinstance(entries, list) else []:
        if len(hits) >= limit or not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or entry.get("page_url") or "").strip()
        if not url or len(url) > MAX_URL_CHARS or not _is_safe_public_url(url):
            continue
        title = redact_sensitive_text(str(entry.get("title") or ""))[:MAX_TITLE_CHARS]
        snippet = redact_sensitive_text(
            str(entry.get("description") or entry.get("snippet") or "")
        )[:MAX_SNIPPET_CHARS]
        hit: dict = {"title": title, "url": url, "snippet": snippet}

        # Vorschaubild extrahieren und im Medien-Proxy freigeben
        img_url = ""
        props = entry.get("properties")
        thumb = entry.get("thumbnail")
        if isinstance(props, dict) and props.get("url"):
            img_url = str(props.get("url") or "").strip()
        elif isinstance(thumb, dict) and thumb.get("src"):
            img_url = str(thumb.get("src") or "").strip()
        elif isinstance(entry.get("thumbnail"), str):
            img_url = str(entry.get("thumbnail") or "").strip()
        if img_url and len(img_url) <= MAX_URL_CHARS and _is_safe_public_url(img_url):
            hit["bild"] = img_url
            _merke_bild_url(img_url)

        pub = _zeitpunkt(entry.get("page_age") or entry.get("age"))
        if pub:
            hit["published"] = pub
        hits.append(hit)
    return hits


def _google_cse_treffer(
    query: str,
    *,
    api_key_val: str,
    cx_val: str,
    limit: int = 10,
    seite: int = 1,
    sicher: bool = False,
    sprache: str | None = None,
    kategorie: str | None = None,
) -> list[dict]:
    """Fragt die offizielle Google Programmable Search Engine (CSE JSON API) ab."""
    kat = (kategorie or "general").lower()
    if kat in ("videos", "news"):
        # Google CSE unterstützt Web und Image Search direkt
        return []
    start_index = max(1, min(((seite - 1) * 10) + 1, 91))
    params: dict[str, str | int] = {
        "key": api_key_val,
        "cx": cx_val,
        "q": query,
        "num": max(1, min(limit, 10)),
        "start": start_index,
        "safe": "active" if sicher else "off",
    }
    if sprache:
        params["lr"] = f"lang_{sprache[:2].lower()}"
    if kat == "images":
        params["searchType"] = "image"

    try:
        resp = _http_client().get(
            _GOOGLE_CSE_ENDPOINT,
            params=params,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
    except Exception as exc:
        logger.debug("Google CSE nicht erreichbar error=%s", type(exc).__name__)
        return []

    items = payload.get("items") if isinstance(payload, dict) else None
    hits: list[dict] = []
    for item in items if isinstance(items, list) else []:
        if len(hits) >= limit or not isinstance(item, dict):
            continue
        if kat == "images":
            img_info = item.get("image") if isinstance(item.get("image"), dict) else {}
            page_url = str(img_info.get("contextLink") or item.get("link") or "").strip()
            img_src = str(item.get("link") or img_info.get("thumbnailLink") or "").strip()
            if not page_url or len(page_url) > MAX_URL_CHARS or not _is_safe_public_url(page_url):
                continue
            hit: dict = {
                "title": redact_sensitive_text(str(item.get("title") or ""))[:MAX_TITLE_CHARS],
                "url": page_url,
                "snippet": redact_sensitive_text(str(item.get("snippet") or ""))[:MAX_SNIPPET_CHARS],
            }
            if img_src and len(img_src) <= MAX_URL_CHARS and _is_safe_public_url(img_src):
                hit["bild"] = img_src
                _merke_bild_url(img_src)
            hits.append(hit)
        else:
            url = str(item.get("link") or "").strip()
            if not url or len(url) > MAX_URL_CHARS or not _is_safe_public_url(url):
                continue
            hit = {
                "title": redact_sensitive_text(str(item.get("title") or ""))[:MAX_TITLE_CHARS],
                "url": url,
                "snippet": redact_sensitive_text(str(item.get("snippet") or ""))[:MAX_SNIPPET_CHARS],
            }
            pagemap = item.get("pagemap") if isinstance(item.get("pagemap"), dict) else {}
            thumbs = pagemap.get("cse_thumbnail") or pagemap.get("cse_image")
            if isinstance(thumbs, list) and thumbs and isinstance(thumbs[0], dict):
                src = str(thumbs[0].get("src") or "").strip()
                if src and len(src) <= MAX_URL_CHARS and _is_safe_public_url(src):
                    hit["bild"] = src
                    _merke_bild_url(src)
            hits.append(hit)
    return hits


def _normalisiere_url(url: str) -> str:
    """Normalisiert eine Web-Adresse fuer die Deduplizierung.

    Entfernt ueberfluessige Schlussslashs, Tracking-Parameter und vereinheitlicht Scheme/Host.
    """
    url_str = (url or "").strip()
    if not url_str:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url_str)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        query_parts = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered_query = [
            (k, v) for k, v in query_parts
            if not k.lower().startswith("utm_") and k.lower() not in ("ref", "fbclid", "gclid")
        ]
        query = urllib.parse.urlencode(filtered_query)
        return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        return url_str.rstrip("/").lower()


def _mische_treffer(
    primaer_oder_listen: list[dict] | list[list[dict]],
    zusatz: list[dict] | None = None,
    limit: int = MAX_RESULTS,
) -> list[dict]:
    """Vereint und dedupliziert Treffer aus SearXNG und offiziellen Such-APIs.

    Nutzt gewichtetes Round-Robin-Interleaving, damit Treffer aus allen aktiven
    Suchquellen (Brave, SearXNG, Google CSE) im Ergebnis vertreten sind und keine
    Quelle verhungert. Brave erhält dabei hohe Priorität für erstklassige Trefferqualität.
    Finden mehrere Quellen dieselbe URL, wird ein fehlendes Vorschaubild,
    Snippet oder Datum ergänzt, ohne dass ein doppelter Eintrag entsteht.
    """
    if (
        isinstance(primaer_oder_listen, list)
        and primaer_oder_listen
        and isinstance(primaer_oder_listen[0], list)
    ):
        listen = [l for l in primaer_oder_listen if isinstance(l, list)]
    else:
        listen = [primaer_oder_listen, zusatz or []]

    listen_kopien = [[dict(x) for x in l if isinstance(x, dict)] for l in listen]
    gewichte = [2 if i == 0 and len(listen_kopien) > 1 else 1 for i in range(len(listen_kopien))]

    nach_norm_url: dict[str, dict] = {}
    reihenfolge: list[str] = []
    pos = [0] * len(listen_kopien)

    while len(reihenfolge) < limit:
        fortschritt = False
        for idx, (liste, w) in enumerate(zip(listen_kopien, gewichte)):
            entnommen = 0
            while entnommen < w and pos[idx] < len(liste) and len(reihenfolge) < limit:
                eintrag = liste[pos[idx]]
                pos[idx] += 1
                u = str(eintrag.get("url") or "").strip()
                if not u:
                    continue
                norm_u = _normalisiere_url(u)
                if not norm_u:
                    continue
                fortschritt = True
                if norm_u not in nach_norm_url:
                    nach_norm_url[norm_u] = dict(eintrag)
                    reihenfolge.append(norm_u)
                    entnommen += 1
                else:
                    vorhanden = nach_norm_url[norm_u]
                    if not vorhanden.get("bild") and eintrag.get("bild"):
                        vorhanden["bild"] = eintrag["bild"]
                    if not vorhanden.get("snippet") and eintrag.get("snippet"):
                        vorhanden["snippet"] = eintrag["snippet"]
                    if not vorhanden.get("published") and eintrag.get("published"):
                        vorhanden["published"] = eintrag["published"]
                    if not vorhanden.get("title") and eintrag.get("title"):
                        vorhanden["title"] = eintrag["title"]
        if not fortschritt:
            break

    return [nach_norm_url[norm_u] for norm_u in reihenfolge[:limit]]


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
    """Treffer, Vorschläge, Infobox und Direktantworten von Maunting Search.

    Fragt die konfigurierte SearXNG-Instanz ab und fusioniert additiv
    parallel die hinterlegten offiziellen Such-APIs (Brave, Google CSE),
    sofern konfiguriert und erlaubt. Treffer werden dedupliziert und
    nach Qualität gemischt.
    """
    target_url = s_url or searxng_url()
    apis_erlaubt = _apis_aktiv_fuer_instanz(target_url)
    brave_key = api_key() if apis_erlaubt else None
    google_creds = google_cse_credentials() if apis_erlaubt else None

    if not target_url and not brave_key and not google_creds:
        raise WebSearchUnavailable("AI_WEB_SEARCH_NOT_CONFIGURED")

    searxng_hits: list[dict] = []
    suggestions: list[str] = []
    infobox: dict | None = None
    antworten: list[str] = []
    searxng_fehler: WebSearchUnavailable | None = None

    brave_hits: list[dict] = []
    brave_fehler: WebSearchUnavailable | None = None

    google_hits: list[dict] = []

    def _abfrage_searxng() -> None:
        nonlocal searxng_hits, suggestions, infobox, antworten, searxng_fehler
        if not target_url:
            return
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
            raw = data.get("results") if isinstance(data, dict) else None
            for r in raw if isinstance(raw, list) else []:
                if len(searxng_hits) >= limit:
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
                    searxng_hits.append(hit)

            raw_sug = data.get("suggestions") if isinstance(data, dict) else []
            suggestions = (
                [
                    str(s).strip()
                    for s in raw_sug
                    if isinstance(s, str) and s.strip() and len(s) <= MAX_QUERY_CHARS
                ][:8]
                if isinstance(raw_sug, list)
                else []
            )
            infobox = _parse_infobox(data.get("infoboxes") if isinstance(data, dict) else None)
            antworten = _parse_answers(data.get("answers") if isinstance(data, dict) else None)
        except WebSearchUnavailable as exc:
            searxng_fehler = exc
        except Exception as exc:
            logger.debug("SearXNG nicht erreichbar error=%s", type(exc).__name__)
            searxng_fehler = WebSearchUnavailable("AI_WEB_SEARCH_UNAVAILABLE")

    def _abfrage_brave() -> None:
        nonlocal brave_hits, brave_fehler
        if not (brave_key and seite == 1):
            return
        try:
            brave_hits = _brave_kategorie_treffer(
                query,
                key=brave_key,
                limit=limit,
                sicher=sicher,
                sprache=sprache,
                kategorie=kategorie,
            )
        except WebSearchUnavailable as exc:
            brave_fehler = exc
        except Exception as exc:
            logger.debug("Brave Zusatzabfrage fehlgeschlagen: %s", type(exc).__name__)
            brave_fehler = WebSearchUnavailable("AI_WEB_SEARCH_UNAVAILABLE")

    def _abfrage_google() -> None:
        nonlocal google_hits
        if not google_creds:
            return
        try:
            google_hits = _google_cse_treffer(
                query,
                api_key_val=google_creds[0],
                cx_val=google_creds[1],
                limit=min(10, limit),
                seite=seite,
                sicher=sicher,
                sprache=sprache,
                kategorie=kategorie,
            )
        except WebSearchUnavailable as exc:
            logger.debug("Google Zusatzabfrage nicht erreichbar: %s", type(exc).__name__)
        except Exception as exc:
            logger.debug("Google Zusatzabfrage fehlgeschlagen: %s", type(exc).__name__)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_s = executor.submit(_abfrage_searxng) if target_url else None
        f_b = executor.submit(_abfrage_brave) if (brave_key and seite == 1) else None
        f_g = executor.submit(_abfrage_google) if google_creds else None
        for f in (f_s, f_b, f_g):
            if f:
                try:
                    f.result(timeout=10.0)
                except concurrent.futures.TimeoutError:
                    logger.warning("Suchmaschinen-Abfrage im ThreadPoolExecutor hat das 10.0s Timeout überschritten.")
                except Exception as exc:
                    logger.warning("Suchmaschinen-Abfrage fehlgeschlagen: %s", type(exc).__name__)

    if not searxng_hits and not brave_hits and not google_hits:
        if not target_url and not google_creds and brave_fehler is not None:
            raise brave_fehler
        if searxng_fehler is not None:
            raise searxng_fehler

    listen: list[list[dict]] = []
    if brave_hits:
        listen.append(brave_hits)
    if searxng_hits:
        listen.append(searxng_hits)
    if google_hits:
        listen.append(google_hits)

    hits = _mische_treffer(listen, limit=limit) if listen else []

    ergebnis_dict: dict = {"treffer": hits, "vorschlaege": suggestions}
    if infobox:
        ergebnis_dict["infobox"] = infobox
    if antworten:
        ergebnis_dict["antworten"] = antworten
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


def search(query: str, count: int = DEFAULT_SEARCH_COUNT, *, cache_scope: str | None = None) -> list[dict]:
    """Fuehrt eine Websuche ueber Maunting Search (SearXNG + Brave + Google CSE additiv) fuer Singra aus."""
    safe_query = (query or "").strip()[:MAX_QUERY_CHARS]
    if not safe_query:
        raise WebSearchUnavailable("AI_WEB_SEARCH_QUERY_EMPTY")
    limit = max(1, min(int(count or DEFAULT_SEARCH_COUNT), MAX_RESULTS))

    if not ai_web_search_enabled():
        raise WebSearchUnavailable("AI_WEB_SEARCH_NOT_CONFIGURED")

    key = api_key()
    s_url = searxng_url()
    g_creds = google_cse_credentials()

    if key is None and s_url is None and g_creds is None:
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

    searxng_res: list[dict] = []
    brave_res: list[dict] = []
    brave_err: WebSearchUnavailable | None = None
    google_res: list[dict] = []

    def _search_searxng_thread() -> None:
        nonlocal searxng_res
        if s_url:
            searxng_res = _search_searxng(safe_query, limit, s_url=s_url)

    def _search_brave_thread() -> None:
        nonlocal brave_res, brave_err
        if not (key and _apis_aktiv_fuer_instanz(s_url)):
            return
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
                brave_err = WebSearchUnavailable("AI_WEB_SEARCH_AUTH_FAILED")
                return
            if response.status_code == 429:
                logger.warning("Websuche Rate-Limit erreicht (429)")
                brave_err = WebSearchUnavailable("AI_WEB_SEARCH_RATE_LIMITED")
                return
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
                        brave_res.append({
                            "title": redact_sensitive_text(str(entry.get("title") or ""))[:MAX_TITLE_CHARS],
                            "url": url,
                            "snippet": redact_sensitive_text(str(entry.get("description") or ""))[:MAX_SNIPPET_CHARS],
                        })
            else:
                logger.warning("Websuche abgelehnt status=%s text=%s", response.status_code, response.text[:200])
                brave_err = WebSearchUnavailable("AI_WEB_SEARCH_REJECTED")
        except WebSearchUnavailable as exc:
            brave_err = exc
        except Exception as exc:
            logger.info("Brave API nicht erreichbar error=%s", type(exc).__name__)
            brave_err = WebSearchUnavailable("AI_WEB_SEARCH_UNAVAILABLE")

    def _search_google_thread() -> None:
        nonlocal google_res
        if g_creds and _apis_aktiv_fuer_instanz(s_url):
            google_res = _google_cse_treffer(
                safe_query,
                api_key_val=g_creds[0],
                cx_val=g_creds[1],
                limit=limit,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_s = executor.submit(_search_searxng_thread) if s_url else None
        f_b = executor.submit(_search_brave_thread) if (key and _apis_aktiv_fuer_instanz(s_url)) else None
        f_g = executor.submit(_search_google_thread) if (g_creds and _apis_aktiv_fuer_instanz(s_url)) else None
        for f in (f_s, f_b, f_g):
            if f:
                f.result()

    if not searxng_res and not brave_res and not google_res:
        if not key and not g_creds and s_url:
            searxng_res = _search_duckduckgo(safe_query, limit)
        if not searxng_res and brave_err is not None:
            _finish_inflight(cache_key, pending)
            raise brave_err

    quellen: list[list[dict]] = []
    if brave_res:
        quellen.append(brave_res)
    if searxng_res:
        quellen.append(searxng_res)
    if google_res:
        quellen.append(google_res)

    results = _mische_treffer(quellen, limit=limit) if quellen else []

    if cache_key and pending:
        with _cache_lock:
            _cache[cache_key] = (time.monotonic() + _CACHE_TTL_SECONDS, results)
            _inflight.pop(cache_key, None)
            pending.set()
    return results
