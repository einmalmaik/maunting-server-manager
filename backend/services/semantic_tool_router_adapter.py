from __future__ import annotations

import json
import math
import re
from collections import Counter

from services import ai_embedding_service
from services.ai_tool_registry import WERKZEUGE
from services.tool_selection_port import HOTSET


def _tool_searchable(name: str, schema: dict | None) -> str:
    desc = ""
    params = ""
    if schema:
        func = schema.get("function", schema) if isinstance(schema, dict) else {}
        desc = func.get("description", "") if isinstance(func, dict) else ""
        parameters = func.get("parameters", {}) if isinstance(func, dict) else {}
        props = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
        if isinstance(props, dict):
            parts = []
            for k, v in props.items():
                if isinstance(v, dict):
                    d = v.get("description", "")
                    parts.append(f"{k}: {d}")
            params = " ".join(parts)
    return f"{name}: {desc} {params}".strip()


def _bm25_score(query: str, doc: str) -> float:
    q_terms = re.findall(r"\w+", query.lower())
    d_terms = re.findall(r"\w+", doc.lower())
    if not q_terms or not d_terms:
        return 0.0
    counter = Counter(d_terms)
    n = len(d_terms)
    score = 0.0
    for t in q_terms:
        tf = counter.get(t, 0)
        if tf == 0:
            continue
        idf = 1.0
        score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * n / 20))
    return score / (len(q_terms) * 2)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


class SemanticToolRouterAdapter:
    def __init__(self, tool_schemas: dict[str, dict] | None = None):
        self._schemas: dict[str, dict] = tool_schemas or {}
        self._searchable: dict[str, str] = {}
        self._vectors: dict[str, list[float]] | None = None
        self._ready = False

    def _ensure_index(self, allowed: frozenset[str]) -> None:
        needed = sorted({n for n in allowed if n not in self._searchable})
        missing_vectors = False
        if self._vectors is not None:
            missing_vectors = any(n not in self._vectors for n in allowed if n in self._searchable)
        if not needed and not missing_vectors:
            return
        for name in needed:
            schema = self._schemas.get(name)
            if schema is None:
                try:
                    from services import ai_action_service
                    for e in ai_action_service.provider_tool_definitions() + ai_action_service.voice_control_tool_definitions():
                        func = e.get("function", e) if isinstance(e, dict) else {}
                        if func.get("name") == name:
                            schema = e
                            break
                except Exception:
                    schema = None
            self._searchable[name] = _tool_searchable(name, schema)
        if ai_embedding_service.is_ready():
            to_encode = needed
            if missing_vectors and not needed:
                to_encode = sorted({n for n in allowed if n not in (self._vectors or {})})
            if to_encode:
                texts = [self._searchable[n] for n in to_encode]
                vecs = ai_embedding_service.encode(texts)
                if vecs and len(vecs) == len(to_encode):
                    if self._vectors is None:
                        self._vectors = {}
                    for n, v in zip(to_encode, vecs):
                        self._vectors[n] = v
                    self._ready = True

    def warm(self, allowed: frozenset[str]) -> None:
        self._ensure_index(allowed)

    def select(self, query: str, allowed: frozenset[str], top_k: int = 5) -> list[str]:
        if not query or not query.strip() or not allowed:
            return []
        query = query.strip()
        self._ensure_index(allowed)
        hot_in_allowed = [n for n in HOTSET if n in allowed]
        candidates = sorted([n for n in allowed if n not in hot_in_allowed])
        if not candidates:
            return []
        q_vec = None
        if self._vectors is not None:
            qv = ai_embedding_service.encode([query])
            if qv:
                q_vec = qv[0]
        scored: list[tuple[float, str]] = []
        max_bm25 = 0.0
        bm25_scores: dict[str, float] = {}
        for name in candidates:
            doc = self._searchable.get(name, name)
            s = _bm25_score(query, doc)
            bm25_scores[name] = s
            max_bm25 = max(max_bm25, s)
        for name in candidates:
            bm25 = bm25_scores[name] / max_bm25 if max_bm25 > 0 else 0
            cos = 0.0
            if q_vec is not None and self._vectors and name in self._vectors:
                cos = _cosine(q_vec, self._vectors[name])
                cos = (cos + 1.0) / 2.0
            hybrid = 0.6 * cos + 0.4 * bm25
            scored.append((hybrid, name))
        scored.sort(reverse=True, key=lambda x: x[0])
        return [n for _, n in scored[:top_k]]

    def select_with_hot(self, query: str, allowed: frozenset[str], top_k: int = 5) -> list[str]:
        routed = self.select(query, allowed, top_k=top_k)
        hot = [n for n in HOTSET if n in allowed]
        seen = set(hot)
        result = list(hot)
        # Cluster-Erweiterung: Wenn ein Werkzeug gewählt wurde, schalte seine gesamte Gruppe frei
        group_peers: list[str] = []
        for n in routed:
            w = WERKZEUGE.get(n)
            if w and w.gruppe:
                for peer_name, peer_w in WERKZEUGE.items():
                    if peer_w.gruppe == w.gruppe and peer_name in allowed and peer_name not in seen:
                        group_peers.append(peer_name)
                        seen.add(peer_name)
        for n in routed:
            if n not in seen:
                result.append(n)
                seen.add(n)
        for n in group_peers:
            result.append(n)
        return result
