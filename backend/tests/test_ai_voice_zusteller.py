"""Der Zusteller der Sprachsitzung: Worker-Meldungen als gesprochener Zwischenruf.

docs/agentic-framework.md (§4, §12): Im Sprachmodus ersetzt der VAD-Zustand
„bereit" die Chat-Ruhe der Meldestelle. Drei Zusagen, jede als eigener Test:

1. **Die Ruhe der Stimme hat vier Bedingungen**, und jede einzelne hält den
   Zusteller auf — sonst redete die KI dem Menschen ins Wort oder einem
   offenen Vorschlag dazwischen (das nächste „Ja" bestätigte dann etwas
   anderes, als der Mensch meint).
2. **Gesprochen wird die Lieferung des Gehirns**, nicht der rohe
   Meldungstext — dieselbe Nachricht, die auch im Chat steht, kein zweiter
   Wortlaut, keine Phrase. Und wenn es nichts zu liefern gibt, hängt der
   Zustand nicht auf „denkt".
3. **Die Lieferung liegt in derselben Hand wie ein gewöhnlicher Zug**
   (`self._laufende`) — nur dieses Feld cancelt `_abwuergen`, und nur so
   bricht Dazwischenreden auch eine laufende Zustellansage ab.

Geprüft wird an der Brücke selbst, ohne Browser und ohne Anbieter, wie in
`test_ai_voice_bestaetigung.py`: der Lauf kommt über den echten
`ai_run_broker`, die Stimme ist eine stumme Attrappe.
"""

from __future__ import annotations

import asyncio
import contextlib
from uuid import uuid4

import pytest

from services import ai_meldestelle, ai_run_broker, ai_tts_elevenlabs, ai_voice_bridge


