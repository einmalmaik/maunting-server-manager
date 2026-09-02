"""Was passiert, wenn die KI hinsehen will und das Modell keine Bilder liest.

Der Anlass ist ein Bericht des Betreibers vom 23.08.2026: er bat um einen
Blick auf seinen Bildschirm und bekam *"Der Bildschirmzugriff ist abgelaufen,
bevor ein Bild bereitgestellt wurde."* — eine Erklaerung, die nichts erklaert.

Zwei Regeln haelt diese Datei fest.

**Unbekannt ist nicht blind.** ``sieht`` kennt drei Werte, und verweigert wird
nur bei einem ausdruecklichen ``False``. Faellt der Katalog aus, faehrt das
Bild mit; ein Anbieter, der es nicht mag, sagt das selbst. Dieselbe Regel wie
beim Kontextfenster, wo ``None`` "unbekannt" heisst und nie "klein".

**Die KI spricht ueber sich, nicht ueber Modelle.** Der Betreiber hat das
woertlich verlangt: *"das sagt die ki aber ... nicht dass das Modell es nicht
kann, sondern dass sie die Faehigkeiten dazu nicht besitzt."* Die technische
Wahrheit steht dort, wo man das Modell waehlt — als Marke "Sieht Bilder".
"""

from dataclasses import replace
from types import SimpleNamespace

from services import ai_model_catalog, ai_stream_service
from services.ai_provider_registry.basis import Modell
from services.ai_provider_registry.openrouter import _sieht, katalog_lesen
from services.ai_stream_service import (
    KEIN_BLICK_GRUND,
    _runde_filtern,
    _sieht_nicht,
)


def _blick(ruf_id: str = "ruf-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=ruf_id, name="desktop_system", arguments={"aktion": "bildschirm"}
    )


class TestKatalog:
    def test_image_in_den_modalitaeten_heisst_sieht(self):
        assert _sieht({"architecture": {"input_modalities": ["text", "image"]}}) is True

    def test_nur_text_heisst_sieht_nicht(self):
        assert _sieht({"architecture": {"input_modalities": ["text"]}}) is False

    def test_ohne_angabe_bleibt_es_unbekannt(self):
        """Kein ``False``: der Katalog sagt hier nichts, und Schweigen ist
        keine Aussage ueber das Modell."""
        assert _sieht({"architecture": {}}) is None
        assert _sieht({}) is None
        assert _sieht({"architecture": "kaputt"}) is None

    def test_der_wert_landet_am_modell(self):
        """Bei beiden Zweigen von ``katalog_lesen`` — denkend und nicht."""
        stumpf = katalog_lesen({
            "id": "a/b", "architecture": {"input_modalities": ["text", "image"]},
        })
        denkend = katalog_lesen({
            "id": "c/d",
            "architecture": {"input_modalities": ["text"]},
            "reasoning": {"supported_efforts": ["low", "high"]},
        })
        assert stumpf.sieht is True
        assert denkend.sieht is False

    def test_geliehen_wird_die_bildsicht_wie_das_fenster(self):
        """Anders als die Cache-Marke: ob ein Modell Bilder liest, haengt am
        Modell und nicht am Weg dorthin."""
        eigen = Modell(model_id="mein-deployment", name="mein-deployment", denkt=False)
        fremd = Modell(model_id="gpt-x", name="GPT-X", denkt=True, sieht=True)
        assert ai_model_catalog._anreichern(eigen, fremd).sieht is True

    def test_die_eigene_angabe_schlaegt_die_geliehene(self):
        eigen = Modell(model_id="x", name="x", denkt=False, sieht=False)
        fremd = Modell(model_id="x", name="X", denkt=False, sieht=True)
        assert ai_model_catalog._anreichern(eigen, fremd).sieht is False


class TestWannVerweigertWird:
    def test_ein_blindes_modell_sieht_nicht_hin(self):
        assert _sieht_nicht({"sieht": False}, _blick()) is True

    def test_unbekannt_ist_nicht_blind(self):
        """Der teurere Irrtum waere hier das Wegwerfen: ein Katalogausfall
        naehme der KI die Augen, ohne dass irgendwer es merkt."""
        assert _sieht_nicht({}, _blick()) is False
        assert _sieht_nicht({"sieht": None}, _blick()) is False

    def test_ein_sehendes_modell_sieht_hin(self):
        assert _sieht_nicht({"sieht": True}, _blick()) is False

    def test_nur_der_blick_ist_betroffen(self):
        """`desktop_system` kann sieben andere Dinge, und keines davon
        braucht Augen."""
        prozesse = SimpleNamespace(
            id="r", name="desktop_system", arguments={"aktion": "prozesse"}
        )
        klick = SimpleNamespace(
            id="r", name="desktop_steuern", arguments={"aktion": "klick"}
        )
        assert _sieht_nicht({"sieht": False}, prozesse) is False
        assert _sieht_nicht({"sieht": False}, klick) is False

    def test_fehlende_argumente_sind_kein_absturz(self):
        ohne = SimpleNamespace(id="r", name="desktop_system", arguments=None)
        assert _sieht_nicht({"sieht": False}, ohne) is False


