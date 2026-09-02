"""Sitzungsgebundenes, ausschließlich lesendes Intent-Prefetching."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from database import SessionLocal
from models import AiRun, User


class VoicePrefetch:
    """Hält Revision und Cachebindung einer Voice-Sitzung zusammen."""

    def __init__(
        self,
        *,
        user_id: int,
        herkunft: str,
        familie: str | None,
        senden: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._user_id = user_id
        self._herkunft = herkunft
        self._familie = familie
        self._senden = senden
        self.session_id = str(uuid4())
        self.revision = 0

    def invalidieren(self) -> None:
        from services.ai_intent_classifier import prefetch_cache

        prefetch_cache.invalidate(session_id=self.session_id, user_id=self._user_id)

    async def verarbeite(self, text_chunk: str) -> None:
        from services.ai_intent_classifier import (
            classify_streaming_intent,
            is_side_effect_free,
            prefetch_cache,
        )
        from services.ai_latency_metrics import metrics

        started_at = time.perf_counter()
        prediction = classify_streaming_intent(text_chunk)
        metrics.record(
            "voice",
            "intent_classification",
            (time.perf_counter() - started_at) * 1000,
            "matched" if prediction is not None else "no_match",
        )
        if prediction is None:
            return
        self.revision += 1
        revision = self.revision
        self.invalidieren()
        nachricht = {
            "art": "intent_erkannt",
            "intent": prediction.intent,
            "confidence": prediction.confidence,
            "entities": prediction.entities,
            "arguments": prediction.arguments,
            "spekulativ": bool(prediction.arguments),
            "prefetch_status": "erkannt",
            "revision": revision,
        }
        await self._senden(nachricht)
        if not prediction.arguments or not is_side_effect_free(prediction.intent):
            return
        task = await prefetch_cache.prefetch(
            session_id=self.session_id,
            user_id=self._user_id,
            tool_name=prediction.intent,
            arguments=prediction.arguments,
            executor=lambda: asyncio.to_thread(
                self._lesen, prediction.intent, prediction.arguments
            ),
        )
        if task is not None:
            await self._senden({**nachricht, "prefetch_status": "gestartet"})
            asyncio.create_task(self._beobachten(task, prediction, revision))
            if prediction.intent == "analyze_region":
                asyncio.create_task(self._geo_ziel_senden(prediction, revision))

    def _lesen(self, tool_name: str, arguments: dict) -> dict:
        from services.ai_action_service import execute_read_tool

        with SessionLocal() as db:
            user = db.get(User, self._user_id)
            if user is None or not user.is_active:
                raise RuntimeError("user unavailable")
            return execute_read_tool(
                db,
                user=user,
                tool_name=tool_name,
                arguments=arguments,
                herkunft=self._herkunft,
                familie=self._familie,
            )

    async def _beobachten(self, task: asyncio.Task, prediction, revision: int) -> None:
        value = None
        try:
            value = await task
            status = "fertig" if value is not None else "fehler"
        except asyncio.CancelledError:
            status = "abgebrochen"
        except Exception:
            status = "fehler"
        if revision != self.revision:
            return
        nachricht = {
            "art": "intent_erkannt",
            "intent": prediction.intent,
            "confidence": prediction.confidence,
            "entities": prediction.entities,
            "arguments": prediction.arguments,
            "spekulativ": True,
            "prefetch_status": status,
            "revision": revision,
        }
        # Das Ergebnis ist bereits unter der Benutzeridentität ausgeführt und
        # auf den engen Geo-Vertrag begrenzt. Es direkt zu zeigen spart die
        # zweite Modellrunde für die Karte; gesprochen wird weiterhin nur die
        # eigentliche Modellantwort.
        if (
            status == "fertig"
            and prediction.intent == "analyze_region"
            and isinstance(value, dict)
        ):
            nachricht["geo_analysis"] = value
        await self._senden(nachricht)

    async def _geo_ziel_senden(self, prediction, revision: int) -> None:
        from services import ai_geo_service

        location = str(prediction.arguments["location"])
        geo = await asyncio.to_thread(ai_geo_service.geocode_location, location)
        if not geo or revision != self.revision:
            return
        await self._senden({
            "art": "intent_erkannt",
            "intent": prediction.intent,
            "confidence": prediction.confidence,
            "entities": prediction.entities,
            "arguments": prediction.arguments,
            "spekulativ": True,
            "prefetch_status": "gestartet",
            "revision": revision,
            "geo_target": {
                "location": geo["name"],
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "bbox": geo["bbox"],
            },
        })

    def an_lauf_binden(self, run_id: str) -> None:
        from services import ai_run_service

        with SessionLocal() as db:
            run = db.get(AiRun, run_id)
            if run is None:
                return
            zustand = ai_run_service.zustand_lesen(run)
            zustand["prefetch_session_id"] = self.session_id
            ai_run_service.zustand_schreiben(run, zustand)
            db.commit()
