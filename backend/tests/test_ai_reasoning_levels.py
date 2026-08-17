"""Denkstufen: was ein Modell kann, was eine Rolle erlaubt, was gesendet wird.

Die Zahlen in diesen Tests sind **gemessen**, nicht ausgedacht: am 2026-08-11
über alle 402 Einträge des OpenRouter-Katalogs. Sie stehen hier, weil sie die
Bauform begründen — jede Vereinfachung, die naheliegt, scheitert an einer davon.

* 272 Modelle denken, davon nennen nur **127** eine Stufenliste. Die übrigen
  **145** kennen ausschließlich an/aus. Deshalb zwei Felder und nicht eines.
* Die 127 verteilen sich auf **20 verschiedene** Zusammenstellungen — von
  ``['high']`` bis ``['max','xhigh','high','medium','low','none']``. Deshalb
  kommt die Auswahl aus dem Katalog und nicht aus einer Konstante.
* **82** können Nachdenken nicht abschalten. Deshalb ist „aus“ nicht überall
  eine gültige Wahl.
"""

from __future__ import annotations

import pytest

from services import ai_limit_service, ai_reasoning
from services.ai_provider_registry import Modell


def _modell(**overrides) -> Modell:
    werte = {
        "model_id": "anthropic/claude-opus-5",
        "name": "Claude Opus 5",
        "denkt": True,
        "stufen": ("max", "xhigh", "high", "medium", "low"),
        "standard_stufe": "high",
        "zwingend": False,
    }
    werte.update(overrides)
    return Modell(**werte)


# ── Die Ordnung ───────────────────────────────────────────────────────


def test_the_scale_matches_the_limit_services_maximum() -> None:
    """Zwei Zahlen, eine Bedeutung — sie dürfen nicht auseinanderlaufen.

    ``ai_limit_service`` führt die Obergrenze als eigene Konstante, damit es
    nicht von der Denklogik abhängt. Der Preis dafür ist genau dieser Test:
    ohne ihn könnte jemand eine Stufe ergänzen und der Rollendeckel würde sie
    still abschneiden.
    """
    assert ai_limit_service.MAX_REASONING_EFFORT_MAX == ai_reasoning.MAX_RANG


def test_off_is_not_a_depth() -> None:
    """``none`` gehört nicht in die Rangfolge, sondern in das An/Aus-Feld.

    Es steht in manchen Stufenlisten des Anbieters, meint dort aber „nicht
    nachdenken“. Als Rang 0 geführt gäbe es zwei Wege, dasselbe zu sagen — und
    zwei Wege heißt: irgendwann widersprechen sie sich.
    """
    assert ai_reasoning.AUS_STUFE not in ai_reasoning.RANGFOLGE
    assert ai_reasoning.rang(ai_reasoning.AUS_STUFE) is None
    assert ai_reasoning.waehlbare_stufen(_modell(stufen=("high", "none")), None) == ["high"]


def test_an_unknown_level_is_dropped_rather_than_guessed() -> None:
    """Ein neues Wort des Anbieters wird nicht angeboten, statt geraten.

    Es einzuordnen hieße raten, und ein geratener Rang unterläuft den
    Rollendeckel: eine Stufe, die MSM nicht vergleichen kann, kann es auch
    nicht begrenzen.
    """
    modell = _modell(stufen=("ultra", "high", "low"))
    assert ai_reasoning.waehlbare_stufen(modell, None) == ["low", "high"]


def test_levels_are_offered_shallow_to_deep() -> None:
    """Der Katalog liefert absteigend; eine Auswahl liest sich aufsteigend."""
    assert ai_reasoning.waehlbare_stufen(_modell(), None) == [
        "low", "medium", "high", "xhigh", "max",
    ]


# ── Der Deckel ────────────────────────────────────────────────────────


def test_the_role_cap_cuts_the_list_at_its_rank() -> None:
    deckel = ai_reasoning.rang("medium")
    assert ai_reasoning.waehlbare_stufen(_modell(), deckel) == ["low", "medium"]


def test_a_wish_above_the_cap_is_lowered_not_rejected() -> None:
    """Eine Kostengrenze ist kein Verbot.

    Der Benutzer bekommt eine Antwort — nur eine flachere. Eine Fehlermeldung
    wäre hier die schlechtere Wahl: sie erklärt einem Kunden ein Limit, das
    ihn nichts angeht, statt ihm zu helfen.
    """
    aktiv, stufe = ai_reasoning.klemmen(
        _modell(), wunsch="max", aktiv=True, deckel=ai_reasoning.rang("medium")
    )
    assert (aktiv, stufe) == (True, "medium")


