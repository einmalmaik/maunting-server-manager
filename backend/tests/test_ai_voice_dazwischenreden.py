"""Das Tor fürs Dazwischenreden: Störgeräusche würgen keine Antwort ab.

Die alte Kante war 60 ms Rede — ein Türknall, ein Huster, eine fremde Stimme
im Hintergrund reichte, und die laufende Antwort war weg, während das Geräusch
selbst kurz darauf vom Huster-Filter verworfen wurde. Der Betreiber sass mit
Kopfhörern still da und wurde trotzdem „von sich selbst" unterbrochen.

Die Zusage seit dem Tor: eine laufende Antwort wird erst abgewürgt, wenn die
Störung dieselbe Messlatte reisst wie der Huster-Filter (`min_sekunden` laute
Rahmen) — wer wirklich dazwischenredet, kommt durch, nur um die Mindestrede
später. Ohne laufende Antwort bleibt die schnelle 60-ms-Kante, damit „hört zu"
sofort in der Blase steht.

Geprüft wird an der Brücke selbst, ohne Browser und ohne Anbieter, wie in
`test_ai_voice_zusteller.py`: die Töne sind Sinuswellen (stabiler
Effektivwert, kein flatterhafter Test über eine Schwelle).
"""

from __future__ import annotations

import asyncio
import math
import struct

import pytest

from services import ai_voice_bridge, ai_voice_vad


class _Attrappe(ai_voice_bridge.Sprachbruecke):
    """Eine Brücke ohne Browser: gesendete Ereignisse landen in einer Liste."""

    def __init__(self) -> None:  # noqa: D107 - siehe Klassendoku
        super().__init__(
            browser=None,  # type: ignore[arg-type]
            user_id=1,
            conversation_id="egal",
            chat_provider_id=1,
            stimm_kind="elevenlabs",
            stimm_adresse="wss://example.invalid/",
            stimm_schluessel="egal",
            http_client=None,  # type: ignore[arg-type]
        )
        self.ereignisse: list[dict] = []

    async def _senden(self, nutzlast: dict) -> None:
        self.ereignisse.append(nutzlast)

    def zustaende(self) -> list[str]:
        return [
            ereignis["zustand"]
            for ereignis in self.ereignisse
            if ereignis.get("art") == "zustand"
        ]


def _ton(sekunden: float, *, pegel: int, frequenz: float = 220.0) -> bytes:
    anzahl = int(sekunden * ai_voice_vad.ABTASTRATE)
    werte = (
        int(pegel * math.sin(2 * math.pi * frequenz * i / ai_voice_vad.ABTASTRATE))
        for i in range(anzahl)
    )
    return struct.pack(f"<{anzahl}h", *werte)


def _stille(sekunden: float) -> bytes:
    return b"\x00\x00" * int(sekunden * ai_voice_vad.ABTASTRATE)


@pytest.mark.asyncio
async def test_ein_kurzes_geraeusch_wuergt_die_antwort_nicht_ab() -> None:
    """Ein Geräusch unter der Mindestrede lässt die KI ausreden.

    Genau der gemeldete Fall: die KI spricht, im Raum knallt etwas oder
    jemand sagt zwei Silben — die alte Kante hätte sofort abgewürgt und die
    Silben danach selbst verworfen. Jetzt läuft die Antwort weiter, und
    weder „hört zu" noch „bereit" geht hinaus.
    """
    bruecke = _Attrappe()
    bruecke._zustand = ai_voice_bridge.ZUSTAND_SPRICHT
    laufende = asyncio.create_task(asyncio.sleep(60))
    bruecke._laufende = laufende

    await bruecke._ton(_stille(0.5))
    await bruecke._ton(_ton(0.12, pegel=9000))
    await bruecke._ton(_stille(1.0))

    assert not laufende.done()
    assert bruecke.zustaende() == []
    assert bruecke._zustand == ai_voice_bridge.ZUSTAND_SPRICHT

    laufende.cancel()
    try:
        await laufende
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_echtes_dazwischenreden_stoppt_nur_die_ausgabe() -> None:
    """Wer die Mindestrede erreicht, stoppt Audio, aber nicht den Tool-Run.

    Die Unterbrechung gilt global fuer Sprache, nicht als stiller Abbruch von
    Read-Tools. Ein ausdruecklicher ``abbrechen``-Rahmen ist der einzige Weg,
    der den Run selbst beendet.
    """
    bruecke = _Attrappe()
    bruecke._zustand = ai_voice_bridge.ZUSTAND_SPRICHT
    laufende = asyncio.create_task(asyncio.sleep(60))
    bruecke._laufende = laufende

    await bruecke._ton(_stille(0.5))
    await bruecke._ton(_ton(0.6, pegel=6000))

    assert not laufende.done()
    assert bruecke._laufende is laufende
    assert laufende in bruecke._unterdrueckte_laeufe
    assert bruecke.zustaende() == [ai_voice_bridge.ZUSTAND_HOERT]

    laufende.cancel()
    with pytest.raises(asyncio.CancelledError):
        await laufende