class TestDerAufrufWirdBeantwortet:
    def test_der_blick_wandert_mit_begruendung_weg(self):
        """Aussortiert, nicht geworfen: der Lauf arbeitet weiter."""
        usage = SimpleNamespace(tool_calls=[_blick()])
        zustand = {"sieht": False}
        deferred, signal = _runde_filtern(
            kinds={"read"},
            current_usage=usage,
            signaturen={},
            zustand=zustand,
            run_id="lauf-1",
        )
        assert usage.tool_calls == []
        assert [grund for _, grund in deferred] == [KEIN_BLICK_GRUND]
        # Nicht "fertig": es gibt eine Antwort zu geben.
        assert signal is None

    def test_ein_sehendes_modell_behaelt_seinen_aufruf(self):
        usage = SimpleNamespace(tool_calls=[_blick()])
        deferred, _ = _runde_filtern(
            kinds={"read"},
            current_usage=usage,
            signaturen={},
            zustand={"sieht": True},
            run_id="lauf-1",
        )
        assert len(usage.tool_calls) == 1
        assert deferred == []

    def test_der_blick_ueberlebt_eine_gemischte_runde(self):
        """Die Runde, in der das Modell hinsieht, liest **und** schreibt.

        In der Mischrunden-Absage stand eine Zuweisung statt eines `extend`,
        und die warf die Absage des blinden Blicks darueber wieder weg: kein
        Ergebnis, keine Begruendung, kein Hinweis — der Aufruf verschwand
        spurlos, und das Modell hatte eine `tool_call_id` ohne Antwort.

        Nur diese eine Zusage kann das sehen. Alle anderen hier fahren mit
        ``kinds={"read"}`` und laufen an der Absage vorbei.
        """
        lesen = SimpleNamespace(
            id="ruf-2", name="desktop_system", arguments={"aktion": "prozesse"}
        )
        schreiben = SimpleNamespace(
            id="ruf-3", name="propose_config_set", arguments={"server_id": 1}
        )
        usage = SimpleNamespace(tool_calls=[_blick(), lesen, schreiben])

        deferred, signal = _runde_filtern(
            kinds={"read", "write"},
            current_usage=usage,
            signaturen={},
            zustand={"sieht": False},
            run_id="lauf-1",
        )

        gruende = {ruf.id: grund for ruf, grund in deferred}
        assert [ruf.id for ruf, _ in deferred] == ["ruf-1", "ruf-3"]
        assert gruende["ruf-1"] == KEIN_BLICK_GRUND
        assert "eigenen Runde" in gruende["ruf-3"]
        # Gelaufen ist nur, was dieses Modell auch lesen kann.
        assert [ruf.id for ruf in usage.tool_calls] == ["ruf-2"]
        assert signal is None

    def test_ein_blinder_blick_zaehlt_nicht_als_schleife(self):
        """Der Aufruf laeuft nie — ihn zu zaehlen hiesse, dem Modell spaeter
        eine Wiederholung vorzuwerfen, die nie stattgefunden hat."""
        signaturen: dict[str, int] = {}
        usage = SimpleNamespace(tool_calls=[_blick()])
        _runde_filtern(
            kinds={"read"},
            current_usage=usage,
            signaturen=signaturen,
            zustand={"sieht": False},
            run_id="lauf-1",
        )
        assert signaturen == {}


class TestDerWortlaut:
    """Die Regel des Betreibers, als Zusage am Text festgehalten.

    Kein Test kann pruefen, was das Modell daraus macht — aber sehr wohl, dass
    die Anweisung das Richtige verlangt und das Falsche nicht anbietet.
    """

    def test_sie_soll_von_sich_sprechen(self):
        assert "in der ersten Person" in KEIN_BLICK_GRUND
        assert "Fähigkeit" in KEIN_BLICK_GRUND

    def test_kein_wort_ueber_modelle_oder_einstellungen(self):
        assert "nicht über Modelle" in KEIN_BLICK_GRUND

    def test_es_ist_eine_anweisung_und_keine_fertige_phrase(self):
        """Sonst waere es die Panel-Phrase, die der Betreiber am 18.08.2026
        ausdruecklich verboten hat: alles Gesagte ist KI-erzeugt."""
        assert "in eigenen Worten" in KEIN_BLICK_GRUND


class TestDasFeldKommtVomKatalog:
    def test_der_zustand_traegt_drei_werte(self):
        """Kein ``bool(...)`` auf dem Weg vom Katalog in den Lauf — das machte
        aus "unbekannt" ein "blind" und naehme der KI still die Augen."""
        sehend = Modell(model_id="a", name="a", denkt=False, sieht=True)
        blind = replace(sehend, sieht=False)
        unbekannt = replace(sehend, sieht=None)
        for modell, erwartet in ((sehend, True), (blind, False), (unbekannt, None)):
            assert modell.sieht is erwartet
        # Und der Standard eines Modells, das niemand gefragt hat:
        assert Modell(model_id="x", name="x", denkt=False).sieht is None

    def test_das_bildfeld_heisst_ueberall_gleich(self):
        """`bildschirm.rs`, der Router und der Sendepfad muessen dasselbe Feld
        meinen; ein Tippfehler an einer Stelle ist unsichtbar.

        Die dritte Stelle steht in Rust und wird deshalb als Text gelesen —
        dasselbe Vorgehen wie in `test_ai_tool_handler_contract.py`, wo
        `frontend/src/api/ai.ts` gegen den Backend-Vertrag gehalten wird. Ohne
        sie prueft dieser Test nur zwei Konstanten gegeneinander, und genau die
        gefaehrliche Richtung bliebe gruen: benennt die App das Feld um, findet
        `ai_stream_service` das Bild nie mehr, `routers/desktop` rechnet es
        gegen das Textbudget, und jedes Bildschirmfoto scheitert als zu gross.
        Die Aufnahme klappt, ankommen tut nichts — der Auge-ohne-Sehnerv-Fehler,
        dessentwegen es diese Datei gibt.
        """
        from pathlib import Path

        from routers import desktop as desktop_router

        assert desktop_router.BILDFELD == ai_stream_service.BILDFELD
        rust = (
            Path(ai_stream_service.__file__).resolve().parents[2]
            / "smart-system" / "src-tauri" / "src" / "bildschirm.rs"
        ).read_text(encoding="utf-8")
        assert f'"{ai_stream_service.BILDFELD}"' in rust
