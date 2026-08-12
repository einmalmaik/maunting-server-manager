"""Was die KI ueber MSM sagt, muss aus der Doku stammen — also muss die Doku
ankommen.

Diese Datei sichert die Zusagen, an denen der Korpus still scheitern koennte,
ohne dass es jemand merkt: eine Seite, die keine Abschnitte mehr liefert, eine
Gliederung, die von der gerenderten Seite abweicht, ein Suchbegriff, den nur
die ASCII-Umschreibung in der Sprachdatei verhindert. Jeder dieser Faelle
erzeugt beim Benutzer dieselbe Antwort — eine erfundene.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from services import ai_action_service, ai_context_service
from services import ai_docs_corpus as korpus
from services.ai_action_errors import AiActionValidationError

FRONTEND = korpus.WURZEL / "frontend" / "src"


def test_every_page_delivers_sections() -> None:
    """Keine Seite darf leer ankommen.

    Ein leeres Verzeichnis waere die schlimmste Form des Ausfalls: das Modell
    bekaeme ein gueltiges Ergebnis und schloesse daraus, in der Doku stehe
    nichts.
    """
    for schluessel in korpus.SEITEN:
        verzeichnis = korpus.verzeichnis(schluessel)
        assert verzeichnis["sections"], schluessel
        assert all(a["chars"] > 0 for a in verzeichnis["sections"]), schluessel


def test_a_missing_source_is_reported_not_swallowed() -> None:
    """"Konnte nicht lesen" und "steht nichts drin" sind zwei Auskuenfte.

    Nur eine davon darf das Modell weitergeben. Dieselbe Unterscheidung wie bei
    `web_search`, das bei Ausfall `available: false` meldet statt einer leeren
    Trefferliste.
    """
    with pytest.raises(korpus.DokuNichtVerfuegbar):
        korpus.abschnitte("gibt-es-nicht")


def test_missing_markdown_file_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fehlend = korpus.SEITEN["hoster-api"].__class__(
        schluessel="hoster-api",
        titel="x",
        route="/docs/hoster-api",
        quelle="markdown",
        datei=Path("/gibt/es/nicht/hoster-api.md"),
    )
    monkeypatch.setitem(korpus.SEITEN, "hoster-api", fehlend)
    korpus._cache.pop("hoster-api", None)
    with pytest.raises(korpus.DokuNichtVerfuegbar):
        korpus.abschnitte("hoster-api")
    korpus._cache.pop("hoster-api", None)


def test_the_privacy_order_is_the_rendered_order_not_the_json_order() -> None:
    """`Privacy.tsx` rendert anders, als `de.json` sortiert.

    Im JSON stehen `hoster` und `credentials` am Ende, auf der Seite in der
    Mitte — und die uebrigen Abschnitte tragen Nummern im Ueberschriftentext.
    Wer die JSON-Reihenfolge ausgibt, behauptet eine Gliederung, die kein
    Mensch je zu sehen bekommt.
    """
    quelle = (FRONTEND / "pages" / "Privacy.tsx").read_text(encoding="utf-8")
    gerendert = tuple(
        dict.fromkeys(re.findall(r"privacyPolicy\.sections\.(\w+)\.heading", quelle))
    )
    assert korpus.SEITEN["datenschutz"].reihenfolge == gerendert


def test_the_oauth_order_is_the_rendered_order() -> None:
    quelle = (FRONTEND / "pages" / "docs" / "OAuthDocs.tsx").read_text(encoding="utf-8")
    gerendert = tuple(
        dict.fromkeys(re.findall(r'id="oauth-docs-([\w-]+)"', quelle))
    )
    assert korpus.SEITEN["oauth"].reihenfolge == gerendert


def test_the_privacy_date_and_version_match_the_page() -> None:
    """Beides steht als Literal in `Privacy.tsx` und in keiner Sprachdatei.

    Genau die zwei Angaben, die ein Modell auf die Frage "von wann ist die
    Datenschutzerklaerung?" sonst erfindet.
    """
    quelle = (FRONTEND / "pages" / "Privacy.tsx").read_text(encoding="utf-8")
    assert f"lastUpdated: '{korpus.DATENSCHUTZ_STAND}'" in quelle
    assert f"version: '{korpus.DATENSCHUTZ_VERSION}'" in quelle


def test_the_dead_privacy_namespace_never_shows_up() -> None:
    """`de.json` fuehrt ein totes `privacy` neben `privacyPolicy`.

    Es enthaelt "6. Verschluesselte Cloud-Backups (S3)", waehrend
    `privacyPolicy.sections.ai.heading` bereits die 6 traegt. Ein Leser, der
    beide nimmt, meldet zwei Abschnitte 6 und zitiert einen S3-Abschnitt, den
    keine Seite rendert.
    """
    assert "privacy" in korpus.TOTE_NAMENSRAEUME
    text = " ".join(a.text for a in korpus.abschnitte("datenschutz"))
    assert "S3" not in text or "Cloud-Backups (S3)" not in text


@pytest.mark.parametrize(
    "begriff,seite,abschnitt",
    [
        # Der Header steht nur in der Markdown-Fassung und in keiner
        # Sprachdatei — die Frage, an der ein reiner i18n-Leser scheitert.
        ("X-MSM-Hoster-Key", "hoster-api", "authentifizierung"),
        # Fehlercodes sind in i18n nur JSON-Schluessel, nie Text.
        ("port_conflict", "hoster-api", "fehler-und-statuscodes"),
        ("Redirect-URI", "oauth", "create"),
    ],
)
def test_hard_contract_facts_are_findable(begriff: str, seite: str, abschnitt: str) -> None:
    treffer = korpus.suche(begriff)
    assert (seite, abschnitt) in [(t["page"], t["section"]) for t in treffer], begriff


@pytest.mark.parametrize("begriff", ["Gedächtnis", "Gedaechtnis", "gedachtnis"])
def test_the_search_folds_umlauts_and_their_ascii_spelling(begriff: str) -> None:
    """Vier Schluessel der Sprachdatei sind ASCII-umgeschrieben.

    `privacyPolicy.sections.ai.items.memory` sagt "verschluesselt", der Benutzer
    schreibt "verschlüsselt". Ohne Faltung findet die Suche nichts — und "steht
    nichts dazu drin" ist hier eine falsche Aussage ueber die eigene Doku.
    """
    treffer = korpus.suche(begriff, "datenschutz")
    assert "ai" in [t["section"] for t in treffer], begriff


def test_a_section_is_returned_whole_or_marked_as_cut() -> None:
    """Ein gekuerzter Abschnitt ohne Marke laesst das Modell aus einem Text
    schliessen, dessen Ende es nie gesehen hat."""
    for schluessel in korpus.SEITEN:
        for eintrag in korpus.verzeichnis(schluessel)["sections"]:
            ergebnis = korpus.abschnitt(schluessel, eintrag["section"])
            assert ergebnis["total_chars"] == eintrag["chars"]
            if ergebnis["truncated"]:
                assert ergebnis["text"].endswith(korpus.GEKUERZT)
            else:
                assert len(ergebnis["text"]) == ergebnis["total_chars"]


def test_a_search_without_hits_says_what_it_searched(db, regular_user) -> None:
    """Eine leere Trefferliste allein laesst offen, ob nichts drinsteht oder
    nichts gelesen wurde.

    Dieselbe Zusage wie bei `web_search`, das bei Ausfall `available: false`
    meldet statt einer leeren Liste — hier als `found` plus `searched_pages`.
    """
    ergebnis = ai_action_service._execute_global_read_tool(
        db,
        user=regular_user,
        tool_name="search_docs",
        arguments={"query": "zaphod beeblebrox"},
    )
    assert ergebnis["found"] == 0
    assert sorted(ergebnis["searched_pages"]) == sorted(korpus.SEITEN)


def test_the_doc_tools_need_no_extra_permission(db, regular_user) -> None:
    """Dieselben Seiten stehen jedem angemeldeten Benutzer im Panel offen.

    Ein Gate haette ausgerechnet dort gesperrt, wo die Belegpflicht am noetigsten
    ist: bei jemandem, der das Panel noch nicht kennt und deshalb fragt.
    `regular_user` hat keine globalen Rechte.
    """
    verzeichnis = ai_action_service._execute_global_read_tool(
        db, user=regular_user, tool_name="read_docs", arguments={"page": "hoster-api"}
    )
    assert verzeichnis["available"] is True
    assert verzeichnis["sections"]


def test_an_invented_section_is_refused_with_the_real_list(db, regular_user) -> None:
    """Das Modell soll nach einem Fehlgriff weiterarbeiten koennen.

    Eine Ablehnung, die nur "gibt es nicht" sagt, laedt zum naechsten Rateversuch
    ein — die vorhandenen Kennungen mitzugeben beendet die Schleife nach einer
    Runde.
    """
    with pytest.raises(AiActionValidationError) as exc:
        ai_action_service._execute_global_read_tool(
            db,
            user=regular_user,
            tool_name="read_docs",
            arguments={"page": "hoster-api", "section": "gibt-es-nicht"},
        )
    assert "zustandsmodell" in str(exc.value)


def test_an_unreadable_source_is_reported_not_raised(
    db, regular_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Ausfall darf den Zug nicht kosten.

    Ein geworfener Fehler beendet die Werkzeugrunde; das Modell wuerde im
    naechsten Anlauf ohne Beleg antworten. Als Feld kann es den Ausfall
    benennen — genau die Auskunft, die der Benutzer braucht.
    """
    def _kaputt(_schluessel: str):
        raise korpus.DokuNichtVerfuegbar("Quelle nicht lesbar: hoster-api.md")

    monkeypatch.setattr(korpus, "verzeichnis", _kaputt)
    ergebnis = ai_action_service._execute_global_read_tool(
        db, user=regular_user, tool_name="read_docs", arguments={"page": "hoster-api"}
    )
    assert ergebnis["available"] is False
    assert "nicht lesbar" in ergebnis["reason"]