@pytest.mark.asyncio
async def test_expliziter_abbruch_beendet_den_lauf() -> None:
    """Nur der klare Abbruch darf aus einer Sprachunterbrechung einen Run-Abbruch machen."""
    bruecke = _Attrappe()
    laufende = asyncio.create_task(asyncio.sleep(60))
    bruecke._laufende = laufende

    await bruecke._rahmen({"text": '{"art":"abbrechen"}'})

    assert laufende.cancelled()
    assert bruecke._laufende is None


@pytest.mark.asyncio
async def test_ohne_laufende_antwort_bleibt_die_schnelle_kante() -> None:
    """Ohne Zug gibt es nichts zu schützen — „hört zu" kommt sofort.

    Die Mindestrede ist der Preis fürs Abwürgen, nicht fürs Zuhören: wer in
    der Stille anfängt zu sprechen, sieht die Blase sofort umschalten und
    nicht erst nach 0,35 Sekunden.
    """
    bruecke = _Attrappe()
    bruecke._zustand = ai_voice_bridge.ZUSTAND_BEREIT

    await bruecke._ton(_stille(0.5))
    await bruecke._ton(_ton(0.12, pegel=9000))

    assert bruecke.zustaende() == [ai_voice_bridge.ZUSTAND_HOERT]


@pytest.mark.asyncio
async def test_ein_verworfenes_geraeusch_laesst_die_anzeige_nicht_haengen() -> None:
    """Nach einem verworfenen Geräusch geht die Anzeige zurück auf „bereit".

    Die schnelle Kante meldet „hört zu", der Huster-Filter verwirft das Stück
    danach — ohne Rückmeldung bliebe die Anzeige stehen, und der Zusteller
    (der auf „bereit" wartet) käme nie mehr zu Wort.
    """
    bruecke = _Attrappe()
    bruecke._zustand = ai_voice_bridge.ZUSTAND_BEREIT

    await bruecke._ton(_stille(0.5))
    await bruecke._ton(_ton(0.12, pegel=9000))
    await bruecke._ton(_stille(2.0))

    assert bruecke.zustaende() == [
        ai_voice_bridge.ZUSTAND_HOERT,
        ai_voice_bridge.ZUSTAND_BEREIT,
    ]


@pytest.mark.asyncio
async def test_turn_merging_und_kadenz_anpassung() -> None:
    """Prüft, dass unterbrochene oder unfertige Äußerungen nahtlos verschmolzen werden."""
    bruecke = _Attrappe()
    bruecke._letzte_eingabe = "Würdest du bitte den Server neustarten"
    bruecke._letzte_eingabe_zeit = 100.0
    bruecke._letzte_antwort_fertig = False
    bruecke._unterbrochen_fuer_merge = True
    anfangs_kadenz = bruecke._kadenz_faktor

    # Mock für _abhoeren und _antworten
    async def mock_abhoeren(aeusserung):
        return "und um 15 Uhr das Backup machen."

    async def mock_antworten(text):
        pass

    bruecke._abhoeren = mock_abhoeren  # type: ignore[method-assign]
    bruecke._antworten = mock_antworten  # type: ignore[method-assign]

    fake_aeusserung = ai_voice_vad.Aeusserung(pcm=b"\x00\x00", sekunden=1.0)
    await bruecke._zug(fake_aeusserung)

    assert bruecke._letzte_eingabe == "Würdest du bitte den Server neustarten und um 15 Uhr das Backup machen."
    # Durch den Merge wurde die Kadenz erhöht (mehr Geduld für längere Pausen)
    assert bruecke._kadenz_faktor > anfangs_kadenz
    # Transkript-Ereignis wurde mit dem verschmolzenen Text gesendet
    gehoert_ereignisse = [e for e in bruecke.ereignisse if e.get("art") == "gehoert"]
    assert len(gehoert_ereignisse) == 1
    assert gehoert_ereignisse[0]["text"] == "Würdest du bitte den Server neustarten und um 15 Uhr das Backup machen."