def test_no_wish_takes_the_models_default_never_the_deepest() -> None:
    aktiv, stufe = ai_reasoning.klemmen(
        _modell(), wunsch=None, aktiv=True, deckel=None
    )
    assert (aktiv, stufe) == (True, "high")


def test_without_an_allowed_default_the_shallowest_wins() -> None:
    """Eine fehlende Angabe darf nie das Teuerste auslösen."""
    aktiv, stufe = ai_reasoning.klemmen(
        _modell(standard_stufe="max"), wunsch=None, aktiv=True,
        deckel=ai_reasoning.rang("low"),
    )
    assert (aktiv, stufe) == (True, "low")


def test_a_cap_of_zero_forbids_thinking() -> None:
    aktiv, stufe = ai_reasoning.klemmen(
        _modell(), wunsch="max", aktiv=True, deckel=0
    )
    assert (aktiv, stufe) == (False, None)


# ── Die Mehrheit: Modelle ohne Stufen ─────────────────────────────────


def test_a_model_without_levels_is_still_switchable() -> None:
    """145 der 272 denkenden Modelle landen hier — das ist kein Randfall."""
    modell = _modell(stufen=(), standard_stufe=None)
    assert ai_reasoning.waehlbare_stufen(modell, None) == []
    assert ai_reasoning.klemmen(modell, wunsch=None, aktiv=True, deckel=None) == (True, None)
    assert ai_reasoning.klemmen(modell, wunsch=None, aktiv=False, deckel=None) == (False, None)


def test_a_cap_of_zero_also_silences_a_model_without_levels() -> None:
    """Ohne diesen Zweig wäre der Deckel bei der Mehrheit der Modelle wirkungslos."""
    modell = _modell(stufen=(), standard_stufe=None)
    assert ai_reasoning.klemmen(modell, wunsch=None, aktiv=True, deckel=0) == (False, None)


# ── Was sich nicht abschalten lässt ───────────────────────────────────


def test_a_mandatory_model_never_pretends_to_be_off() -> None:
    """82 der 402 Modelle denken zwingend — ein „aus“ wäre dort gelogen.

    Der Anbieter denkt ohnehin und rechnet es ab. MSM meldet den Zustand,
    statt einen Schalter anzubieten, der nichts bewirkt.
    """
    modell = _modell(model_id="google/gemini-3.6-flash", zwingend=True)
    assert ai_reasoning.darf_abschalten(modell) is False
    assert ai_reasoning.darf_nachdenken(modell, 0) is True
    aktiv, _stufe = ai_reasoning.klemmen(modell, wunsch=None, aktiv=False, deckel=None)
    assert aktiv is True


def test_a_mandatory_model_still_obeys_the_cap_for_its_depth() -> None:
    """Nicht abschaltbar heißt nicht unbegrenzt tief."""
    modell = _modell(model_id="qwen/qwen3.8-max", zwingend=True)
    aktiv, stufe = ai_reasoning.klemmen(
        modell, wunsch="max", aktiv=True, deckel=ai_reasoning.rang("low")
    )
    assert (aktiv, stufe) == (True, "low")


# ── Wenn der Deckel unter der Untergrenze des Modells liegt ───────────
#
# Die drei folgenden Zusicherungen hat eine Reviewrunde am 2026-08-11 als
# fehlend nachgewiesen — alle drei Fälle waren falsch, und alle drei hatten
# dieselbe Ursache: „das Modell kennt keine Stufen“ und „der Deckel hat alle
# Stufen weggeschnitten“ liefen durch denselben Zweig. Das Ergebnis war jeweils
# „an, ohne Stufe“, und ein fehlendes ``effort`` ist bei OpenRouter keine
# Sparsamkeit, sondern die Vorgabe des Anbieters.


