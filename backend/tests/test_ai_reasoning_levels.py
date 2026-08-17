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
from services.ai_model_catalog import Modell


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
