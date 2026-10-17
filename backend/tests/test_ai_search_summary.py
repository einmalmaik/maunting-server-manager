"""Tests für die neutrale KI-Suchzusammenfassung (`ai_search_summary_service.py`

und `routers/browser_suche.py`).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dependencies import get_current_user, get_db, verify_csrf
from models.ai_provider import AiProvider
from models.user import User
from routers import browser_suche
from services import ai_search_summary_service
from services.openai_compatible_adapter import StreamChunk, StreamUsage


def test_baue_suchkontext_leere_treffer() -> None:
    text = ai_search_summary_service.baue_suchkontext([])
    assert "Keine Suchergebnisse" in text


def test_baue_suchkontext_nummeriert_und_kuerzt() -> None:
    treffer = [
        {"index": 1, "titel": "Erster Treffer", "url": "https://example.com/1", "inhalt": "Wichtiges Wissen 1"},
        {"index": 2, "titel": "Zweiter Treffer", "url": "https://example.com/2", "inhalt": "A" * 1000},
    ]
    kontext = ai_search_summary_service.baue_suchkontext(treffer)
    assert "[1] Erster Treffer" in kontext
    assert "https://example.com/1" in kontext
    assert "[2] Zweiter Treffer" in kontext
    assert len(kontext) < 1500  # Auszug 2 wurde gekürzt


def test_baue_nachrichten_ohne_und_mit_verlauf() -> None:
    treffer = [{"index": 1, "titel": "Test", "url": "https://example.com", "inhalt": "Inhalt"}]
    # 1. Ohne Verlauf
    msgs = ai_search_summary_service.baue_nachrichten("Wie backe ich Brot?", treffer)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "Nutzeranfrage:\nWie backe ich Brot?" in msgs[1]["content"]

    # 2. Mit Verlauf (Nachfrage)
    verlauf = [
        {"role": "assistant", "content": "Brot backt man mit Mehl und Hefe."},
        {"role": "user", "content": "Und wie viel Wasser?"},
    ]
    msgs_verlauf = ai_search_summary_service.baue_nachrichten("Wie backe ich Brot?", treffer, verlauf)
    assert len(msgs_verlauf) == 4
    assert msgs_verlauf[0]["role"] == "system"
    assert msgs_verlauf[1]["role"] == "user"
    assert msgs_verlauf[2]["role"] == "assistant"
    assert msgs_verlauf[3]["role"] == "user"
    assert msgs_verlauf[3]["content"] == "Und wie viel Wasser?"


@pytest.fixture
def db():
    class MockDb:
        def __init__(self):
            self.items = []

        def add(self, item):
            if not hasattr(item, "id") or item.id is None:
                item.id = len(self.items) + 1
            if item not in self.items:
                self.items.append(item)

        def flush(self):
            pass

        def get(self, model, ident):
            for it in self.items:
                if isinstance(it, model) and getattr(it, "id", None) == ident:
                    return it
            return None

        def query(self, *entities):
            class Query:
                def __init__(self, pool):
                    self.pool = pool

                def filter(self, *conds):
                    if len(entities) == 1 and isinstance(entities[0], type):
                        model = entities[0]
                        res = [
                            x
                            for x in self.pool
                            if isinstance(x, model)
                            and getattr(x, "enabled", False) is True
                            and getattr(x, "search_summary_enabled", False) is True
                        ]
                    else:
                        res = []
                    return Query(res)

                def order_by(self, *args):
                    return self

                def all(self):
                    return list(self.pool)

                def first(self):
                    return self.pool[0] if self.pool else None

            if len(entities) == 1 and isinstance(entities[0], type):
                return Query([x for x in self.items if isinstance(x, entities[0])])
            return Query([])

    return MockDb()


def test_suchzusammenfassung_anbieter_auswahl(db) -> None:
    # 1. Kein Anbieter eingerichtet -> None
    user = User(
        username="searcher",
        password_hash="pw",
        is_active=True,
        is_owner=True,
    )
    db.add(user)
    db.flush()

    assert ai_search_summary_service.suchzusammenfassung_anbieter(db, user) is None

    # 2. Anbieter ohne search_summary_enabled -> None
    p1 = AiProvider(
        name="OpenAI Standard",
        provider_kind="openai",
        default_model="gpt-4o",
        enabled=True,
        requires_api_key=False,
        search_summary_enabled=False,
        search_summary_model="gpt-4o-mini",
    )
    db.add(p1)
    db.flush()
    assert ai_search_summary_service.suchzusammenfassung_anbieter(db, user) is None

    # 3. Anbieter mit search_summary_enabled -> Gefunden!
    p1.search_summary_enabled = True
    db.flush()
    gefunden = ai_search_summary_service.suchzusammenfassung_anbieter(db, user)
    assert gefunden is not None
    prov, model = gefunden
    assert prov.id == p1.id
    assert model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_stream_suchzusammenfassung_deaktiviert(db, monkeypatch) -> None:
    user = User(
        username="stream_user",
        password_hash="pw",
        is_active=True,
        is_owner=True,
    )
    db.add(user)
    db.flush()

    monkeypatch.setattr("services.permission_service.has_global_permission", lambda _db, _u, _k: True)

    # Keine Anbieter aktiv
    events = [
        event async for event in ai_search_summary_service.stream_suchzusammenfassung(
            db=db,
            user=user,
            q="Test",
            treffer=[],
        )
    ]
    assert len(events) == 1
    assert "disabled" in events[0]
    assert "not_configured" in events[0]


@pytest.mark.asyncio
async def test_stream_suchzusammenfassung_erfolg(db, monkeypatch) -> None:
    user = User(
        username="stream_user_ok",
        password_hash="pw",
        is_active=True,
        is_owner=True,
    )
    p = AiProvider(
        name="Search Provider",
        provider_kind="openai",
        default_model="gpt-4o",
        enabled=True,
        requires_api_key=False,
        search_summary_enabled=True,
        search_summary_model="gpt-4o-mini",
    )
    db.add(user)
    db.add(p)
    db.flush()

    monkeypatch.setattr("services.permission_service.has_global_permission", lambda _db, _u, _k: True)

    async def fake_stream(*args, **kwargs) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(kind="content", text="Hier ist ")
        yield StreamChunk(kind="content", text="die Zusammenfassung [1].")

    monkeypatch.setattr(ai_search_summary_service, "stream_chat_completion", fake_stream)

    events = [
        event async for event in ai_search_summary_service.stream_suchzusammenfassung(
            db=db,
            user=user,
            q="Test",
            treffer=[{"index": 1, "titel": "Titel", "url": "https://example.com", "inhalt": "Text"}],
        )
    ]

    # start, content 1, content 2, done
    assert len(events) == 4
    assert '"type": "start"' in events[0]
    assert '"text": "Hier ist "' in events[1]
    assert '"text": "die Zusammenfassung [1]."' in events[2]
    assert '"type": "done"' in events[3]


def test_deute_umschreibung_gueltig_und_rueckfall() -> None:
    gut = ai_search_summary_service._deute_umschreibung(
        '{"suchanfragen": ["GTA 6 Release Datum", "GTA 6 Preis"], "themenwechsel": true}',
        "Wann kommt das raus?",
    )
    assert gut == {"suchanfragen": ["GTA 6 Release Datum", "GTA 6 Preis"], "themenwechsel": True}

    # Markdown-Zaun wird abgestreift
    zaun = ai_search_summary_service._deute_umschreibung(
        '```json\n{"suchanfragen": ["Gulasch Rezept"], "themenwechsel": true}\n```',
        "Gulaschrezept?",
    )
    assert zaun["suchanfragen"] == ["Gulasch Rezept"]
    assert zaun["themenwechsel"] is True

    # Mehr als drei Anfragen werden beschnitten
    viele = ai_search_summary_service._deute_umschreibung(
        json.dumps({"suchanfragen": ["a", "b", "c", "d", "e"], "themenwechsel": False}),
        "q",
    )
    assert viele["suchanfragen"] == ["a", "b", "c"]

    # Jede Abweichung fällt auf die wörtliche Nachsuche ohne Themenwechsel zurück
    for kaputt in ["", "kein json", "[1, 2]", '{"anders": 1}', '{"suchanfragen": []}']:
        assert ai_search_summary_service._deute_umschreibung(kaputt, "Originalfrage") == {
            "suchanfragen": ["Originalfrage"],
            "themenwechsel": False,
        }


def _nutzer_mit_anbieter(db) -> tuple:
    user = User(username="recherche_user", password_hash="pw", is_active=True, is_owner=True)
    p = AiProvider(
        name="Search Provider",
        provider_kind="openai",
        default_model="gpt-4o",
        enabled=True,
        requires_api_key=False,
        search_summary_enabled=True,
        search_summary_model="gpt-4o-mini",
    )
    db.add(user)
    db.add(p)
    db.flush()
    return user, p


async def _ereignisse_sammeln(service, **kwargs) -> list[str]:
    return [event async for event in service.stream_suchzusammenfassung(**kwargs)]


def _ereignis_typen(events: list[str]) -> list[str]:
    return [json.loads(e.split("data:", 1)[1])["type"] for e in events]


@pytest.mark.asyncio
async def test_folgefrage_schreibt_um_sucht_zweimal_und_oeffnet_zwei_seiten(db, monkeypatch) -> None:
    user, _ = _nutzer_mit_anbieter(db)
    monkeypatch.setattr("services.permission_service.has_global_permission", lambda _db, _u, _k: True)

    anfragen: list[str] = []

    def fake_treffer(query: str, limit: int = 5):
        anfragen.append(query)
        return [{"title": f"Treffer zu {query}", "url": f"https://example.com/{query}", "snippet": "Auszug"}]

    monkeypatch.setattr(ai_search_summary_service.ai_web_search_service, "searxng_treffer", fake_treffer)

    ge_scrapet: list[str] = []

    def fake_scrape(url: str, max_chars: int = 3500, *args, **kwargs):
        ge_scrapet.append(url)
        return {"title": "Seite", "url": url, "content": "Voller Seiteninhalt"}

    monkeypatch.setattr(ai_search_summary_service.ai_web_search_service, "scrape_url", fake_scrape)

    async def fake_stream(*args, **kwargs) -> AsyncIterator[StreamChunk]:
        messages = kwargs.get("messages", [])
        if messages and messages[0].get("role") == "system" and "Metasuche" in messages[0].get("content", ""):
            yield StreamChunk(
                kind="content",
                text='{"suchanfragen": ["Grafikkarte einbauen Anleitung", "Grafikkarte Stromanschluss", "dritte Anfrage"], "themenwechsel": true}',
            )
        else:
            yield StreamChunk(kind="content", text="Antwort [3].")

    monkeypatch.setattr(ai_search_summary_service, "stream_chat_completion", fake_stream)

    events = await _ereignisse_sammeln(
        ai_search_summary_service,
        db=db,
        user=user,
        q="Und wie baue ich eine Grafikkarte ein?",
        treffer=[{"index": 1, "titel": "GTA 6", "url": "https://example.com/gta", "inhalt": "Release"}],
        verlauf=[{"role": "user", "content": "Wann kommt GTA 6?"}],
    )
    typen = _ereignis_typen(events)

    # Zwei Nachsuchen (nicht drei), danach neue Treffer, dann Antwort
    assert anfragen == ["Grafikkarte einbauen Anleitung", "Grafikkarte Stromanschluss"]
    assert typen.count("recherche") == 2
    assert "neue_treffer" in typen
    assert "done" in typen
    # Höchstens zwei Seiten geöffnet
    assert len(ge_scrapet) == 2
    # Themenwechsel: alte GTA-Treffer sind raus, Kennziffern beginnen frisch
    treffer_event = json.loads([e for e in events if '"neue_treffer"' in e][0].split("data:", 1)[1])
    assert [t["index"] for t in treffer_event["treffer"]] == [1, 2]


@pytest.mark.asyncio
async def test_vertiefung_behaelt_alte_treffer_und_trennt_abschnitte(db, monkeypatch) -> None:
    user, _ = _nutzer_mit_anbieter(db)
    monkeypatch.setattr("services.permission_service.has_global_permission", lambda _db, _u, _k: True)
    monkeypatch.setattr(
        ai_search_summary_service.ai_web_search_service,
        "searxng_treffer",
        lambda query, limit=5: [{"title": "Neu", "url": "https://example.com/neu", "snippet": "Frisch"}],
    )
    monkeypatch.setattr(
        ai_search_summary_service.ai_web_search_service,
        "scrape_url",
        lambda url, max_chars=3500, *args, **kwargs: {"title": "S", "url": url, "content": ""},
    )

    gesehen: dict = {}

    async def fake_stream(*args, **kwargs) -> AsyncIterator[StreamChunk]:
        messages = kwargs.get("messages", [])
        if messages and messages[0].get("role") == "system" and "Metasuche" in messages[0].get("content", ""):
            yield StreamChunk(kind="content", text='{"suchanfragen": ["Brot Wasser Menge"], "themenwechsel": false}')
        else:
            gesehen["messages"] = messages
            yield StreamChunk(kind="content", text="Antwort [1] [2].")

    monkeypatch.setattr(ai_search_summary_service, "stream_chat_completion", fake_stream)

    alter = [{"index": 1, "titel": "Brot", "url": "https://example.com/brot", "inhalt": "Mehl und Hefe"}]
    events = await _ereignisse_sammeln(
        ai_search_summary_service,
        db=db,
        user=user,
        q="Und wie viel Wasser?",
        treffer=alter,
        verlauf=[{"role": "user", "content": "Wie backe ich Brot?"}],
    )
    assert "done" in _ereignis_typen(events)
    nutzer_text = [m for m in gesehen["messages"] if m["role"] == "user"][0]["content"]
    assert "Treffer zur Ausgangssuche:" in nutzer_text
    assert "Frische Treffer zur Nachfrage:" in nutzer_text
    assert "Mehl und Hefe" in nutzer_text
    assert "Frisch" in nutzer_text


@pytest.mark.asyncio
async def test_kaputtes_umschreiben_faellt_auf_woertliche_suche_zurueck(db, monkeypatch) -> None:
    user, _ = _nutzer_mit_anbieter(db)
    monkeypatch.setattr("services.permission_service.has_global_permission", lambda _db, _u, _k: True)
    monkeypatch.setattr(
        ai_search_summary_service.ai_web_search_service,
        "searxng_treffer",
        lambda query, limit=5: [],
    )
    monkeypatch.setattr(
        ai_search_summary_service.ai_web_search_service,
        "scrape_url",
        lambda url, max_chars=3500, *args, **kwargs: {"title": "S", "url": url, "content": ""},
    )

    # Umschreiben liefert kein JSON, die Antwort danach läuft normal
    aufrufe = {"n": 0}

    async def zaehlender_stream(*args, **kwargs) -> AsyncIterator[StreamChunk]:
        aufrufe["n"] += 1
        if aufrufe["n"] == 1:
            yield StreamChunk(kind="content", text="kein json, nur text")
        else:
            yield StreamChunk(kind="content", text="Antwort")

    monkeypatch.setattr(ai_search_summary_service, "stream_chat_completion", zaehlender_stream)

    events = await _ereignisse_sammeln(
        ai_search_summary_service,
        db=db,
        user=user,
        q="Irgendwas?",
        treffer=[],
        verlauf=[{"role": "user", "content": "Hallo"}],
    )
    assert "done" in _ereignis_typen(events)
    assert aufrufe["n"] == 2  # Umschreiben + Antwort, kein Abbruch


def test_browser_suche_zusammenfassung_route(db, monkeypatch) -> None:
    app = FastAPI()
    app.include_router(browser_suche.router)

    user = User(
        username="route_user",
        password_hash="pw",
        is_active=True,
        is_owner=True,
    )
    db.add(user)
    db.flush()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[verify_csrf] = lambda: None

    async def fake_stream_svc(*args, **kwargs) -> AsyncIterator[str]:
        yield 'data: {"type": "content", "text": "Antwort"}\n\n'
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(ai_search_summary_service, "stream_suchzusammenfassung", fake_stream_svc)

    client = TestClient(app)
    resp = client.post(
        "/api/browser/suche/zusammenfassung",
        json={
            "q": "Wie funktioniert das?",
            "treffer": [{"index": 1, "titel": "Doku", "url": "https://test.local", "inhalt": "Info"}],
            "verlauf": [],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert 'data: {"type": "content", "text": "Antwort"}' in body
    assert 'data: {"type": "done"}' in body


def test_browser_suche_konfig_route(db, monkeypatch) -> None:
    app = FastAPI()
    app.include_router(browser_suche.router)

    user = User(
        username="konfig_user",
        password_hash="pw",
        is_active=True,
        is_owner=True,
    )
    db.add(user)
    db.flush()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db

    monkeypatch.setattr(
        ai_search_summary_service,
        "suchzusammenfassung_konfig",
        lambda db, user: {"aktiviert": True, "modell": "test-model", "denkstufen": ["minimal", "low"], "max_denkstufe": "low"},
    )

    client = TestClient(app)
    resp = client.get("/api/browser/suche/konfig")
    assert resp.status_code == 200
    daten = resp.json()
    assert daten["aktiviert"] is True
    assert daten["modell"] == "test-model"
    assert daten["denkstufen"] == ["minimal", "low"]
    assert daten["max_denkstufe"] == "low"


def test_baue_suchkontext_mit_volltext() -> None:
    treffer = [
        {
            "index": 1,
            "titel": "Witcher 4 News",
            "url": "https://example.com/w4",
            "inhalt": "Kurzes Snippet",
            "volltext": "Das Spiel wird auf Basis der Unreal Engine 5 entwickelt. Release nicht vor 2026.",
        }
    ]
    kontext = ai_search_summary_service.baue_suchkontext(treffer)
    assert "[1] Witcher 4 News" in kontext
    assert "Auszug: Kurzes Snippet" in kontext
    assert "Volltext-Auszug: Das Spiel wird auf Basis der Unreal Engine 5 entwickelt" in kontext


@pytest.mark.asyncio
async def test_stream_suchzusammenfassung_scrapes_initial_top_results(db, monkeypatch) -> None:
    user, _ = _nutzer_mit_anbieter(db)
    monkeypatch.setattr("services.permission_service.has_global_permission", lambda _db, _u, _k: True)

    scraped_urls = []

    def fake_scrape_parallel(urls, max_chars=2500, limit=4):
        scraped_urls.extend(urls)
        return {u: f"Extrahierter Volltext fuer {u}" for u in urls}

    monkeypatch.setattr(
        ai_search_summary_service.ai_web_search_service,
        "scrape_top_urls_parallel",
        fake_scrape_parallel,
    )

    gesehen: dict = {}

    async def fake_stream(*args, **kwargs) -> AsyncIterator[StreamChunk]:
        gesehen["messages"] = kwargs.get("messages", [])
        yield StreamChunk(kind="content", text="Antwort basierend auf Volltext.")

    monkeypatch.setattr(ai_search_summary_service, "stream_chat_completion", fake_stream)

    treffer = [
        {"index": 1, "titel": "Witcher 4 Info", "url": "https://example.com/1", "inhalt": "Snippet 1"},
        {"index": 2, "titel": "CDPR Ankündigung", "url": "https://example.com/2", "inhalt": "Snippet 2"},
    ]

    events = await _ereignisse_sammeln(
        ai_search_summary_service,
        db=db,
        user=user,
        q="Wann kommt The Witcher 4 raus?",
        treffer=treffer,
        verlauf=[],
    )
    assert "done" in _ereignis_typen(events)
    assert "https://example.com/1" in scraped_urls
    assert "https://example.com/2" in scraped_urls

    nutzer_msg = [m for m in gesehen["messages"] if m["role"] == "user"][0]["content"]
    assert "Volltext-Auszug: Extrahierter Volltext fuer https://example.com/1" in nutzer_msg


@pytest.mark.asyncio
async def test_stream_suchzusammenfassung_followup_preserves_fulltext(db, monkeypatch) -> None:
    user, _ = _nutzer_mit_anbieter(db)
    monkeypatch.setattr("services.permission_service.has_global_permission", lambda _db, _u, _k: True)

    async def fake_umschreiben(*args, **kwargs):
        return {"suchanfragen": ["Witcher 4 Release"], "themenwechsel": False}

    monkeypatch.setattr(
        ai_search_summary_service,
        "suchanfragen_umschreiben",
        fake_umschreiben,
    )

    def fake_searx_treffer(query, limit=5):
        return [{
            "title": "Frische News",
            "url": "https://example.com/frisch",
            "snippet": "Neuer Snippet-Text",
        }]

    monkeypatch.setattr(ai_search_summary_service.ai_web_search_service, "searxng_treffer", fake_searx_treffer)
    monkeypatch.setattr(
        ai_search_summary_service.ai_web_search_service,
        "scrape_url",
        lambda url, max_chars=3500, *args, **kwargs: {"title": "Frische News", "url": url, "content": "Volltext ueber 1000 Zeichen: " + ("Detail " * 150)},
    )

    gesehen: dict = {}

    async def fake_stream(*args, **kwargs) -> AsyncIterator[StreamChunk]:
        gesehen["messages"] = kwargs.get("messages", [])
        yield StreamChunk(kind="content", text="Antwort basierend auf Volltext.")

    monkeypatch.setattr(ai_search_summary_service, "stream_chat_completion", fake_stream)

    treffer = [{"index": 1, "titel": "Alte News", "url": "https://example.com/alt", "inhalt": "Alt"}]
    verlauf = [{"role": "user", "content": "Erste Frage"}, {"role": "assistant", "content": "Erste Antwort"}]

    events = await _ereignisse_sammeln(
        ai_search_summary_service,
        db=db,
        user=user,
        q="Gibt es schon einen Trailer?",
        treffer=treffer,
        verlauf=verlauf,
    )
    assert "done" in _ereignis_typen(events)
    nutzer_msg = [m for m in gesehen["messages"] if m["role"] == "user"][0]["content"]
    assert "Volltext-Auszug: Volltext ueber 1000 Zeichen:" in nutzer_msg


def test_erkenne_deep_dive_quellen() -> None:
    treffer = [
        {"index": 1, "titel": "Tom's Hardware RTX 5090", "url": "https://www.tomshardware.com/gpu/5090"},
        {"index": 2, "titel": "Heise Online Benchmark", "url": "https://www.heise.de/news/benchmarks"},
        {"index": 3, "titel": "ComputerBase Test", "url": "https://www.computerbase.de/artikel"},
    ]
    # 1. Direkte Indizes [1] oder [2]
    q1 = ai_search_summary_service.erkenne_deep_dive_quellen("Lies bitte Quelle [2] genauer.", treffer)
    assert len(q1) == 1
    assert q1[0]["index"] == 2

    # 2. Textuelle Referenzen ("erste Quelle")
    q2 = ai_search_summary_service.erkenne_deep_dive_quellen("Was steht in der ersten Quelle zu Benchmarks?", treffer)
    assert len(q2) == 1
    assert q2[0]["index"] == 1

    # 3. Domain-Bezug
    q3 = ai_search_summary_service.erkenne_deep_dive_quellen("Was berichtet heise ueber die Ergebnisse?", treffer)
    assert len(q3) == 1
    assert q3[0]["index"] == 2

    # 4. Allgemeine Detailanfrage -> Top 2 Quellen
    q4 = ai_search_summary_service.erkenne_deep_dive_quellen("Zeige mir die genauen FPS und Benchmarks im Detail", treffer)
    assert len(q4) == 2
    assert [t["index"] for t in q4] == [1, 2]

    # 5. Keine Detailanfrage
    q5 = ai_search_summary_service.erkenne_deep_dive_quellen("Danke fuer die Antwort!", treffer)
    assert len(q5) == 0


def test_baue_suchkontext_deep_dive() -> None:
    normaler_text = "Standard " * 400
    deep_text = "Benchmark Tabellenwerte " * 500

    treffer = [
        {"index": 1, "titel": "Normaler Treffer", "url": "https://example.com/1", "inhalt": "Auszug", "volltext": normaler_text, "deep": False},
        {"index": 2, "titel": "Deep Treffer", "url": "https://example.com/2", "inhalt": "Auszug", "volltext": deep_text, "deep": True},
    ]

    kontext = ai_search_summary_service.baue_suchkontext(treffer)
    assert "[1] Normaler Treffer" in kontext
    assert "Volltext-Auszug:" in kontext
    assert len(kontext.split("[2]")[0]) < 3000

    assert "[2] Deep Treffer" in kontext
    assert "Volltext-Auszug (Deep-Dive):" in kontext
    assert len(kontext.split("[2]")[1]) > 5000