def test_a_cap_below_the_models_floor_switches_thinking_off() -> None:
    """Die Rolle darf ``low``, das Modell fängt bei ``high`` an — also aus.

    Der teure Irrtum war „an, ohne Stufe“: OpenRouter setzt dann seinen
    ``default_effort`` ein, hier ``high``. Die Rolle durfte ``low`` und
    bezahlte ``high`` — der Deckel stand da, wirkte aber nicht.
    """
    modell = _modell(stufen=("max", "xhigh", "high"), standard_stufe="high")
    assert ai_reasoning.waehlbare_stufen(modell, ai_reasoning.rang("low")) == []
    assert ai_reasoning.klemmen(
        modell, wunsch=None, aktiv=True, deckel=ai_reasoning.rang("low")
    ) == (False, None)


def test_a_mandatory_model_under_a_low_cap_gets_the_shallowest_it_knows() -> None:
    """Abschalten geht nicht — dann wenigstens so flach wie möglich.

    ``None`` wäre hier das Gegenteil von sparsam: es überlässt die Tiefe dem
    Anbieter. Genannt wird deshalb die flachste Stufe, die das Modell führt.
    """
    modell = _modell(stufen=("max", "high"), standard_stufe="max", zwingend=True)
    assert ai_reasoning.klemmen(
        modell, wunsch=None, aktiv=True, deckel=ai_reasoning.rang("low")
    ) == (True, "high")


def test_a_wish_below_the_models_floor_is_raised_to_the_shallowest_not_the_deepest() -> None:
    """Wer um wenig bittet, darf nicht das Teuerste bekommen.

    Bisher galt jede Abweichung als „zu hoch“ und wurde auf ``erlaubt[-1]``
    gesetzt. Bei einem Modell ab ``high`` wurde aus der Bitte um ``low`` damit
    ``max`` — die Klemmung lief in die falsche Richtung.
    """
    modell = _modell(stufen=("max", "high"), standard_stufe="high")
    assert ai_reasoning.klemmen(
        modell, wunsch="low", aktiv=True, deckel=None
    ) == (True, "high")


def test_a_model_without_levels_still_sends_no_level() -> None:
    """Die Gegenprobe: hier ist „an, ohne Stufe“ richtig und bleibt es.

    145 der 272 denkenden Modelle kennen keine Stufen. Für sie gibt es keine,
    die man nennen könnte — der Fall darf durch die Trennung oben nicht
    versehentlich mit abgeschaltet werden.
    """
    modell = _modell(stufen=(), standard_stufe=None)
    assert ai_reasoning.klemmen(
        modell, wunsch=None, aktiv=True, deckel=ai_reasoning.rang("low")
    ) == (True, None)


# ── „Aus“ als Wort, für Anbieter ohne Schalter ────────────────────────
#
# OpenRouter kennt einen Schalter (``reasoning: {"enabled": false}``); dort
# genügt das ``False``. OpenAI kennt keinen — dort ist „aus“ die Stufe
# ``reasoning_effort: "none"``, und ohne dieses Wort geht gar nichts hinaus.
# Das Modell denkt dann in OpenAIs Voreinstellung weiter (``medium`` bei
# ``gpt-5.5``): abgeschaltet in der Oberfläche, bezahlt auf der Rechnung.
#
# Ob ein Modell das Wort verträgt, sagt **nur der Katalog**. Es aus ``zwingend``
# zu folgern wäre falsch: ``gpt-5.1-codex-mini`` ist abschaltbar im Sinne von
# `darf_abschalten` und führt trotzdem kein ``none`` in seinen Stufen.


def test_off_is_named_when_the_model_knows_a_word_for_it() -> None:
    """Abgeschaltet heißt abgeschaltet — auch beim Anbieter ohne Schalter."""
    modell = _modell(stufen=("high", "medium", "low", "none"))
    assert ai_reasoning.klemmen(
        modell, wunsch=None, aktiv=False, deckel=None
    ) == (False, "none")


def test_off_stays_wordless_when_the_model_lists_no_such_level() -> None:
    """Kein Wort im Katalog, kein Wort in der Anfrage.

    Der Gegenbeleg ist ``openai/gpt-5.1-codex-mini``: abschaltbar, aber ohne
    ``none`` in ``supported_efforts``. Eines zu senden hiesse, eine Stufe zu
    erfinden — und der Anbieter antwortet darauf mit einem 400, das die ganze
    Anfrage verwirft.
    """
    modell = _modell(stufen=("high", "medium", "low"))
    assert ai_reasoning.klemmen(
        modell, wunsch=None, aktiv=False, deckel=None
    ) == (False, None)


