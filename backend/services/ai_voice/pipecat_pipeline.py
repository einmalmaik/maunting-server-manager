"""Pipecat-Transport für den bestehenden MSM-Sprachmodus.

Dieses Modul ist die einzige Produktivstelle mit Pipecat-Imports. Es übersetzt
den bereits authentifizierten Starlette-WebSocket in interne Audio- und
Steuerframes. Fachlogik bleibt in ``Sprachbruecke`` und ihren bestehenden,
kleinen Diensten: VAD, Abschrift, AiRun, Broker, TTS, Berechtigungen und
Vorschläge werden hier weder ersetzt noch erneut implementiert.

Kein Pipecat-Runner, kein LiveKit-Serializer und kein Pipecat-Providerdienst
werden verwendet. Ein fehlendes Paket macht ausschließlich den Sprachmodus
unverfügbar; das Panel selbst kann weiterhin starten.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from starlette.websockets import WebSocketDisconnect

from services.ai_voice.contracts import (
    MAX_STEUERRAHMEN_ZEICHEN,
    MAX_TONRAHMEN_BYTES,
    ZUSTAND_BEREIT,
)
from services.ai_voice_vad import ABTASTRATE
from services.ai_latency_metrics import metrics

if TYPE_CHECKING:
    from services.ai_voice_bridge import Sprachbruecke


_PIPECAT_VERSION = "1.8.1"


class PipecatNichtVerfuegbar(RuntimeError):
    """Die optionale Voice-Orchestrierung kann nicht sicher gestartet werden."""


def pipecat_verfuegbar() -> bool:
    """Prüft Version und Import, ohne einen Runner oder Provider zu starten."""

    try:
        if importlib.metadata.version("pipecat-ai") != _PIPECAT_VERSION:
            return False
        from pipecat.frames.frames import InputAudioRawFrame  # noqa: F401
        from pipecat.pipeline.pipeline import Pipeline  # noqa: F401
        from pipecat.processors.frame_processor import FrameProcessor  # noqa: F401
    except (ImportError, importlib.metadata.PackageNotFoundError):
        return False
    return True


if pipecat_verfuegbar():
    from pipecat.frames.frames import (
        EndFrame,
        Frame,
        InputAudioRawFrame,
        InterruptionFrame,
        TTSAudioRawFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    @dataclass
    class _SteuerFrame(Frame):
        """Ein unveränderter, begrenzter Browser-Steuerrahmen innerhalb Pipecats."""

        text: str


    @dataclass
    class _AusgabeFrame(Frame):
        """Bereits bereinigte Voice-UI-Nutzlast für den bestehenden Browservertrag."""

        nutzlast: dict[str, Any]


    class _MsmVoiceProcessor(FrameProcessor):
        """Verbindet Pipecat-Frames mit der vorhandenen MSM-Voice-Fachlogik."""

        def __init__(self, bridge: "Sprachbruecke") -> None:
            super().__init__(name="msm-voice", enable_direct_mode=True)
            self._bridge = bridge

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                gestartet = time.perf_counter()
                await self._bridge._ton(frame.audio)
                metrics.record("ai_voice_pipecat", "input_frame", (time.perf_counter() - gestartet) * 1000)
                return
            if isinstance(frame, _SteuerFrame):
                # Die bestehende, begrenzte Steuerframe-Validierung bleibt die
                # einzige Wahrheit für Unterbrechen, Abort und Teiltranskripte.
                from services.ai_voice.session import VoiceSession

                await VoiceSession(self._bridge).rahmen({"text": frame.text})
                return
            await self.push_frame(frame, direction)

        async def ton_ausgeben(self, pcm: bytes) -> None:
            await self.push_frame(
                TTSAudioRawFrame(audio=pcm, sample_rate=ABTASTRATE, num_channels=1)
            )

        async def steuerung_ausgeben(self, nutzlast: dict[str, Any]) -> None:
            await self.push_frame(_AusgabeFrame(nutzlast=nutzlast))


    class _WebSocketOutputProcessor(FrameProcessor):
        """Der alleinige Pipecat-zu-WebSocket-Ausgang; keine Rohdatenprotokolle."""

        def __init__(self, bridge: "Sprachbruecke") -> None:
            super().__init__(name="msm-websocket-output", enable_direct_mode=True)
            self._bridge = bridge

        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSAudioRawFrame):
                gestartet = time.perf_counter()
                await self._bridge._ton_senden_direkt(frame.audio)
                metrics.record("ai_voice_pipecat", "output_audio_frame", (time.perf_counter() - gestartet) * 1000)
                return
            if isinstance(frame, _AusgabeFrame):
                gestartet = time.perf_counter()
                await self._bridge._senden_direkt(frame.nutzlast)
                metrics.record("ai_voice_pipecat", "output_control_frame", (time.perf_counter() - gestartet) * 1000)
                return
            await self.push_frame(frame, direction)


    class PipecatVoicePipeline:
        """Besitzt genau eine Pipecat-Pipeline pro bereits geprüfter Voice-Sitzung."""

        def __init__(self, bridge: "Sprachbruecke") -> None:
            self._bridge = bridge
            self._voice = _MsmVoiceProcessor(bridge)
            self._output = _WebSocketOutputProcessor(bridge)
            # Direkte Processor-Ausführung ist bewusst: Starlette besitzt den
            # Socket bereits; ein Pipecat-Runner oder zweiter Transport würde
            # eine weitere Listener-/Lifecycle-Schicht schaffen.
            self._pipeline = Pipeline([self._voice, self._output])
            bridge._pipecat_ton_ausgeben = self._voice.ton_ausgeben
            bridge._pipecat_steuerung_ausgeben = self._voice.steuerung_ausgeben
            bridge._pipecat_ausgabe_unterbrechen = self._unterbrechen

        async def _unterbrechen(self) -> None:
            """Leert Pipecat-Ausgabe, ohne MSM-AiRuns zu canceln."""

            await self._pipeline.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)

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
                    await self._rahmen_einspeisen(nachricht)
            except WebSocketDisconnect:
                pass
            finally:
                zusteller = self._bridge._zusteller
                self._bridge._zusteller = None
                if zusteller is not None:
                    zusteller.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await zusteller
                # Socket-Ende bleibt Output-Cleanup und kein Run-Abbruch.
                await self._bridge._abwuergen()
                with contextlib.suppress(Exception):
                    await self._pipeline.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                self._bridge._pipecat_ton_ausgeben = None
                self._bridge._pipecat_steuerung_ausgeben = None
                self._bridge._pipecat_ausgabe_unterbrechen = None
            return self._bridge._lage

        async def _rahmen_einspeisen(self, nachricht: dict[str, Any]) -> None:
            roh = nachricht.get("bytes")
            if roh is not None:
                if len(roh) <= MAX_TONRAHMEN_BYTES:
                    self._bridge._lage.rahmen_hin += 1
                    await self._pipeline.process_frame(
                        InputAudioRawFrame(audio=roh, sample_rate=ABTASTRATE, num_channels=1),
                        FrameDirection.DOWNSTREAM,
                    )
                return
            text = nachricht.get("text")
            if text is None or len(text) > MAX_STEUERRAHMEN_ZEICHEN:
                return
            # JSON wird hier nur auf seine Grundform geprüft. Die semantische
            # Verarbeitung bleibt beim vorhandenen, zentral getesteten Handler.
            try:
                befehl = json.loads(text)
            except (TypeError, ValueError):
                return
            if isinstance(befehl, dict):
                await self._pipeline.process_frame(_SteuerFrame(text=text), FrameDirection.DOWNSTREAM)


else:

    class PipecatVoicePipeline:
        def __init__(self, _bridge: "Sprachbruecke") -> None:
            raise PipecatNichtVerfuegbar("Pipecat 1.8.1 ist nicht verfügbar")

        async def fuehren(self):  # pragma: no cover - Konstruktion schlägt bereits fehl
            raise PipecatNichtVerfuegbar("Pipecat 1.8.1 ist nicht verfügbar")
