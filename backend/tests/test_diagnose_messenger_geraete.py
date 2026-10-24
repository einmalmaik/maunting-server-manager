"""Das Diagnoseskript meldet kein Gerät als „bekommt nichts“, das ankommt.

Am 20.10.2026 lieferte es für ein gesundes erstes Gerät `geisterwarnung`,
sobald ein zweites Gerät freigegeben war: die Zeile hat keine Unterschrift
(niemand gab es, als das Konto nur dieses Gerät hatte), und der Text behauptete
„Es bekommt nichts“. Der Client hält das Gerät aber für vertraut, solange sein
Fingerabdruck in `msm_vertraute_geraete:<userId>` steht — und aus der Datenbank
ist ein Schlüsselwechsel nicht ablesbar, weil `_auffrischen` dabei seit 10/2026
`is_approved` zurücknimmt.

Ein Befund darf deshalb nur sagen: freigegeben ohne Unterschrift. Ob es ankommt,
entscheidet der Fingerabdruck, den das Skript zum Vergleich mitgibt.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SKRIPT = ROOT / "backend" / "scripts" / "diagnose_messenger_geraete.py"

_spec = importlib.util.spec_from_file_location("diagnose_messenger_geraete", SKRIPT)
diagnose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diagnose)


def _geraet(*, freigegeben: bool, unterschrift: str, freigeber: str = "") -> SimpleNamespace:
    """Nur die Felder, die `urteil` liest — ohne Datenbank."""
    return SimpleNamespace(
        is_approved=freigegeben,
        approval_signature=unterschrift,
        approved_by=freigeber,
    )


def test_erstes_geraet_ohne_unterschrift_ist_kein_problem() -> None:
    geraet = _geraet(freigegeben=True, unterschrift="")

    assert diagnose.urteil(geraet, andere_freigegebene=0) == diagnose.URTEIL_ERSTES


def test_freigegeben_ohne_unterschrift_neben_anderem_geraet_ist_nur_ein_kandidat() -> None:
    """Der Fehlerbericht: nach dem Freigeben eines zweiten Geräts sah das erste aus wie ein Geist."""
    geraet = _geraet(freigegeben=True, unterschrift="")

    # Wörtlich erwartet, nicht über die Konstante: am alten Stand lautete das
    # Urteil „geisterwarnung“, und der Test soll das zeigen, nicht daran
    # scheitern, dass eine Konstante fehlt.
    urteil = diagnose.urteil(geraet, andere_freigegebene=1)

    assert urteil == "ohne-unterschrift"
    # Der Text darf keine Zustellung behaupten.
    text = diagnose.URTEIL_TEXT[urteil]
    assert "bekommt nichts" not in text
    assert "Fingerabdruck" in text


def test_unterschriebene_freigabe_ist_gedeckt() -> None:
    geraet = _geraet(freigegeben=True, unterschrift="sig", freigeber="geraet-a")

    assert diagnose.urteil(geraet, andere_freigegebene=2) == diagnose.URTEIL_GEDECKT


def test_wartendes_geraet_bleibt_wartend() -> None:
    geraet = _geraet(freigegeben=False, unterschrift="")

    assert diagnose.urteil(geraet, andere_freigegebene=3) == diagnose.URTEIL_WARTEND