def test_a_cap_that_cuts_everything_also_says_off_out_loud() -> None:
    """Der Deckel schaltet ab — und muss das genauso deutlich sagen.

    Die Rolle darf höchstens ``low``, das Modell fängt bei ``high`` an. Ohne das
    Wort bliebe es bei der Vorgabe des Anbieters, also genau bei der Tiefe, die
    der Deckel verbietet.
    """
    modell = _modell(stufen=("max", "xhigh", "high", "none"), standard_stufe="high")
    assert ai_reasoning.klemmen(
        modell, wunsch="max", aktiv=True, deckel=ai_reasoning.rang("low")
    ) == (False, "none")


def test_a_model_whose_only_level_is_off_still_switches_off() -> None:
    """``('none',)`` ist eine leere Auswahl und trotzdem ein Wort.

    ``none`` fällt aus der Auswahl heraus (`waehlbare_stufen`), das Modell
    landet damit im Zweig „kennt keine Stufen" — dort muss das Wort trotzdem
    ankommen, sonst wirkt ein Rollendeckel von 0 bei genau diesen Modellen
    nicht.
    """
    modell = _modell(stufen=("none",), standard_stufe=None)
    assert ai_reasoning.waehlbare_stufen(modell, None) == []
    assert ai_reasoning.klemmen(modell, wunsch=None, aktiv=False, deckel=None) == (
        False,
        "none",
    )
    assert ai_reasoning.klemmen(modell, wunsch=None, aktiv=True, deckel=0) == (
        False,
        "none",
    )


def test_a_mandatory_model_is_never_told_to_stop() -> None:
    """Denkzwang schlägt das Wort. Sonst ginge ein ``none`` an ein Modell, das es ablehnt."""
    modell = _modell(stufen=("high", "none"), zwingend=True)
    aktiv, stufe = ai_reasoning.klemmen(modell, wunsch=None, aktiv=False, deckel=None)
    assert aktiv is True
    assert stufe != ai_reasoning.AUS_STUFE


def test_a_non_thinking_model_says_nothing_at_all() -> None:
    """Weder Schalter noch Wort: wer nicht denkt, bekommt das Feld nicht.

    Der Zweig steht **vor** allen anderen, und das ist wichtig: ein Modell ohne
    Denkfähigkeit antwortet auf ``reasoning_effort`` mit einem 400, gleich
    welchen Wert man nennt — ``none`` eingeschlossen.
    """
    modell = _modell(denkt=False, stufen=("none",))
    assert ai_reasoning.klemmen(modell, wunsch=None, aktiv=False, deckel=None) == (
        False,
        None,
    )


# ── Modelle, die gar nicht denken ─────────────────────────────────────


def test_a_non_thinking_model_gets_no_field_at_all() -> None:
    modell = _modell(denkt=False, stufen=(), standard_stufe=None)
    assert ai_reasoning.darf_nachdenken(modell, None) is False
    assert ai_reasoning.klemmen(modell, wunsch="high", aktiv=True, deckel=None) == (False, None)


def test_the_frontend_dropdown_carries_the_same_rank_words() -> None:
    """Die Rollen-Deckel-Auswahl pflegt die Rangwoerter als zweite Kopie.

    `AiTab.tsx` verspricht per Kommentar „dieselbe Reihenfolge wie
    `services/ai_reasoning.RANGFOLGE`" — geprueft hat das bisher niemand.
    Kommt in `RANGFOLGE` eine Stufe dazu oder faellt eine weg, zeigte die
    Auswahl still falsche Woerter zu falschen Raengen. Rang 0 ist „aus" und
    traegt im Backend bewusst kein Wort; vorn steht deshalb ``off``.

    Der Test liest die Frontend-Datei als Text. Das ist grob, aber die
    ehrlichste Bruecke, die es zwischen den beiden Sprachen gibt — eine
    API dafuer waere ein Endpunkt fuer sieben Woerter.
    """
    import re
    from pathlib import Path

    pfad = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "pages" / "settings" / "AiTab.tsx"
    )
    quelle = pfad.read_text(encoding="utf-8")
    treffer = re.search(r"const REASONING_RANKS = \[(.*?)\]", quelle)
    assert treffer is not None, "REASONING_RANKS steht nicht mehr in AiTab.tsx"
    woerter = re.findall(r"'([a-z]+)'", treffer.group(1))
    assert woerter == ["off", *ai_reasoning.RANGFOLGE]


