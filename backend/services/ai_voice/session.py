"""Steuerframe-Validierung des Sprachmodus.

Der produktive WebSocket-Transport läuft über ``pipecat_pipeline``. Diese
Klasse behält nur den kleinen, zentral getesteten Steuerframe-Vertrag, damit
Unterbrechen, Abort und Teiltranskripte nicht doppelt implementiert werden.
"""

from __future__ import annotations

import json
from typing import Any

from services.ai_voice.contracts import (
    MAX_STEUERRAHMEN_ZEICHEN,
    MAX_TONRAHMEN_BYTES,
    ZUSTAND_BEREIT,
)


class VoiceSession:
    """Besitzt den kompatiblen, begrenzten Steuerframe-Handler."""

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge

    async def fuehren(self):
        from services.ai_voice.pipecat_pipeline import PipecatVoicePipeline

        return await PipecatVoicePipeline(self._bridge).fuehren()

    async def rahmen(self, nachricht: dict) -> bool:
        roh = nachricht.get("bytes")
        if roh is not None:
            if len(roh) <= MAX_TONRAHMEN_BYTES:
                self._bridge._lage.rahmen_hin += 1
                await self._bridge._ton(roh)
            return True
        text = nachricht.get("text")
        if text is None or len(text) > MAX_STEUERRAHMEN_ZEICHEN:
            return True
        try:
            befehl = json.loads(text)
        except (TypeError, ValueError):
            return True
        if not isinstance(befehl, dict):
            return True
        art = befehl.get("art")
        if art == "unterbrechen":
            await self._bridge._ausgabe_unterbrechen()
            await self._bridge._zustand_melden(ZUSTAND_BEREIT)
        elif art in ("abbrechen", "abort"):
            await self._bridge._abwuergen(runs_abbrechen=True)
            await self._bridge._zustand_melden(ZUSTAND_BEREIT)
        elif art in ("teil_transkript", "partial_transcript", "transkript_chunk", "chunk"):
            text_chunk = str(befehl.get("text") or befehl.get("chunk") or "").strip()
            if text_chunk:
                await self._bridge._verarbeite_teil_transkript(text_chunk)
        return True