def test_doc_results_do_not_leak_into_the_next_turn() -> None:
    """Ein Doku-Abschnitt ist bis zu 12.000 Zeichen gross und aendert sich nie.

    Stuende er im Folgekontext, verdraengte er die Messungen des Servers — die
    sich sehr wohl aendern. Braucht das Modell ihn erneut, liest es ihn erneut;
    das ist die Belegpflicht, nicht ihr Umweg.
    """
    from services.ai_tool_registry import DOCS_TOOLS

    assert DOCS_TOOLS == {"read_docs", "search_docs"}
    quelle = inspect.getsource(ai_context_service._recent_tool_results)
    assert "DOCS_TOOLS" in quelle


def test_markdown_pages_do_not_claim_a_deep_link_anchor() -> None:
    """Die Abschnittskennung ist **nicht** der Anker der Panel-Seite.

    Die Seiten setzen ihre Anker aus i18n-Schluesseln (`#docs-<key>`,
    `#oauth-docs-<key>`), der Korpus schneidet Markdown an Ueberschriften. Ein
    zusammengesetzter Link waere geraten — also gibt `panel_page` nur die Route
    aus, nie einen Anker.
    """
    for schluessel, seite in korpus.SEITEN.items():
        for eintrag in korpus.verzeichnis(schluessel)["sections"]:
            ergebnis = korpus.abschnitt(schluessel, eintrag["section"])
            assert ergebnis["panel_page"] == seite.route
            assert "#" not in ergebnis["panel_page"]