@pytest.mark.parametrize("wert", [0, 7, -1])
def test_ranks_outside_the_scale_have_no_word(wert: int) -> None:
    assert ai_reasoning.stufe_fuer_rang(wert) is None


# ── Nebenaufträge: Falten, Mailtext, Diktat ───────────────────────────
#
# Drei Aufrufer gehen am Chat vorbei direkt an den Adapter. Sie wollen alle
# dasselbe — nicht nachdenken —, und sie sagten es alle drei so, dass es nur
# bei einem Anbieter mit Schalter ankam: `reasoning=False` und sonst nichts.
# Bei OpenAI ging damit gar keine Zeile hinaus, und keine Zeile heisst dort
# „nimm deine Vorgabe". Jede Faltung, jede Betreibermail und jedes Diktat
# wurde also mit Denkschritten bezahlt, die niemand bestellt hatte.


def _provider(kind: str = "openai", model: str = "gpt-5.5"):
    from models import AiProvider

    return AiProvider(
        id=1, name="P", provider_kind=kind, default_model=model,
        enabled=True, requires_api_key=False,
    )


def _katalog(monkeypatch: pytest.MonkeyPatch, modell: Modell | None) -> dict:
    """Ersetzt den Katalog und merkt sich, wonach gefragt wurde."""
    gefragt: dict = {}

    async def finde(_client, kind, model_id, *, schluessel=None):
        gefragt.update(kind=kind, model_id=model_id, schluessel=schluessel)
        return modell

    monkeypatch.setattr(ai_reasoning.ai_model_catalog, "finde", finde)
    return gefragt


@pytest.mark.asyncio
async def test_a_side_job_says_off_in_the_providers_own_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kennt das Modell ein Wort für „aus“, geht es mit — auch ohne Chat."""
    _katalog(monkeypatch, _modell(stufen=("high", "medium", "none")))
    assert await ai_reasoning.aus_fuer(None, _provider()) == (
        False, ai_reasoning.AUS_STUFE,
    )


@pytest.mark.asyncio
async def test_a_side_job_at_a_mandatory_model_takes_the_shallowest_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abschalten geht nicht — dann wenigstens nicht die teure Vorgabe.

    Dieselbe Regel wie im Chat, und aus demselben Grund: sie steht nur einmal
    da, nämlich in `klemmen`. `aus_fuer` holt den Katalog und fragt.
    """
    _katalog(monkeypatch, _modell(
        stufen=("max", "high", "medium", "low"), standard_stufe="high", zwingend=True,
    ))
    assert await ai_reasoning.aus_fuer(None, _provider()) == (True, "low")


@pytest.mark.asyncio
async def test_a_side_job_at_a_silent_catalog_sends_no_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unbekanntes Modell: nichts erfinden.

    ``none`` blind mitzuschicken wäre schlimmer als der verlorene Deckel — ein
    Modell ohne Denkvermögen weist die Zeile hart ab (*Unrecognized request
    argument supplied: reasoning_effort*), und ob es eines ist, weiss genau die
    Quelle nicht, die hier schweigt.
    """
    _katalog(monkeypatch, None)
    assert await ai_reasoning.aus_fuer(None, _provider()) == (False, None)


@pytest.mark.asyncio
async def test_a_side_job_asks_the_catalog_with_the_key_it_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kontogebundene Kataloge brauchen den Schlüssel des Auftrags.

    OpenAIs ``/v1/models`` antwortet je Zugang verschieden. Ohne den Schlüssel
    käme hier gar keine Liste — und damit nie ein Wort für „aus“.
    """
    gefragt = _katalog(monkeypatch, _modell(stufen=("none", "low")))
    await ai_reasoning.aus_fuer(
        None, _provider(), api_key="sk-geheim", model_id="gpt-5.5-mini",
    )
    assert gefragt == {
        "kind": "openai", "model_id": "gpt-5.5-mini", "schluessel": "sk-geheim",
    }


# ── Das Modell wechselt mitten im Lauf ────────────────────────────────
#
# Die Denkstufe eines Laufs steht in `AiRun` und bleibt über alle Segmente
# stehen; der Zugang wird je Segment frisch gelesen. Der Betreiber darf also
# das ``default_model`` austauschen, während ein Lauf auf eine Bestätigung
# wartet — und die eingefrorene Stufe gehört dann zum alten Modell.


