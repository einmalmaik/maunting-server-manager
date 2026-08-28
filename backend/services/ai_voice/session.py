"""WebSocket-Sitzungsschleife des Sprachmodus.

Die Klasse benutzt nur die schmale Oberfläche der kompatiblen Brücke. Damit
bleiben Transport und Session-Orchestrierung getrennt, ohne eine hypothetische
zweite Pipeline oder ein Framework einzuführen.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from starlette.websockets import WebSocketDisconnect

from services.ai_voice.contracts import (
    MAX_STEUERRAHMEN_ZEICHEN,
    MAX_TONRAHMEN_BYTES,
    ZUSTAND_BEREIT,
)


class VoiceSession:
    """Besitzt die langlebige Empfangsschleife einer bestehenden Brücke."""

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge

    async def fuehren(self):
        from services.ai_intent_classifier import classifier

        asyncio.create_task(asyncio.to_thread(classifier.warm))
        await self._bridge._zustand_melden(ZUSTAND_BEREIT, erstmalig=True)
        self._bridge._zusteller = asyncio.create_task(self._bridge._meldungen_zustellen())
        ende = time.monotonic() + self._bridge._hoechstdauer
        try:
            while True:
                rest = ende - time.monotonic()
                if rest <= 0:
                    self._bridge._lage.abgelaufen = True
                    await self._bridge._senden({"art": "abgelaufen"})
                    break
                try:
                    nachricht = await asyncio.wait_for(self._bridge._browser.receive(), timeout=rest)
                except asyncio.TimeoutError:
                    continue
                if nachricht.get("type") == "websocket.disconnect":
                    break
                if not await self.rahmen(nachricht):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            zusteller = self._bridge._zusteller
            self._bridge._zusteller = None
            if zusteller is not None:
                zusteller.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await zusteller
            # Ein Socket-Ende ist kein Benutzer-Abort. Die Ausgabe wird
            # bereinigt, der zentrale Run arbeitet kontrolliert weiter.
            await self._bridge._abwuergen()
        return self._bridge._lage

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
