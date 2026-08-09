"""Vertragstest zwischen Werkzeugtabelle und den Beschriftungen im Chat.

`services/ai_tool_registry.py` ist die Single Source of Truth dafuer, welche
Werkzeuge es gibt. Der Chat zeigt zu jedem einen Text aus den Locale-Dateien
und faellt, wenn er fehlt, still auf den Rohnamen zurueck:

    t(`ai.tools.${entry.tool.tool_name}`, { defaultValue: entry.tool.tool_name })

Genau dieser stille Rueckfall war der Fehler. Nach dem Bau von sechs neuen
Werkzeugen stand im Verlauf woertlich

    ai.tools.read_blueprint
    ai.actions.tools.propose_blueprint_change

statt "Blueprint gelesen" und "Blueprint ableiten". Aufgefallen ist es dem
Betreiber im Betrieb, nicht der Testsuite — und `propose_bind_ip_update` fehlte
da schon seit Phase D, ohne dass es jemand gemerkt hatte.

Der Test laeuft im Backend, weil dort die Wahrheit liegt, und liest die
Locale-Dateien als JSON. Dasselbe Muster wie
`test_permission_catalog_ui_contract.py`, aus demselben Anlass: ein Katalog, der
waechst, und eine Oberflaeche, die das Fehlen nicht meldet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.ai_tool_registry import WERKZEUGE, WRITE_TOOLS


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "frontend" / "src" / "locales"
SPRACHEN = ("de", "en")


def _texte(sprache: str) -> dict:
    pfad = LOCALES / f"{sprache}.json"
    assert pfad.is_file(), f"Locale-Datei nicht gefunden: {pfad}"
    return json.loads(pfad.read_text(encoding="utf-8"))


@pytest.mark.parametrize("sprache", SPRACHEN)
def test_every_read_tool_has_a_label(sprache: str) -> None:
    """Ohne Text steht der Rohname im Verlauf.

    Der Benutzer sieht dann `ai.tools.read_blueprint` statt "Blueprint gelesen"
    — und das an der Stelle, die erklaeren soll, was die KI gerade getan hat.
    """
    texte = _texte(sprache).get("ai", {}).get("tools", {})
    lesend = sorted(name for name in WERKZEUGE if name not in WRITE_TOOLS)
    fehlend = [name for name in lesend if not texte.get(name)]

    assert not fehlend, (
        f"Diese Lesewerkzeuge haben keinen Text in {sprache}.json unter "
        f"ai.tools und wuerden im Chat als Rohname erscheinen: {fehlend}"
    )


@pytest.mark.parametrize("sprache", SPRACHEN)
def test_every_write_tool_has_a_label(sprache: str) -> None:
    """Bei Schreibwerkzeugen ist es schlimmer als kosmetisch.

    Der Text steht auf der Bestaetigungskarte. Wer `propose_server_delete` als
    Rohname liest, bekommt nicht gesagt, dass er gerade dem Loeschen eines
    Servers zustimmt.
    """
    texte = _texte(sprache).get("ai", {}).get("actions", {}).get("tools", {})
    fehlend = [name for name in sorted(WRITE_TOOLS) if not texte.get(name)]

    assert not fehlend, (
        f"Diese Schreibwerkzeuge haben keinen Text in {sprache}.json unter "
        f"ai.actions.tools und wuerden auf der Bestaetigungskarte als Rohname "
        f"erscheinen: {fehlend}"
    )


def test_both_languages_cover_the_same_tools() -> None:
    """Eine Sprache nachzupflegen und die andere zu vergessen ist der Normalfall."""
    je_sprache = {}
    for sprache in SPRACHEN:
        daten = _texte(sprache).get("ai", {})
        je_sprache[sprache] = (
            set(daten.get("tools", {})),
            set(daten.get("actions", {}).get("tools", {})),
        )
    lesend = {s: paar[0] for s, paar in je_sprache.items()}
    schreibend = {s: paar[1] for s, paar in je_sprache.items()}

    assert lesend["de"] == lesend["en"], (
        "ai.tools weicht zwischen de und en ab: "
        f"{lesend['de'] ^ lesend['en']}"
    )
    assert schreibend["de"] == schreibend["en"], (
        "ai.actions.tools weicht zwischen de und en ab: "
        f"{schreibend['de'] ^ schreibend['en']}"
    )


@pytest.mark.parametrize("sprache", SPRACHEN)
def test_no_label_is_left_over_from_a_removed_tool(sprache: str) -> None:
    """Andersherum genauso: ein Text ohne Werkzeug ist toter Ballast.

    Beim Ausbau des Makro-Systems in Phase E sind Werkzeuge verschwunden. Texte,
    die niemand mehr zeigt, verwirren beim naechsten Uebersetzen.
    """
    daten = _texte(sprache).get("ai", {})
    ueberzaehlig = (
        set(daten.get("tools", {})) | set(daten.get("actions", {}).get("tools", {}))
    ) - set(WERKZEUGE)

    assert not ueberzaehlig, (
        f"Diese Texte in {sprache}.json gehoeren zu keinem Werkzeug mehr: "
        f"{sorted(ueberzaehlig)}"
    )