def _vorbereitung(*, reasoning: bool, effort: str | None):
    from services.ai_stream_service import _Vorbereitung

    return _Vorbereitung(
        run_id="r", user_id=1, conversation_id="c", provider=_provider(),
        api_key=None, message_id="m", usage_event_id=1, request_id="q",
        reasoning=reasoning, reasoning_effort=effort,
        token_price_micro_usd_per_million=None, zustand={},
        angebotene_werkzeuge=frozenset(),
    )


def _pruefe(modell: Modell | None, *, reasoning=True, effort="xhigh"):
    from services.ai_stream_service import _denken_am_modell

    return _denken_am_modell(_vorbereitung(reasoning=reasoning, effort=effort), modell)


def test_a_frozen_level_the_new_model_knows_goes_out_unchanged() -> None:
    assert _pruefe(_modell(stufen=("max", "xhigh", "high"))) == (True, "xhigh")


def test_a_frozen_level_the_new_model_lacks_is_lowered_never_raised() -> None:
    """Sonst ist jedes weitere Segment ein ``400`` — der Lauf läuft nie wieder an.

    Gesenkt und nicht verworfen: ohne Stufe nähme der Anbieter seine eigene
    Vorgabe, und die kann über der stehen, die dieser Lauf einmal zugeteilt
    bekam. Die eingefrorene Stufe ist hier die **Decke**.
    """
    assert _pruefe(_modell(stufen=("high", "low"))) == (True, "high")
    assert _pruefe(_modell(stufen=("max",))) == (False, None), (
        "Das neue Modell kann nur tiefer als die eingefrorene Stufe — dann gar "
        "nicht denken statt teurer denken als der Lauf einmal zugeteilt bekam"
    )


def test_a_frozen_level_at_a_model_that_stopped_thinking_is_dropped() -> None:
    """Getauscht gegen ein Modell ohne Denkvermögen: dort ist jedes Wort ein 400."""
    assert _pruefe(_modell(denkt=False, stufen=())) == (False, None)


def test_a_frozen_off_stays_off_in_the_new_models_words() -> None:
    """„Aus“ ist auch nur ein Wort, und das neue Modell führt es womöglich nicht."""
    assert _pruefe(_modell(stufen=("high", "low")), reasoning=False, effort="none") == (
        False, None,
    )
    assert _pruefe(
        _modell(stufen=("high", "none")), reasoning=False, effort="none",
    ) == (False, "none")
    # Denkzwang: abschalten geht nicht, dann wenigstens die flachste Stufe.
    assert _pruefe(
        _modell(stufen=("max", "high", "low"), zwingend=True),
        reasoning=False, effort="none",
    ) == (True, "low")


def test_a_silent_catalog_changes_nothing_about_the_frozen_level() -> None:
    """Eine Netzstörung darf keine Stufe abräumen — das wäre stille Verteuerung."""
    assert _pruefe(None) == (True, "xhigh")


def test_a_missing_level_is_never_filled_in_from_the_model() -> None:
    """``None`` heisst „kein Feld“ und darf nicht zur Vorgabe des Modells werden.

    Das wäre eine Erhöhung nach oben, am ursprünglichen Rollendeckel vorbei —
    genau der Grund, warum hier geprüft und nicht neu geklemmt wird.
    """
    assert _pruefe(_modell(stufen=("max", "high"), standard_stufe="max"), effort=None) == (
        True, None,
    )


def test_only_a_provider_with_a_switch_can_be_off_without_a_word() -> None:
    """Woran MSM erkennt, dass ``(False, None)`` wirklich „aus“ heisst.

    Nicht am Namen des Anbieters, sondern an seinem Wortschatz. Genau diese
    Frage entscheidet, ob ein Rollendeckel von 0 bei unbekanntem Modell greift
    — bei OpenRouter tut er es, bei OpenAI nicht (`vorgabe`).
    """
    assert ai_reasoning._kennt_schalter("openrouter") is True
    assert ai_reasoning._kennt_schalter("openai") is False
    # Und ein Schlüssel, den es nicht gibt, nimmt keinen Lauf mit.
    assert ai_reasoning._kennt_schalter("gibtesnicht") is False
