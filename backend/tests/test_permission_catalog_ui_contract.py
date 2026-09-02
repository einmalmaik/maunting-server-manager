"""Vertragstest zwischen Permission-Katalog und Rollen-Editor.

Der Katalog in ``services/permission_catalog.py`` ist die Single Source of
Truth. Der Rollen-Editor im Frontend zeigt dazu einen Titel und eine
Beschreibung aus dem Abschnitt ``permissionDetails`` der Sprachdateien und
fällt, wenn ein Key dort fehlt, still auf das Backend-Label zurück.

Genau dieser stille Rueckfall war der Fehler: neu hinzugekommene Rechte wie
``server.credentials.manage`` landeten ohne Beschreibung in einer Sammelgruppe
"Andere Berechtigungen" und zeigten dort den Backend-Rohtext samt
ASCII-Ersatzschreibung ("Zugangsdaten fuer diesen Server zuweisen"). Ein Recht
ohne verstaendliche Erklaerung ist ein Sicherheitsproblem: wer nicht weiss, was
er vergibt, vergibt zu viel.

Dieser Test laeuft im Backend, weil dort die Wahrheit liegt. Er liest die
Texte als JSON aus den Sprachdateien und die Gruppierung als Text aus dem
Editor — bewusst kein TypeScript-Parser, sondern ein enger regulärer Ausdruck
auf einem klar abgegrenzten Block.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from services.permission_catalog import ALL_KEYS


ROOT = Path(__file__).resolve().parents[2]
EDITOR = ROOT / "frontend" / "src" / "Singra" / "UI" / "PermissionEditor.tsx"
LOCALES = ROOT / "frontend" / "src" / "locales"

# Nur diese beiden Sprachdateien tragen die Rechtetexte; alle anderen Sprachen
# fallen im Editor auf sie zurück.
SPRACHEN = ("de", "en")

# Die im Frontend absichtlich anders geschriebenen Zeichen. Der Katalog darf
# keine ASCII-Ersatzschreibung mehr enthalten, weil sie im Fallback sichtbar
# wird.
FORBIDDEN_TRANSLITERATIONS = ("loeschen", "aendern", "ausfuehren", "fuer ")


def _editor_source() -> str:
    assert EDITOR.is_file(), f"Rollen-Editor nicht gefunden: {EDITOR}"
    return EDITOR.read_text(encoding="utf-8")


def _block(source: str, start_marker: str) -> str:
    """Schneidet den Text ab einem Marker bis zur naechsten Top-Level-Deklaration."""
    start = source.index(start_marker)
    rest = source[start + len(start_marker):]
    end = rest.find("\nconst ")
    return rest if end < 0 else rest[:end]


def _detail_schluessel(key: str) -> str:
    """Spiegelt ``detailSchluessel`` im Editor.

    Der Punkt ist bei i18next ein Ebenentrenner, deshalb hält der Unterstrich
    den Übersetzungsschlüssel flach: ``permissionDetails.server_files_read``.
    """
    return key.replace(".", "_")


def _permission_details(sprache: str) -> dict[str, dict[str, str]]:
    quelle = LOCALES / f"{sprache}.json"
    assert quelle.is_file(), f"Sprachdatei nicht gefunden: {quelle}"
    daten = json.loads(quelle.read_text(encoding="utf-8"))
    return daten.get("permissionDetails", {})


def test_every_permission_key_has_a_frontend_description() -> None:
    """Kein Recht darf ohne eigenen Titel und Beschreibung angezeigt werden."""
    # Ohne diese Zeile prüfte der Test irgendwann eine Quelle, die der Editor
    # gar nicht mehr liest — und bliebe grün, während die Oberfläche wieder
    # rohe Backend-Labels zeigt. Genau so ist er schon einmal blind geworden.
    assert "permissionDetails." in _editor_source(), (
        "Der Rollen-Editor holt seine Texte nicht mehr aus den Sprachdateien; "
        "dann prüft dieser Test die falsche Quelle."
    )

    for sprache in SPRACHEN:
        details = _permission_details(sprache)
        missing = sorted(
            key
            for key in ALL_KEYS
            if not (
                details.get(_detail_schluessel(key), {}).get("title")
                and details.get(_detail_schluessel(key), {}).get("desc")
            )
        )

        assert not missing, (
            f"Diese Permission-Keys haben in {sprache}.json keinen Titel und "
            f"keine Beschreibung und würden mit dem Backend-Rohtext "
            f"angezeigt: {missing}"
        )


def test_every_permission_key_is_assigned_to_a_group() -> None:
    """Kein Recht darf in der Sammelgruppe 'Andere Berechtigungen' landen."""
    groups_block = _block(_editor_source(), "const SUBGROUPS")
    grouped = set(re.findall(r"'([a-z0-9_.]+)'", groups_block))

    missing = sorted(ALL_KEYS - grouped)

    assert not missing, (
        "Diese Permission-Keys sind keiner SUBGROUP zugeordnet und erscheinen "
        f"unter 'Andere Berechtigungen': {missing}"
    )


@pytest.mark.parametrize("transliteration", FORBIDDEN_TRANSLITERATIONS)
def test_catalog_labels_use_real_umlauts(transliteration: str) -> None:
    """Die Labels sind sichtbarer Produkttext und brauchen echte Umlaute."""
    offenders = sorted(
        definition.key
        for definition in _all_definitions()
        if transliteration in definition.label
    )

    assert not offenders, (
        f"Label enthaelt die Ersatzschreibung {transliteration!r}: {offenders}"
    )


def _all_definitions():
    from services.permission_catalog import GLOBAL_PERMISSIONS, SERVER_PERMISSIONS

    return (*GLOBAL_PERMISSIONS, *SERVER_PERMISSIONS)


# ── server.update: was ein Recht verspricht, muss es auch oeffnen ─────

# Woerter, die eine Wirkung auf die Spieldateien zusagen. Die oeffnet
# `server.install`, nicht `server.update`.
INSTALL_VERSPRECHEN = ("Reinstall", "Spieldatei")


def _permission_texts(key: str) -> tuple[str, str]:
    """Titel und Beschreibung eines Rechts, so wie der Rollen-Editor sie zeigt.

    Bewusst nur die deutsche Fassung: die Wortlisten oben sind deutsch, und das
    Backend-Label daneben ist es auch. Eine auf ``en.json`` nur halb greifende
    Prüfung wäre schlechter als keine, weil sie Deckung vortäuschte.
    """
    eintrag = _permission_details("de").get(_detail_schluessel(key))
    assert eintrag, f"Kein permissionDetails-Eintrag für {key} in de.json"
    return eintrag["title"], eintrag["desc"]


def test_server_update_verspricht_nur_die_outbound_webhooks() -> None:
    """Das Recht heisst `server.update` und aktualisiert trotzdem keine Spieldateien.

    Durchgesetzt wird es an genau einer Stelle: routers/webhooks_outbound.py.
    Die Spieldateien holt POST /servers/{id}/install, und dort wird
    `server.install` geprueft. Label und Editortext sagten aber "Reinstall/
    Update" bzw. "Aktualisieren der Spieldateien" zu. Wer daraufhin eine
    Wartungsrolle baute, vergab ein Recht ohne die erwartete Wirkung.

    Der Test prueft beide Richtungen. Faengt jemand an, `server.update` auch
    anderswo durchzusetzen, faellt er hier auf und muss die Texte wieder
    weiten. Beschriftung und Durchsetzung bleiben so aneinander gebunden -
    genau das war hier auseinandergelaufen, ohne dass es jemand bemerkte.
    """
    router_mit_recht = sorted(
        pfad.name
        for pfad in (ROOT / "backend" / "routers").glob("*.py")
        if '"server.update"' in pfad.read_text(encoding="utf-8")
    )

    assert router_mit_recht == ["webhooks_outbound.py"], (
        "server.update wird jetzt an anderer Stelle durchgesetzt "
        f"({router_mit_recht}) - dann muessen Katalog-Label und Rollen-Editor "
        "wieder beschreiben, was das Recht tatsaechlich oeffnet."
    )

    label = next(d.label for d in _all_definitions() if d.key == "server.update")

    for text in (label, *_permission_texts("server.update")):
        assert "Webhook" in text, (
            f"server.update benennt seine einzige Wirkung nicht: {text!r}"
        )
        for wort in INSTALL_VERSPRECHEN:
            assert wort not in text, (
                f"{wort!r} sagt eine Wirkung auf die Spieldateien zu; die oeffnet "
                f"aber server.install: {text!r}"
            )
