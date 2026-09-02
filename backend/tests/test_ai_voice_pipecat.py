"""Der Pipecat-Rand darf nur transportieren, nie Voice-Policy übernehmen."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from services.ai_voice.contracts import Lage
from services.ai_voice.pipecat_pipeline import PipecatVoicePipeline, pipecat_verfuegbar


@dataclass
class _Bridge:
    lage: Lage

    def __init__(self) -> None:
        self._lage = Lage()
        self._pipecat_ton_ausgeben = None
        self._pipecat_steuerung_ausgeben = None
        self._pipecat_ausgabe_unterbrechen = None
        self.eingang: list[bytes] = []
        self.ton: list[bytes] = []
        self.steuerung: list[dict] = []

    async def _ton(self, pcm: bytes) -> None:
        self.eingang.append(pcm)

    async def _ton_senden_direkt(self, pcm: bytes) -> None:
        self.ton.append(pcm)

    async def _senden_direkt(self, nutzlast: dict) -> None:
        self.steuerung.append(nutzlast)


@pytest.mark.asyncio
async def test_pipecat_transports_audio_and_safe_output_without_browser_contract_change() -> None:
    assert pipecat_verfuegbar() is True
    bridge = _Bridge()
    pipeline = PipecatVoicePipeline(bridge)  # type: ignore[arg-type]

    await pipeline._rahmen_einspeisen({"bytes": b"\x01\x00"})
    await bridge._pipecat_ton_ausgeben(b"\x02\x00")
    await bridge._pipecat_steuerung_ausgeben({"art": "zustand", "zustand": "bereit"})

    assert bridge.eingang == [b"\x01\x00"]
    assert bridge.ton == [b"\x02\x00"]
    assert bridge.steuerung == [{"art": "zustand", "zustand": "bereit"}]


def test_only_the_voice_pipeline_imports_pipecat() -> None:
    """Runner, LiveKit und Providerdienste dürfen keine Produktionsfläche erhalten."""
    backend = Path(__file__).resolve().parent.parent
    imports = []
    for path in (backend / "services").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "pipecat" in text and ("import pipecat" in text or "from pipecat" in text):
            imports.append(path.relative_to(backend).as_posix())

    assert imports == ["services/ai_voice/pipecat_pipeline.py"]
    source = (backend / imports[0]).read_text(encoding="utf-8")
    assert "LivekitFrameSerializer" not in source
    assert "workers.runner" not in source
    assert "services.openai" not in source
