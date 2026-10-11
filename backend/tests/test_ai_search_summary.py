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

        def query(self, model):
            class Query:
                def __init__(self, pool):
                    self.pool = pool

                def filter(self, *conds):
                    res = [
                        x
                        for x in self.pool
                        if isinstance(x, model)
                        and getattr(x, "enabled", False) is True
                        and getattr(x, "search_summary_enabled", False) is True
                    ]
                    return Query(res)

                def order_by(self, *args):
                    return self

                def all(self):
                    return list(self.pool)

                def first(self):
                    return self.pool[0] if self.pool else None

            return Query(self.items)

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
