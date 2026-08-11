"""Vertragstest: die Aktionsrouten antworten mit Schluesseln, nicht mit Saetzen.

`frontend/src/api/client.ts` schickt jedes `detail` einer Fehlerantwort durch
`i18n.t()`. Ein deutscher Satz ist kein Schluessel; `parseMissingKeyHandler` in
`i18n.ts` gibt ihn dann woertlich zurueck, und `AiActionProposalCard.tsx` zeigt
genau diesen Text bevorzugt vor seinem eigenen `t()`-Rueckfall an. Ein Benutzer
mit Sprache Englisch las deshalb "Aktionsvorschlag nicht gefunden".

Der Test liest die Routerquelle als Text - bewusst kein AST-Parser, sondern ein
enger regulaerer Ausdruck auf `detail="..."`. Der strukturierte Zweig
`detail={"code": ...}` wird davon nicht erfasst und bleibt erlaubt: dort traegt
der Code die Aussage, nicht der Text.

Geprueft werden nur `de` und `en`. Die uebrigen Sprachdateien kennen den Block
`ai.errors.codes` gar nicht und fallen laut `i18n.ts` auf Englisch zurueck.

Der Test steht in einer eigenen Datei und nicht in test_ai_actions.py: er
braucht weder Fixture noch Datenbank noch Client, sondern nur zwei Dateien -
und test_ai_actions.py ist bereits gross genug.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ROUTER = ROOT / "backend" / "routers" / "ai_actions.py"
LOCALES = ROOT / "frontend" / "src" / "locales"

PRAEFIX = "ai.errors.codes."
DETAIL_STRING = re.compile(r'detail="([^"]+)"')


def _detail_strings() -> list[str]:
    assert ROUTER.is_file(), f"Router nicht gefunden: {ROUTER}"
    return sorted(set(DETAIL_STRING.findall(ROUTER.read_text(encoding="utf-8"))))


def _uebersetzung(sprache: str, schluessel: str) -> str | None:
    """Laeuft den punktgetrennten Schluessel durch eine Sprachdatei."""
    knoten: object = json.loads(
        (LOCALES / f"{sprache}.json").read_text(encoding="utf-8")
    )
    for teil in schluessel.split("."):
        if not isinstance(knoten, dict) or teil not in knoten:
            return None
        knoten = knoten[teil]
    return knoten if isinstance(knoten, str) and knoten.strip() else None


def test_die_aktionsrouten_antworten_mit_schluesseln() -> None:
    """Welche Sprache der Benutzer liest, entscheidet das Panel - nicht der Router."""
    saetze = [text for text in _detail_strings() if not text.startswith(PRAEFIX)]

    assert not saetze, (
        "Diese `detail`-Texte in routers/ai_actions.py sind keine i18n-Schluessel "
        f"und erreichen den Benutzer unuebersetzt: {saetze}"
    )


@pytest.mark.parametrize("sprache", ["de", "en"])
def test_zu_jedem_schluessel_gibt_es_einen_satz(sprache: str) -> None:
    """Ein Schluessel ohne Satz ist schlimmer als der Satz: er ist unlesbar."""
    fehlend = [
        schluessel
        for schluessel in _detail_strings()
        if _uebersetzung(sprache, schluessel) is None
    ]

    assert not fehlend, (
        f"Diese Schluessel fehlen in {sprache}.json und stuenden als Rohschluessel "
        f"in der Oberflaeche: {fehlend}"
    )