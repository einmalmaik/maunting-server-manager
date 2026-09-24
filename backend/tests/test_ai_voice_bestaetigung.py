"""Nach dem gesprochenen Ja: wem das Ja gilt, und wer danach zuhört.

Zwei Zusagen, und beide sind am 17.08.2026 gebrochen gewesen:

1. **Das Ergebnis ist hörbar.** Die Bestätigung weckt den geparkten Lauf über
   `lauf_fortsetzen` — aber wer sich danach nicht anhängt, hört nichts: das
   Ergebnis blieb stumm, und der Zustand hing für den Rest der Sitzung auf
   „denkt". Jetzt geht die Brücke nach dem Ja denselben Weg wie nach einer
   Frage: abonnieren und `_lauf_verfolgen` (`_fortsetzung_verfolgen`).
2. **Ein Ja meint einen Vorschlag.** Der Browser zeigt genau eine Karte —
   die zuletzt geschickte. Ein Ja, das **alle** offenen Vorschläge des Zuges
   ausführt, führte Dinge aus, die der Mensch nie gesehen hat. Bestätigt wird
   deshalb nur der zuletzt gemerkte; die übrigen werden vergessen wie beim
   Nein und dem Menschen angesagt.

Geprüft wird an der Brücke selbst, ohne Browser und ohne Anbieter: der Lauf
wird über den echten `ai_run_broker` eingespielt, die Stimme ist eine stumme
Attrappe. Genau die Naht zwischen „bestätigt" und „gehört" ist es, die sonst
nirgends unter Test steht.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from services import ai_run_broker, ai_tts_elevenlabs, ai_voice_bridge
from services.ai_voice.interactions import Ausgang


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
        #: Was `_vorschlagslage` meldet: (schon entschieden, braucht Klick).
        self.lage: tuple[str | None, bool] = (None, False)

    async def _senden(self, nutzlast: dict) -> None:
        self.ereignisse.append(nutzlast)

    def _vorschlagslage(self, kennung: str) -> tuple[str | None, bool]:
        return self.lage

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
async def test_nach_dem_ja_wird_der_geweckte_lauf_verfolgt_und_gesprochen(
    monkeypatch,
) -> None:
    """Die Naht selbst: Ja → ausführen → **zuhören** → bereit.

    Ohne das Zuhören meldete `_entscheidung` „denkt" und kehrte zurück — der
    fortgesetzte Lauf sprach ins Leere, und die Sitzung sah für immer
    beschäftigt aus. Der Mensch hatte gerade etwas erlaubt und erfuhr nie,
    wie es ausging.
    """
    monkeypatch.setattr(ai_tts_elevenlabs, "Stimme", _StummeStimme)
    bruecke = _Attrappe()
    bruecke._offene_vorschlaege = ["vorschlag-1"]

    lauf_id = str(uuid4())
    ai_run_broker.eroeffnen(lauf_id)

    def ausfuehren(kennung: str) -> Ausgang:
        return Ausgang(erledigt=True, lauf_id=lauf_id)

    bruecke._ausfuehren = ausfuehren  # type: ignore[method-assign]

    async def lauf_spielt() -> None:
        # Erst nachdem die Bruecke abonniert hat, wie im Betrieb: der geweckte
        # Lauf braucht eine Anbieterrunde, bevor das erste Zeichen kommt.
        await asyncio.sleep(0.05)
        ai_run_broker.veroeffentlichen(
            lauf_id, "delta", {"content": "Der Server wurde neu gestartet.\n"}
        )
        ai_run_broker.veroeffentlichen(
            lauf_id, "run", {"run_id": lauf_id, "status": "completed"}
        )
        ai_run_broker.beenden(lauf_id)

    einspieler = asyncio.create_task(lauf_spielt())
    entschieden = await bruecke._entscheidung("Ja")
    await einspieler

    assert entschieden is True
    assert _StummeStimme.letzte is not None
    assert any("neu gestartet" in satz for satz in _StummeStimme.letzte.saetze)
    # Und der Zustand kommt zurueck: erst denkt, am Ende bereit — nicht
    # haengend auf „denkt".
    assert ai_voice_bridge.ZUSTAND_DENKT in bruecke.zustaende()
    assert bruecke.zustaende()[-1] == ai_voice_bridge.ZUSTAND_BEREIT


@pytest.mark.asyncio
async def test_ein_ja_ohne_weckbaren_lauf_laesst_den_zustand_nicht_haengen() -> None:
    """Auch ohne Fortsetzung muss „denkt" wieder enden.

    Ein Vorschlag ohne Lauf (oder eine Fortsetzung, die nicht zustande kam)
    hat nichts zu verfolgen — aber der Browser zeigte sonst fuer den Rest der
    Sitzung eine KI, die angeblich arbeitet.
    """
    bruecke = _Attrappe()
    bruecke._offene_vorschlaege = ["vorschlag-1"]
    bruecke._ausfuehren = lambda kennung: Ausgang(erledigt=True)  # type: ignore[method-assign]

    entschieden = await bruecke._entscheidung("Ja")

    assert entschieden is True
    assert bruecke.zustaende()[-1] == ai_voice_bridge.ZUSTAND_BEREIT
    # Ein einzelner Vorschlag: nichts wurde verworfen, also wird auch nichts
    # angesagt.
    assert not any(
        "verworfen" in str(ereignis.get("text", ""))
        for ereignis in bruecke.ereignisse
    )


@pytest.mark.asyncio
async def test_ein_ja_bestaetigt_nur_den_zuletzt_gezeigten_vorschlag() -> None:
    """Der Browser zeigt eine Karte — das Ja gilt genau ihr.

    Haette der Zug drei Vorschlaege gemacht, sah der Mensch nur den letzten
    (`useSprachsitzung.ts` haelt genau einen `vorschlag`). Alle drei
    auszufuehren hiesse, zwei ungesehene Aktionen mit einer Silbe zu
    genehmigen. Die uebrigen verhalten sich wie beim Nein: vergessen, in der
    Datenbank weiter ausfuehrbar bis zur Frist — und der Mensch erfaehrt es.
    """
    bruecke = _Attrappe()
    bruecke._offene_vorschlaege = ["alt-1", "alt-2", "zuletzt-gezeigt"]
    bestaetigt: list[str] = []

    def ausfuehren(kennung: str) -> Ausgang:
        bestaetigt.append(kennung)
        return Ausgang(erledigt=True)

    bruecke._ausfuehren = ausfuehren  # type: ignore[method-assign]

    entschieden = await bruecke._entscheidung("Ja")

    assert entschieden is True
    assert bestaetigt == ["zuletzt-gezeigt"]
    assert bruecke._offene_vorschlaege == []
    # Die Ansage: zwei Vorschlaege wurden verworfen, und das steht sichtbar
    # im Antworttext statt stillschweigend nirgends.
    ansagen = [
        str(ereignis.get("text", ""))
        for ereignis in bruecke.ereignisse
        if ereignis.get("art") == "antworttext"
    ]
    assert any("2" in ansage and "verworfen" in ansage for ansage in ansagen)


@pytest.mark.asyncio
async def test_ein_ja_zu_einem_loeschvorgang_zeigt_auf_den_knopf() -> None:
    """Löschen bestätigt der Klick, und das Ja bekommt eine Antwort statt „Störung".

    Betreiberwahl vom 23.09.2026: ein gesprochenes Ja genügt für keinen
    Löschvorgang. `vorschlag_ausfuehren` weist es ab. Ohne diesen Zweig kam
    die Abweisung hier als „Störung" an, und der Mensch hätte gedacht, etwas
    sei kaputt. Der Hinweis wird angezeigt und nicht gesprochen: feste Sätze
    spricht die Stimme nicht (Veto vom 18.08.2026).
    """
    bruecke = _Attrappe()
    bruecke._offene_vorschlaege = ["loeschen-1"]
    bruecke.lage = (None, True)
    ausgefuehrt: list[str] = []

    def ausfuehren(kennung: str) -> Ausgang:
        ausgefuehrt.append(kennung)
        return Ausgang(erledigt=True)

    bruecke._ausfuehren = ausfuehren  # type: ignore[method-assign]

    entschieden = await bruecke._entscheidung("Ja")

    assert entschieden is True
    assert ausgefuehrt == []
    assert {"art": "antworttext", "text": ai_voice_bridge.KLICK_TEXT} in bruecke.ereignisse
    assert not any(e.get("art") == "stoerung" for e in bruecke.ereignisse)
    assert bruecke.zustaende()[-1] == ai_voice_bridge.ZUSTAND_BEREIT


@pytest.mark.asyncio
async def test_ein_ja_nach_dem_klick_fuehrt_nicht_noch_einmal_aus() -> None:
    """Wer auf der Karte geklickt hat und danach „ja" sagt, bekommt keine Störung.

    Die Karte in der Sprachansicht trägt seit dem 23.09.2026 einen Knopf. Ein
    zweites Ausführen scheiterte am verbrauchten Einmal-Token und meldete
    „Störung", obwohl alles erledigt war.
    """
    bruecke = _Attrappe()
    bruecke._offene_vorschlaege = ["schon-geklickt"]
    bruecke.lage = ("succeeded", False)
    bruecke._ausfuehren = lambda kennung: pytest.fail("zweites Ausführen")  # type: ignore[method-assign]

    entschieden = await bruecke._entscheidung("Ja")

    assert entschieden is True
    assert not any(e.get("art") == "stoerung" for e in bruecke.ereignisse)
    assert bruecke.zustaende()[-1] == ai_voice_bridge.ZUSTAND_BEREIT


@pytest.mark.parametrize(
    "fensterart, verfolgt",
    [("primary", True), ("worker", False)],
)
def test_ein_geweckter_worker_wird_geweckt_aber_nicht_verfolgt(
    db, owner_user, monkeypatch, fensterart: str, verfolgt: bool
) -> None:
    """Das Ja weckt auch einen Worker — aber die Stimme folgt ihm nicht.

    Ein Vorschlag kann an einem Worker-Lauf hängen: der Auftrag hat um eine
    Freigabe gebeten, und die Karte kam als Meldung ins Gespräch. Das
    gesprochene Ja weckt ihn wie im Chat — wer sich danach aber anhängte,
    läse dem Menschen das rohe Protokoll eines Hintergrund-Auftrags vor.
    Die Stimme spricht ausschliesslich Gehirn-Ausgaben
    (docs/agentic-framework.md, §12); das Ergebnis des Workers kommt als
    Meldung, und die spricht der Zusteller. Der `primary`-Fall daneben
    beweist, dass die Ausnahme eng bleibt: ein gewöhnlicher Lauf wird
    weiterhin verfolgt.
    """
    from models import AiConversation, AiRun
    from services import ai_proposal_service, ai_run_service

    fenster = AiConversation(
        id=str(uuid4()), user_id=owner_user.id, kind=fensterart, title="Auftrag"
    )
    db.add(fenster)
    db.flush()
    lauf = AiRun(
        id=str(uuid4()),
        conversation_id=fenster.id,
        user_id=owner_user.id,
        status="waiting_confirmation",
    )
    db.add(lauf)
    db.commit()

    class _Vorschlag:
        run_id = lauf.id
        tool_name = "propose_server_lifecycle"
        proposal_type = "write"
        server_id = 1

    geweckt: list[str] = []

    monkeypatch.setattr(
        ai_proposal_service, "owned_proposal", lambda db_, kennung, user: _Vorschlag()
    )
    monkeypatch.setattr(
        ai_proposal_service,
        "confirm_proposal",
        lambda db_, *, proposal_id, user: (_Vorschlag(), "einmal-token"),
    )
    monkeypatch.setattr(
        ai_proposal_service,
        "execute_proposal",
        lambda db_, *, proposal_id, user, confirmation_token: (_Vorschlag(), {}),
    )
    monkeypatch.setattr(
        ai_run_service,
        "lauf_fortsetzen",
        lambda db_, *, run_id: geweckt.append(run_id) or True,
    )

    bruecke = _Attrappe(user_id=owner_user.id)
    ausgang = bruecke._ausfuehren("kennung")

    assert ausgang.erledigt is True
    # Das Wecken selbst bleibt in beiden Fällen — nur das Zuhören entfällt.
    assert geweckt == [lauf.id]
    assert ausgang.lauf_id == (lauf.id if verfolgt else None)
