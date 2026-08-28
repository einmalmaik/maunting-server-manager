"""Lokale Intent-Erkennung und kurzlebiges, sicheres Tool-Prefetching.

Der Classifier kennt keine sprachspezifischen Entscheidungsregeln. Er vergleicht
Teiltranskripte mit lokalen, mehrsprachigen Satzvektoren. Prefetch-Ergebnisse
bleiben an genau eine Sprachsitzung gebunden und werden nie ohne erneute
Autorisierung verwendet.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from config import settings
from services import ai_embedding_service, ai_tool_registry


MIN_WORDS_DEFAULT = 3
PREFETCH_TTL_SECONDS = 10.0

# Diese Menge ist absichtlich enger als der allgemeine Read-Katalog. Ein neues
# Werkzeug darf erst nach Sicherheitsreview und einem echten Executor hier rein.
SPECULATIVE_READ_TOOLS = frozenset({
    "analyze_region", "web_search", "calendar_read", "read_server_status", "search_memory",
})

_PROTOTYPES = {
    "analyze_region": (
        "regional weather satellite earth observation location analysis",
        "Wetter Satellit regionale Lage Geodaten analysieren",
    ),
    "web_search": ("search current information on the web", "aktuelle Informationen im Web suchen"),
    "calendar_read": ("read my calendar appointments", "meine Kalendereintraege und Termine lesen"),
    "read_server_status": ("read server status health capacity", "Serverstatus Zustand und Auslastung lesen"),
    "search_memory": ("search my saved assistant memory", "mein gespeichertes KI Gedaechtnis durchsuchen"),
}

# Eine kleine Gazetteer-Hilfe ist Entitaetsnormalisierung, keine
# Intent-Klassifikation. Unbekannte Orte werden bewusst nicht geraten.
_KNOWN_LOCATIONS = frozenset({
    "berlin", "hamburg", "muenchen", "münchen", "munich", "koeln", "köln", "cologne",
    "frankfurt", "stuttgart", "duesseldorf", "düsseldorf", "paris", "london", "tokio",
    "tokyo", "washington", "new york", "madrid", "barcelona", "rome", "rom", "vienna",
    "wien", "zurich", "zürich", "moscow", "moskau", "singapore", "singapur", "sydney", "toronto",
})


def configured_confidence() -> float:
    value = getattr(settings, "ai_intent_prefetch_confidence", 0.8)
    return max(0.7, min(0.99, float(value)))


def is_side_effect_free(tool_name: str) -> bool:
    spec = ai_tool_registry.WERKZEUGE.get(tool_name)
    return bool(tool_name in SPECULATIVE_READ_TOOLS and spec and spec.art in {"global_read", "server_read"})


@dataclass(frozen=True)
class IntentPrediction:
    intent: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, Any] = field(default_factory=dict)


def _location(text: str) -> str | None:
    cleaned = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    lower = cleaned.casefold()
    for name in sorted(_KNOWN_LOCATIONS, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", lower):
            return " ".join(part.capitalize() for part in name.split())
    # STT liefert Eigennamen üblicherweise großgeschrieben. Nur ein einzelner
    # Kandidat ist sicher genug; mehrere könnten auch deutsche Substantive sein.
    candidates = re.findall(r"\b[A-ZÄÖÜ][\w-]*(?:\s+[A-ZÄÖÜ][\w-]*){0,2}\b", cleaned)
    return candidates[-1] if len(candidates) == 1 else None


def _query(text: str) -> str:
    return " ".join(text.strip().split())[:200]


class StreamingIntentClassifier:
    """Mehrsprachige, nach dem Warmup netzwerkfreie Intent-Klassifikation."""

    def __init__(self, *, min_words: int = MIN_WORDS_DEFAULT, min_confidence: float | None = None) -> None:
        self.min_words = min_words
        self.min_confidence = configured_confidence() if min_confidence is None else min_confidence
        self._prototype_vectors: dict[str, list[float]] | None = None

    def warm(self) -> bool:
        if self._prototype_vectors is not None:
            return True
        vectors = ai_embedding_service.encode([" ".join(values) for values in _PROTOTYPES.values()])
        if not vectors or len(vectors) != len(_PROTOTYPES):
            return False
        self._prototype_vectors = dict(zip(_PROTOTYPES, vectors, strict=True))
        return True

    def classify(self, text: str) -> IntentPrediction | None:
        words = text.strip().split()
        # Das Laden der Gewichte kann Sekunden dauern und gehört nie auf den
        # Chunk-Pfad. `warm` läuft beim Start der Sprachsitzung im Hintergrund.
        if len(words) < self.min_words or self._prototype_vectors is None:
            return None
        started = time.perf_counter()
        vectors = ai_embedding_service.encode([text.strip()])
        if not vectors:
            return None
        scores = ai_embedding_service.similarity(vectors[0], list(self._prototype_vectors.values()))
        if len(scores) != len(self._prototype_vectors):
            return None
        intent, confidence = max(zip(self._prototype_vectors, scores, strict=True), key=lambda item: item[1])
        if confidence < self.min_confidence:
            return None
        entities: dict[str, Any] = {}
        arguments: dict[str, Any] = {}
        if intent == "analyze_region":
            location = _location(text)
            if not location:
                return None
            entities["location"] = location
            arguments["location"] = location
        elif intent in {"web_search", "search_memory"}:
            query = _query(text)
            if not query:
                return None
            entities["query"] = query
            arguments["query"] = query
        elif intent == "read_server_status":
            match = re.search(r"\b(?:server|node)\s*#?\s*(\d+)\b", text, re.IGNORECASE)
            if not match:
                return None
            server_id = int(match.group(1))
            entities["server_id"] = server_id
            arguments["server_id"] = server_id
        # calendar_read deliberately has no default time range. The classifier
        # may announce it, but never speculates without complete arguments.
        if (time.perf_counter() - started) * 1000 >= 50:
            return None
        return IntentPrediction(intent=intent, confidence=round(float(confidence), 3), entities=entities, arguments=arguments)


@dataclass
class PrefetchEntry:
    session_id: str
    user_id: int
    tool_name: str
    arguments_key: str
    created_at: float
    task: asyncio.Task[Any]
    result: Any = None
    completed: bool = False

    def expired(self) -> bool:
        return time.monotonic() - self.created_at >= PREFETCH_TTL_SECONDS


def _arguments_key(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class PrefetchCache:
    """Ein Cache pro Sprachsitzung, nicht pro Benutzer und nicht global teilbar."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], PrefetchEntry] = {}
        self._lock = threading.RLock()

    async def prefetch(self, *, session_id: str, user_id: int, tool_name: str, arguments: dict[str, Any], executor: Callable[[], Any]) -> asyncio.Task[Any] | None:
        if not is_side_effect_free(tool_name) or not arguments:
            return None
        key = (session_id, user_id)
        args_key = _arguments_key(arguments)
        with self._lock:
            self.invalidate(session_id=session_id, user_id=user_id, keep=(tool_name, args_key))
            current = self._entries.get(key)
            if current and not current.expired() and current.tool_name == tool_name and current.arguments_key == args_key:
                return current.task

            async def run() -> Any:
                try:
                    value = executor()
                    if inspect.isawaitable(value):
                        value = await value
                    entry.result = value
                    entry.completed = True
                    return value
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return None

            task = asyncio.create_task(run())
            entry = PrefetchEntry(session_id, user_id, tool_name, args_key, time.monotonic(), task)
            self._entries[key] = entry
            return task

    def get(self, *, session_id: str | None, user_id: int, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
        from services.ai_latency_metrics import metrics

        if not session_id:
            metrics.record("prefetch", "cache_lookup", 0, "miss")
            return False, None
        with self._lock:
            entry = self._entries.get((session_id, user_id))
            if not entry or entry.expired():
                self.invalidate(session_id=session_id, user_id=user_id)
                metrics.record("prefetch", "cache_lookup", 0, "miss")
                return False, None
            if entry.tool_name != tool_name or entry.arguments_key != _arguments_key(arguments) or not entry.completed:
                metrics.record("prefetch", "cache_lookup", 0, "miss")
                return False, None
            self._entries.pop((session_id, user_id), None)
            metrics.record("prefetch", "cache_lookup", 0, "hit")
            return True, entry.result

    def invalidate(self, *, session_id: str | None = None, user_id: int | None = None, keep: tuple[str, str] | None = None) -> None:
        with self._lock:
            for key, entry in list(self._entries.items()):
                if (session_id is not None and entry.session_id != session_id) or (user_id is not None and entry.user_id != user_id):
                    continue
                if keep and (entry.tool_name, entry.arguments_key) == keep:
                    continue
                if not entry.task.done():
                    entry.task.cancel()
                self._entries.pop(key, None)


classifier = StreamingIntentClassifier()
prefetch_cache = PrefetchCache()


def classify_streaming_intent(text: str) -> IntentPrediction | None:
    return classifier.classify(text)
