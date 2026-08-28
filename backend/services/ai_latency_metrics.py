"""Kurzlebige, in-prozess Latenzmetriken für KI-Werkzeuge.

Die Metriken enthalten bewusst nur technische Kategorien und Dauern. Weder
Nutzereingaben noch Provider-Antworten, IDs oder Zugangsdaten werden erfasst.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import threading
import time


_RETENTION_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class LatencySample:
    component: str
    stage: str
    outcome: str
    duration_ms: int
    recorded_at: float


class LatencyMetrics:
    """Thread-safe Ringpuffer ohne fachliche oder personenbezogene Daten."""

    def __init__(self) -> None:
        self._samples: deque[LatencySample] = deque()
        self._lock = threading.Lock()

    def record(self, component: str, stage: str, duration_ms: float, outcome: str = "ok") -> None:
        sample = LatencySample(
            component=component,
            stage=stage,
            outcome=outcome,
            duration_ms=max(0, round(duration_ms)),
            recorded_at=time.monotonic(),
        )
        with self._lock:
            self._samples.append(sample)
            cutoff = sample.recorded_at - _RETENTION_SECONDS
            while self._samples and self._samples[0].recorded_at < cutoff:
                self._samples.popleft()

    def snapshot(self) -> dict[tuple[str, str, str], list[int]]:
        """Nur für interne Diagnose und Tests; kein HTTP- oder WebSocket-Endpunkt."""
        with self._lock:
            grouped: dict[tuple[str, str, str], list[int]] = defaultdict(list)
            for sample in self._samples:
                grouped[(sample.component, sample.stage, sample.outcome)].append(sample.duration_ms)
            return dict(grouped)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


metrics = LatencyMetrics()


class measure:
    """Kleiner Kontextmanager für synchrone, externe Teilschritte."""

    def __init__(self, component: str, stage: str) -> None:
        self.component = component
        self.stage = stage
        self.started_at = 0.0
        self.outcome = "ok"

    def __enter__(self) -> "measure":
        self.started_at = time.perf_counter()
        return self

    def failed(self) -> None:
        self.outcome = "error"

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        if exc_type is not None:
            self.outcome = "error"
        metrics.record(
            self.component,
            self.stage,
            (time.perf_counter() - self.started_at) * 1000,
            self.outcome,
        )
        return False
