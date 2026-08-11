"""Vertragstest zwischen Permission-Katalog und Rollen-Editor.

Der Katalog in ``services/permission_catalog.py`` ist die Single Source of
Truth. Der Rollen-Editor im Frontend zeigt dazu einen deutschen Titel und eine
Beschreibung aus ``PERMISSION_DETAILS`` und faellt, wenn ein Key dort fehlt,
still auf das Backend-Label zurueck.

Genau dieser stille Rueckfall war der Fehler: neu hinzugekommene Rechte wie
``server.credentials.manage`` landeten ohne Beschreibung in einer Sammelgruppe
"Andere Berechtigungen" und zeigten dort den Backend-Rohtext samt
ASCII-Ersatzschreibung ("Zugangsdaten fuer diesen Server zuweisen"). Ein Recht
ohne verstaendliche Erklaerung ist ein Sicherheitsproblem: wer nicht weiss, was
er vergibt, vergibt zu viel.

Dieser Test laeuft im Backend, weil dort die Wahrheit liegt. Er liest die
Frontend-Datei als Text — bewusst kein TypeScript-Parser, sondern zwei enge
regulaere Ausdruecke auf zwei klar abgegrenzten Bloecken.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from services.permission_catalog import ALL_KEYS


ROOT = Path(__file__).resolve().parents[2]
EDITOR = ROOT / "frontend" / "src" / "Singra" / "UI" / "PermissionEditor.tsx"

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


def test_every_permission_key_has_a_frontend_description() -> None:
    """Kein Recht darf ohne eigenen Titel und Beschreibung angezeigt werden."""
    details_block = _block(_editor_source(), "const PERMISSION_DETAILS")
    described = set(re.findall(r"^\s{2}'([a-z0-9_.]+)':\s*\{", details_block, re.MULTILINE))

    missing = sorted(ALL_KEYS - described)

    assert not missing, (
        "Diese Permission-Keys haben keine Beschreibung in PERMISSION_DETAILS "
        f"und wuerden mit dem Backend-Rohtext angezeigt: {missing}"
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


def _permission_texts(key: str) -> tuple[str, ...]:
    """Titel und Beschreibung eines Rechts, so wie der Rollen-Editor sie zeigt.

    Gelesen werden ausdruecklich nur die beiden Textwerte und nicht der ganze
    Block: ein erklaerender Kommentar im Editor darf Woerter enthalten, die dem
    Betreiber nie unter die Augen kommen.
    """
    details_block = _block(_editor_source(), "const PERMISSION_DETAILS")
    eintrag = re.search(
        rf"^  '{re.escape(key)}': \{{\n(.*?)^  \}},",
        details_block,
        re.MULTILINE | re.DOTALL,
    )
    assert eintrag, f"Kein PERMISSION_DETAILS-Eintrag fuer {key}"
    texte = tuple(
        re.findall(r"^\s+(?:title|desc): '(.*)',$", eintrag.group(1), re.MULTILINE)
    )
    assert len(texte) == 2, f"Titel oder Beschreibung fehlt bei {key}: {texte}"
    return texte


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
