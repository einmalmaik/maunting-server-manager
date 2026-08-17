"""Die Stimme am Draht: was passiert, wenn die Gegenstelle auflegt.

ElevenLabs schliesst eine stille Sitzung nach ``inactivity_timeout`` von
selbst — und die Stimme öffnet beim **Laufstart**, nicht beim ersten Satz.
Dauert eine Werkzeugrunde länger als die Frist, ist die Verbindung tot, bevor
das erste Wort der restlichen Antwort hinausgeht. Bis zum 17.08.2026 wurde
daraus eine „stoerung": der Rest der Antwort ging verloren, obwohl an ihr
nichts falsch war.

Deshalb steht hier die Zusage: eine zugegangene Verbindung ist ein
Alltagsfall und wird **einmal transparent** repariert (`_neu_verbinden`) —
und nur sie. Ein abgelehnter Schlüssel, ein Protokollfehler, ein zweiter
Fehlschlag in Folge bleiben Fehler und fallen auf.

Dazu das Aufräumen am Anfang: scheitert schon der Eröffnungsgruss (oder wird
die Aufgabe zwischen Connect und Rückgabe abgebrochen), läuft ``__aexit__``
nie — die Verbindung muss dann von `_eroeffnen` selbst geschlossen werden,
sonst bleibt sie offen, bis die Gegenstelle sie irgendwann aufgibt.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from services import ai_tts_elevenlabs


class _Verbindung:
    """Eine Gegenstelle aus Attrappen: nimmt Rahmen an und kann sterben.

    ``tot`` stellt genau das nach, was ElevenLabs nach ``inactivity_timeout``
    tut: jeder weitere ``send`` scheitert mit `ConnectionClosed`. Der
    Empfänger (`__anext__`) bleibt still, bis er abgebrochen wird — wie eine
    Gegenstelle, die keinen Ton (mehr) schickt.
    """

    def __init__(self) -> None:
        self.rahmen: list[dict] = []
        self.geschlossen = False
        self.tot = False

    async def send(self, nachricht: str) -> None:
        if self.tot:
            raise ConnectionClosedOK(None, None)
        self.rahmen.append(json.loads(nachricht))

    async def close(self) -> None:
        self.geschlossen = True

    def __aiter__(self) -> "_Verbindung":
        return self

    async def __anext__(self) -> str:
        await asyncio.Event().wait()
        raise AssertionError("unerreichbar")  # pragma: no cover


async def _nichts(_pcm: bytes) -> None:
    """Ein Tonabnehmer, der nichts abnimmt — hier geht es um den Hinweg."""


def _saetze(verbindung: _Verbindung) -> list[str]:
    """Die gesprochenen Stücke einer Verbindung, ohne den Eröffnungsgruss."""
    return [
        rahmen["text"]
        for rahmen in verbindung.rahmen
        if rahmen.get("text", " ").strip()
    ]


@pytest.mark.asyncio
async def test_nach_einer_langen_werkzeugrunde_spricht_die_stimme_weiter(
    monkeypatch,
) -> None:
    """Der Kern von allem: die tote Verbindung wird repariert, nicht gemeldet.

    Erster Satz, dann eine Werkzeugrunde länger als `INAKTIVITAET_SEKUNDEN`
    (hier: die Gegenstelle legt auf), dann der zweite Satz. Vorher endete das
    in `ConnectionClosed` und damit in einer „stoerung" — der Mensch hörte den
    Anfang der Antwort und nie ihr Ergebnis.
    """
    verbindungen: list[_Verbindung] = []

    async def verbinden(_adresse: str, _schluessel: str) -> _Verbindung:
        verbindung = _Verbindung()
        verbindungen.append(verbindung)
        return verbindung

    monkeypatch.setattr(ai_tts_elevenlabs, "_verbinden", verbinden)

    async with ai_tts_elevenlabs.Stimme(
        adresse="wss://example.invalid/", schluessel="egal", senden=_nichts
    ) as stimme:
        await stimme.sagen("Der erste Satz ist lang genug zum Senden. ")
        # Die Werkzeugrunde: 40 Sekunden Textstille, die Gegenstelle legt auf.
        verbindungen[0].tot = True
        await stimme.sagen("Und der zweite Satz kommt trotzdem noch an. ")

    assert len(verbindungen) == 2, "Es haette genau eine neue Verbindung gebraucht"
    assert verbindungen[0].geschlossen is True
    # Der zweite Satz ging vollstaendig ueber die neue Verbindung — mitsamt
    # `flush`, damit er sofort erzeugt wird.
    assert any("zweite Satz" in satz for satz in _saetze(verbindungen[1]))
    assert all(
        rahmen.get("try_trigger_generation") is True
        for rahmen in verbindungen[1].rahmen
        if rahmen.get("text", " ").strip()
    )


@pytest.mark.asyncio
async def test_ein_vom_empfaenger_gemeldeter_abriss_ist_kein_fehler(
    monkeypatch,
) -> None:
    """Die Empfangsschleife merkt den Abriss zuerst und legt ihn ab.

    `_pruefe_fehler` warf ihn frueher beim naechsten `sagen()` — genau der
    Weg, auf dem aus dem eigenen `inactivity_timeout` eine „stoerung" wurde.
    Jetzt gilt: ein abgelegter Verbindungstod ist eine Anweisung zum
    Neuverbinden, kein Urteil ueber die Antwort.
    """
    verbindungen: list[_Verbindung] = []

    async def verbinden(_adresse: str, _schluessel: str) -> _Verbindung:
        verbindung = _Verbindung()
        verbindungen.append(verbindung)
        return verbindung

    monkeypatch.setattr(ai_tts_elevenlabs, "_verbinden", verbinden)

    async with ai_tts_elevenlabs.Stimme(
        adresse="wss://example.invalid/", schluessel="egal", senden=_nichts
    ) as stimme:
        verbindungen[0].tot = True
        stimme._fehler = ConnectionClosedOK(None, None)
        await stimme.sagen("Nach der Werkzeugrunde geht es hier weiter. ")

    assert len(verbindungen) == 2
    assert any("weiter" in satz for satz in _saetze(verbindungen[1]))


@pytest.mark.asyncio
async def test_ein_echter_fehler_faellt_weiterhin_auf(monkeypatch) -> None:
    """Die Nachsicht gilt dem Verbindungstod und sonst niemandem.

    Alles andere aus der Empfangsschleife — hier ein `RuntimeError` — muss
    beim naechsten `sagen()` geworfen werden wie bisher: es ist die einzige
    Stelle, an der eine Nebenaufgabe ueberhaupt gehoert wird.
    """
    async def verbinden(_adresse: str, _schluessel: str) -> _Verbindung:
        return _Verbindung()

    monkeypatch.setattr(ai_tts_elevenlabs, "_verbinden", verbinden)

    async with ai_tts_elevenlabs.Stimme(
        adresse="wss://example.invalid/", schluessel="egal", senden=_nichts
    ) as stimme:
        stimme._fehler = RuntimeError("kaputt")
        with pytest.raises(RuntimeError):
            await stimme.sagen("Egal was hier steht, es faellt vorher auf. ")


@pytest.mark.asyncio
async def test_scheitert_auch_die_neue_verbindung_wird_es_eine_stoerung(
    monkeypatch,
) -> None:
    """Genau **ein** zweiter Versuch — kein Kreisen gegen eine tote Gegenstelle.

    Schlaegt auch die neue Verbindung fehl, ist das keine Denkpause mehr,
    sondern eine echte Stoerung, und sie gehoert gemeldet. Und die halb
    eroeffnete zweite Verbindung darf dabei nicht offen liegenbleiben.
    """
    verbindungen: list[_Verbindung] = []

    async def verbinden(_adresse: str, _schluessel: str) -> _Verbindung:
        verbindung = _Verbindung()
        # Jede Verbindung nach der ersten ist sofort tot: der Gruss scheitert.
        verbindung.tot = bool(verbindungen)
        verbindungen.append(verbindung)
        return verbindung

    monkeypatch.setattr(ai_tts_elevenlabs, "_verbinden", verbinden)

    async with ai_tts_elevenlabs.Stimme(
        adresse="wss://example.invalid/", schluessel="egal", senden=_nichts
    ) as stimme:
        verbindungen[0].tot = True
        with pytest.raises(ConnectionClosed):
            await stimme.sagen("Der Satz, der die tote Leitung findet. ")

    assert len(verbindungen) == 2
    assert verbindungen[1].geschlossen is True


@pytest.mark.asyncio
async def test_ein_gescheiterter_eroeffnungsgruss_leckt_keine_verbindung(
    monkeypatch,
) -> None:
    """Scheitert `__aenter__`, laeuft `__aexit__` nie — aufraeumen muss er selbst.

    Ohne das bliebe die Verbindung offen, bis die Gegenstelle sie nach ihrem
    `inactivity_timeout` aufgibt — ein stiller Verbrauch fuer nichts.
    """
    verbindung = _Verbindung()
    verbindung.tot = True

    async def verbinden(_adresse: str, _schluessel: str) -> _Verbindung:
        return verbindung

    monkeypatch.setattr(ai_tts_elevenlabs, "_verbinden", verbinden)

    with pytest.raises(ConnectionClosed):
        async with ai_tts_elevenlabs.Stimme(
            adresse="wss://example.invalid/", schluessel="egal", senden=_nichts
        ):
            raise AssertionError("unerreichbar")  # pragma: no cover

    assert verbindung.geschlossen is True


@pytest.mark.asyncio
async def test_ein_abbruch_zwischen_connect_und_rueckgabe_leckt_auch_nicht(
    monkeypatch,
) -> None:
    """`CancelledError` erbt nicht von `Exception` — deshalb `BaseException`.

    Der Fall aus dem Betrieb: der Mensch redet dazwischen, `_abwuergen`
    bricht den Zug ab, und der Abbruch trifft die Stimme genau zwischen
    Handschlag und Eroeffnungsgruss. Ein blosses ``except Exception`` liesse
    die frische Verbindung offen zurueck.
    """
    class _AbgebrochenBeimGruss(_Verbindung):
        async def send(self, nachricht: str) -> None:
            raise asyncio.CancelledError()

    verbindung = _AbgebrochenBeimGruss()

    async def verbinden(_adresse: str, _schluessel: str) -> _Verbindung:
        return verbindung

    monkeypatch.setattr(ai_tts_elevenlabs, "_verbinden", verbinden)

    stimme = ai_tts_elevenlabs.Stimme(
        adresse="wss://example.invalid/", schluessel="egal", senden=_nichts
    )
    with pytest.raises(asyncio.CancelledError):
        await stimme.__aenter__()

    assert verbindung.geschlossen is True
