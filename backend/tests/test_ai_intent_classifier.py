"""Vertragstests für lokale Intent-Erkennung und Sitzungs-Prefetch."""

import asyncio
import time

import pytest

from services import ai_embedding_service
from services.ai_intent_classifier import (
    PREFETCH_TTL_SECONDS,
    SPECULATIVE_READ_TOOLS,
    PrefetchCache,
    StreamingIntentClassifier,
    is_side_effect_free,
)


CASES = [
    ("Wie ist das Wetter in Berlin heute", "analyze_region"),
    ("Show satellite information for London", "analyze_region"),
    ("Wie ist das Wetter in Moskau heute", "analyze_region"),
    ("Suche aktuelle Nachrichten zu OpenSSH", "web_search"),
    ("Search the web for Python releases", "web_search"),
    ("Welche Termine habe ich im Kalender", "calendar_read"),
    ("Read my calendar appointments", "calendar_read"),
    ("Status von Server 7", "read_server_status"),
    ("Read server 7 health", "read_server_status"),
    ("Durchsuche mein Gedächtnis nach Backup", "search_memory"),
    ("Search my saved memory for backups", "search_memory"),
]


def _semantic_stub(monkeypatch):
    def one(text):
        text = text.casefold()
        if "memory" in text or "gedächtnis" in text:
            return [0, 0, 0, 0, 1.0]
        if "weather" in text or "wetter" in text or "satellite" in text:
            return [1.0, 0, 0, 0, 0]
        if "search" in text or "suche" in text:
            return [0, 1.0, 0, 0, 0]
        if "calendar" in text or "kalender" in text:
            return [0, 0, 1.0, 0, 0]
        if "server" in text or "node" in text:
            return [0, 0, 0, 1.0, 0]
        return [0, 0, 0, 0, 1.0]

    def encode(texts):
        return [one(text) for text in texts]

    monkeypatch.setattr(ai_embedding_service, "encode", encode)
    monkeypatch.setattr(ai_embedding_service, "similarity", lambda query, _: query)


def test_multilingual_semantic_predictions(monkeypatch):
    _semantic_stub(monkeypatch)
    classifier = StreamingIntentClassifier(min_confidence=0.8)
    assert classifier.warm()
    for text, expected in CASES:
        prediction = classifier.classify(text)
        assert prediction is not None, text
        assert prediction.intent == expected
    assert classifier.classify("Wetter Berlin") is None


def test_classifier_is_fast_after_warmup(monkeypatch):
    _semantic_stub(monkeypatch)
    classifier = StreamingIntentClassifier(min_confidence=0.8)
    assert classifier.warm()
    started = time.perf_counter()
    for _ in range(100):
        assert classifier.classify("Wie ist das Wetter in Berlin heute")
    assert (time.perf_counter() - started) / 100 < 0.05


def test_allowlist_is_closed_to_read_tools():
    assert SPECULATIVE_READ_TOOLS == {
        "analyze_region", "web_search", "calendar_read", "read_server_status", "search_memory",
    }
    assert not is_side_effect_free("propose_server_delete")
    assert not is_side_effect_free("calendar_create")
    assert not is_side_effect_free("send_email")


@pytest.mark.asyncio
async def test_prefetch_cache_is_session_scoped_and_consumed():
    cache = PrefetchCache()
    task = await cache.prefetch(
        session_id="voice-a", user_id=7, tool_name="analyze_region",
        arguments={"location": "Berlin"}, executor=lambda: {"location": "Berlin"},
    )
    assert task is not None
    await task
    assert cache.get(session_id="voice-b", user_id=7, tool_name="analyze_region", arguments={"location": "Berlin"}) == (False, None)
    assert cache.get(session_id="voice-a", user_id=7, tool_name="analyze_region", arguments={"location": "Berlin"}) == (True, {"location": "Berlin"})


@pytest.mark.asyncio
async def test_intent_switch_cancels_old_prefetch():
    cache = PrefetchCache()
    cancelled = False

    async def slow():
        nonlocal cancelled
        try:
            await asyncio.sleep(PREFETCH_TTL_SECONDS)
        except asyncio.CancelledError:
            cancelled = True
            raise

    first = await cache.prefetch(
        session_id="voice-a", user_id=7, tool_name="analyze_region",
        arguments={"location": "Berlin"}, executor=slow,
    )
    second = await cache.prefetch(
        session_id="voice-a", user_id=7, tool_name="analyze_region",
        arguments={"location": "Paris"}, executor=lambda: {"location": "Paris"},
    )
    await second
    await asyncio.sleep(0)
    assert first.cancelled() or cancelled
    assert cache.get(session_id="voice-a", user_id=7, tool_name="analyze_region", arguments={"location": "Berlin"}) == (False, None)
