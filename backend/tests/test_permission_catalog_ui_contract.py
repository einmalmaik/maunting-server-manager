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