class _Attrappe(ai_voice_bridge.Sprachbruecke):
    """Eine Brücke ohne Browser: gesendete Ereignisse landen in einer Liste."""

    def __init__(self, user_id: int = 1) -> None:  # noqa: D107 - siehe Klassendoku
        super().__init__(
            browser=None,  # type: ignore[arg-type]
            user_id=user_id,
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


class _StummeStimme:
    """Eine Stimme, die nur mitschreibt — der Draht ist hier nicht die Frage."""

    letzte: "_StummeStimme | None" = None

    def __init__(self, **_unbenutzt) -> None:
        self.saetze: list[str] = []
        _StummeStimme.letzte = self

    async def __aenter__(self) -> "_StummeStimme":
        return self

    async def __aexit__(self, *_ausnahme) -> None:
        return None

    async def sagen(self, text: str) -> None:
        self.saetze.append(text)

    async def ausklingen(self) -> None:
        return None

    async def schliessen(self) -> None:
        return None


@pytest.mark.asyncio
async def test_die_ruhe_der_stimme_braucht_alle_vier_bedingungen() -> None:
    """Jede der vier Bedingungen hält den Zusteller allein auf.

    Der Zustand muss „bereit" sein, der Mensch darf nicht gerade sprechen
    (die Mikrolücke zwischen VAD-Flanke und Zustandsmeldung), kein Zug darf
    laufen, und kein Vorschlag darf offen sein — ein Zwischenruf zwischen
    Vorschlag und gesprochenem Ja, und das nächste „Ja" gälte etwas anderem,
    als der Mensch meint.
    """
    bruecke = _Attrappe()
    bruecke._zustand = ai_voice_bridge.ZUSTAND_BEREIT
    assert bruecke._ruhe() is True

    bruecke._zustand = ai_voice_bridge.ZUSTAND_DENKT
    assert bruecke._ruhe() is False
    bruecke._zustand = ai_voice_bridge.ZUSTAND_BEREIT

    bruecke._erkennung._spricht = True
    assert bruecke._ruhe() is False
    bruecke._erkennung._spricht = False

    laufende = asyncio.create_task(asyncio.sleep(60))
    bruecke._laufende = laufende
    assert bruecke._ruhe() is False
    laufende.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await laufende
    # Eine fertige Aufgabe ist keine Beschäftigung mehr: das Feld wird nach
    # einem Zug nicht geleert, nur abgeschlossen.
    assert bruecke._ruhe() is True

    bruecke._offene_vorschlaege = ["vorschlag-1"]
    assert bruecke._ruhe() is False


@pytest.mark.asyncio
async def test_der_zusteller_spricht_die_lieferung_des_gehirns(
    db, owner_user, monkeypatch
) -> None:
    """Ein Lieferzug: die Meldestelle baut den Gehirn-Lauf, die Brücke spricht ihn.

    Gesprochen wird nicht der Meldungstext, sondern was der Gehirn-Lauf
    daraus macht — derselbe Weg wie nach einer Frage: abonnieren,
    `_lauf_verfolgen`, und am Ende ist der Zustand wieder „bereit". Die
    Brücke gibt dabei ``ruhe_noetig=False`` mit: die Chat-Karenz würde eine
    offene Sprachsitzung fälschlich blockieren, ihr Ruhe-Prädikat ist
    `_ruhe()` und wurde vom Zusteller geprüft.
    """
    monkeypatch.setattr(ai_tts_elevenlabs, "Stimme", _StummeStimme)
    _StummeStimme.letzte = None
    bruecke = _Attrappe(user_id=owner_user.id)

    lauf_id = str(uuid4())
    ai_run_broker.eroeffnen(lauf_id)

    class _Lauf:
        id = lauf_id

    gesehen: dict[str, object] = {}

    async def _zustellung(db_, *, user, ruhe_noetig=True):
        gesehen["ruhe_noetig"] = ruhe_noetig
        gesehen["user_id"] = user.id
        return _Lauf()

    monkeypatch.setattr(ai_meldestelle, "zustellung_anstossen", _zustellung)

    async def lauf_spielt() -> None:
        # Erst nachdem die Brücke abonniert hat, wie im Betrieb: der
        # Lieferlauf braucht eine Anbieterrunde, bevor das erste Zeichen kommt.
        await asyncio.sleep(0.05)
        ai_run_broker.veroeffentlichen(
            lauf_id, "delta", {"content": "Dein Backup-Auftrag ist erledigt.\n"}
        )
        ai_run_broker.veroeffentlichen(
            lauf_id, "run", {"run_id": lauf_id, "status": "completed"}
        )
        ai_run_broker.beenden(lauf_id)

    einspieler = asyncio.create_task(lauf_spielt())
    await bruecke._meldung_liefern()
    await einspieler

    assert gesehen["ruhe_noetig"] is False
    assert gesehen["user_id"] == owner_user.id
    assert _StummeStimme.letzte is not None
    assert any("erledigt" in satz for satz in _StummeStimme.letzte.saetze)
    assert ai_voice_bridge.ZUSTAND_DENKT in bruecke.zustaende()
    assert bruecke.zustaende()[-1] == ai_voice_bridge.ZUSTAND_BEREIT


@pytest.mark.asyncio
async def test_eine_leere_zustellung_laesst_den_zustand_nicht_haengen(
    db, owner_user, monkeypatch
) -> None:
    """``None`` von der Meldestelle heisst: nichts zu liefern — zurück auf „bereit".

    Zwischen dem Blick des Zustellers und dem Lieferzug kann ein anderer
    Kanal die Meldung geholt haben (der Chat-Takt läuft parallel weiter).
    Ohne den Rückweg zeigte der Browser für den Rest der Sitzung eine KI,
    die angeblich arbeitet.
    """

    async def _nichts(db_, *, user, ruhe_noetig=True):
        return None

    monkeypatch.setattr(ai_meldestelle, "zustellung_anstossen", _nichts)
    bruecke = _Attrappe(user_id=owner_user.id)

    await bruecke._meldung_liefern()

    assert bruecke.zustaende()[-1] == ai_voice_bridge.ZUSTAND_BEREIT


@pytest.mark.asyncio
async def test_die_lieferung_liegt_in_derselben_hand_wie_ein_zug(
    monkeypatch,
) -> None:
    """Der Zusteller legt die Lieferung in `self._laufende` — die Hand des Barge-in.

    `_abwuergen` cancelt genau dieses eine Feld. Läge die Lieferung daneben,
    spräche die Ansage weiter, während der Mensch längst dazwischengeredet
    hat (§4: Barge-in bricht auch Zwischenmeldungen ab).
    """
    monkeypatch.setattr(ai_voice_bridge, "ZUSTELL_TAKT_S", 0.01)
    bruecke = _Attrappe()
    bruecke._zustand = ai_voice_bridge.ZUSTAND_BEREIT
    geliefert = asyncio.Event()

    async def _liefern() -> None:
        assert bruecke._laufende is asyncio.current_task()
        geliefert.set()

    monkeypatch.setattr(bruecke, "_meldungen_offen", lambda: True)
    monkeypatch.setattr(bruecke, "_meldung_liefern", _liefern)

    zusteller = asyncio.create_task(bruecke._meldungen_zustellen())
    try:
        await asyncio.wait_for(geliefert.wait(), timeout=2.0)
    finally:
        # Der Cancel trifft absichtlich genau in das Fenster nach der
        # Lieferung (`await aufgabe` im Zusteller): wer ihn dort verschluckt,
        # macht die Schleife zum Zombie — im Betrieb hinge dann `fuehren()`
        # beim Aufräumen für immer. Deshalb ein Zeitlimit statt blindem await.
        zusteller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(zusteller, timeout=2.0)
