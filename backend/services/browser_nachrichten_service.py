"""Nachrichten für die Startseite des Secure Browsers.

Die Schlagzeilen kommen über die SearXNG-Instanz des Servers (Kategorie
``news``, letzter Tag), je Thema ein fester Suchbegriff. Welche Quellen dahinter
stehen, pflegt SearXNG; hier gibt es keine Feedliste.

Geholt wird erst bei Bedarf, höchstens alle 30 Minuten je Sprache, und für
alle Konten gleich: welches Konto welche Themen liest, erfährt der Server
nicht, gefiltert wird im Browser. Der Stand liegt nur im Speicher dieses
Prozesses; nach einem Neustart holt der erste Abruf neu. Scheitert ein
Abruf, bleibt der alte Stand, und vor dem nächsten Versuch vergehen zwei
Minuten.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from services import ai_web_search_service
from services.ai_web_search_service import WebSearchUnavailable

THEMEN: dict[str, dict[str, str]] = {
    "technik": {"de": "Technik", "en": "technology"},
    "gaming": {"de": "Gaming", "en": "video games"},
    "politik": {"de": "Politik", "en": "politics"},
    "wirtschaft": {"de": "Wirtschaft", "en": "economy"},
    "wissenschaft": {"de": "Wissenschaft", "en": "science"},
    "sport": {"de": "Sport", "en": "sports"},
}
SPRACHEN = ("de", "en")
JE_THEMA = 30
INHALT_MAX = 240
FRISCH_S = 30 * 60
NEU_VERSUCH_S = 2 * 60

_sperre = threading.Lock()
# Sprache -> {"geholt": monotonic, "stand": ISO, "themen": {thema: [...]}}
_staende: dict[str, dict] = {}
# Sprache -> (monotonic, Fehlercode) des letzten gescheiterten Abrufs
_fehlschlaege: dict[str, tuple[float, str]] = {}


def vergessen() -> None:
    """Für Tests: der nächste Abruf holt neu."""
    with _sperre:
        _staende.clear()
        _fehlschlaege.clear()


def _eintraege(treffer: list[dict]) -> list[dict]:
    gesehen: set[str] = set()
    eintraege = []
    for t in treffer:
        url = t["url"]
        if not url.startswith("https://") or url in gesehen:
            continue
        gesehen.add(url)
        eintraege.append({
            "titel": t["title"],
            "url": url,
            "inhalt": t["snippet"][:INHALT_MAX],
            "zeit": t.get("published"),
        })
    return eintraege


def _thema_holen(thema: str, sprache: str) -> list[dict]:
    treffer = ai_web_search_service.searxng_treffer(
        THEMEN[thema][sprache],
        limit=JE_THEMA,
        sicher=True,
        sprache=sprache,
        kategorie="news",
        zeitraum="day",
    )
    return _eintraege(treffer)


def nachrichten(sprache: str) -> dict:
    """``{"stand": ISO, "themen": {thema: [{titel, url, inhalt, zeit}]}}``.

    Wirft ``WebSearchUnavailable``, wenn keine Instanz eingerichtet ist oder
    noch kein Stand da ist und SearXNG nicht antwortet.
    """
    if sprache not in SPRACHEN:
        sprache = "en"
    with _sperre:
        alt = _staende.get(sprache)
        jetzt = time.monotonic()
        if alt and jetzt - alt["geholt"] < FRISCH_S:
            return {"stand": alt["stand"], "themen": alt["themen"]}
        fehlschlag = _fehlschlaege.get(sprache)
        if fehlschlag and jetzt - fehlschlag[0] < NEU_VERSUCH_S:
            if alt:
                return {"stand": alt["stand"], "themen": alt["themen"]}
            raise WebSearchUnavailable(fehlschlag[1])
        if not ai_web_search_service.searxng_url():
            raise WebSearchUnavailable("AI_WEB_SEARCH_NOT_CONFIGURED")

        def holen(thema: str) -> list[dict] | None:
            try:
                return _thema_holen(thema, sprache)
            except WebSearchUnavailable:
                return None

        with ThreadPoolExecutor(max_workers=len(THEMEN)) as pool:
            ergebnisse = dict(zip(THEMEN, pool.map(holen, THEMEN)))
        if all(e is None for e in ergebnisse.values()):
            _fehlschlaege[sprache] = (jetzt, "AI_WEB_SEARCH_UNAVAILABLE")
            if alt:
                return {"stand": alt["stand"], "themen": alt["themen"]}
            raise WebSearchUnavailable("AI_WEB_SEARCH_UNAVAILABLE")
        # Ein einzelnes Thema, das scheitert, behält seinen alten Stand.
        themen = {
            thema: neu if neu is not None else (alt["themen"].get(thema, []) if alt else [])
            for thema, neu in ergebnisse.items()
        }
        neu_stand = {"geholt": jetzt, "stand": datetime.now(timezone.utc).isoformat(), "themen": themen}
        _staende[sprache] = neu_stand
        _fehlschlaege.pop(sprache, None)
        return {"stand": neu_stand["stand"], "themen": themen}
