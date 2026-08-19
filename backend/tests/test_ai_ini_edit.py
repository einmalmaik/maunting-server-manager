"""Sektionsbewusstes Setzen von INI-Werten — die Tests zuerst.

Der Anlass ist gemessen, nicht theoretisch. Am 18.08.2026 hat ein ausgefuehrter
Patch auf Server 107 einen **zweiten** ``[ServerSettings]``-Block ans Dateiende
gehaengt. ARK liest nur den ersten. Die Werte waren richtig, die Wirkung war
null, und im Diff sah alles korrekt aus.

Zweiter gemessener Fehlschlag derselben Wurzel: die echte
``GameUserSettings.ini`` hat CRLF-Zeilenenden. Ein mehrzeiliger Suchtext mit
``\\n`` findet darin deterministisch nichts.

Beides sind Fehler der *Textsuche als Werkzeug*. Eine Funktion, die Sektion und
Schluessel kennt, kann sie nicht machen.
"""
from __future__ import annotations

import pytest

from services.ai_action_errors import AiActionValidationError
from services.ai_ini_edit import ini_setzen


def test_setzt_in_vorhandene_sektion_statt_neue_anzuhaengen():
    """Der gemessene Fehler: ein zweiter [ServerSettings]-Block am Dateiende."""
    datei = "[ServerSettings]\r\nXPMultiplier=1.0\r\n\r\n[Startup]\r\nFoo=1\r\n"

    ergebnis = ini_setzen(datei, "ServerSettings", "TamingSpeedMultiplier", "4.0")

    assert ergebnis.count("[ServerSettings]") == 1
    assert "TamingSpeedMultiplier=4.0" in ergebnis
    # Der neue Schluessel gehoert VOR die naechste Sektion, sonst steht er in
    # [Startup] und wirkt wieder nicht.
    assert ergebnis.index("TamingSpeedMultiplier") < ergebnis.index("[Startup]")


def test_erhaelt_crlf_zeilenenden():
    """B3: die echten ASA-Dateien haben CRLF, das Modell denkt in LF."""
    datei = "[ServerSettings]\r\nXPMultiplier=1.0\r\n"

    ergebnis = ini_setzen(datei, "ServerSettings", "HarvestAmountMultiplier", "2.0")

    assert "\r\n" in ergebnis
    # Kein einziges nacktes LF: sonst hat die Datei gemischte Zeilenenden und
    # manche Parser lesen ab dort nichts mehr.
    assert "\n" not in ergebnis.replace("\r\n", "")


def test_erhaelt_lf_zeilenenden():
    """Gegenprobe: eine Unix-Datei darf nicht auf CRLF umgestellt werden."""
    datei = "[ServerSettings]\nXPMultiplier=1.0\n"

    ergebnis = ini_setzen(datei, "ServerSettings", "HarvestAmountMultiplier", "2.0")

    assert "\r" not in ergebnis


def test_ueberschreibt_vorhandenen_schluessel_statt_ihn_zu_doppeln():
    datei = "[ServerSettings]\r\nXPMultiplier=1.0\r\n"

    ergebnis = ini_setzen(datei, "ServerSettings", "XPMultiplier", "2.0")

    assert ergebnis.count("XPMultiplier=") == 1
    assert "XPMultiplier=2.0" in ergebnis


def test_kommentare_bleiben_stehen():
    datei = "; wichtig\r\n[ServerSettings]\r\nXPMultiplier=1.0\r\n"

    ergebnis = ini_setzen(datei, "ServerSettings", "XPMultiplier", "2.0")

    assert "; wichtig" in ergebnis


def test_findet_sektion_unabhaengig_von_gross_kleinschreibung():
    """ASA schreibt [/script/shootergame.shootergamemode] klein, die Wikis gross.

    Ohne diese Toleranz legt die KI eine zweite Sektion an, die sich von der
    ersten nur in der Schreibweise unterscheidet — derselbe Fehlschlag wie oben,
    nur schwerer zu sehen.
    """
    datei = "[/script/shootergame.shootergamemode]\r\nFoo=1\r\n"

    ergebnis = ini_setzen(
        datei, "/Script/ShooterGame.ShooterGameMode", "BabyMatureSpeedMultiplier", "10"
    )

    assert ergebnis.count("[") == 1
    assert "BabyMatureSpeedMultiplier=10" in ergebnis


def test_legt_fehlende_sektion_am_ende_an():
    datei = "[ServerSettings]\r\nXPMultiplier=1.0\r\n"

    ergebnis = ini_setzen(datei, "SessionSettings", "SessionName", "MauntARK")

    assert "[SessionSettings]" in ergebnis
    assert "SessionName=MauntARK" in ergebnis


def test_leere_datei_wird_angelegt():
    ergebnis = ini_setzen("", "ServerSettings", "XPMultiplier", "2.0")

    assert ergebnis.startswith("[ServerSettings]")
    assert "XPMultiplier=2.0" in ergebnis


def test_erster_treffer_gewinnt_spaetere_duplikate_bleiben():
    """UE-INIs nutzen Duplikat-Keys absichtlich (Mod-Listen).

    Wir aendern das erste Vorkommen und lassen die uebrigen in Ruhe — dieselbe
    Regel wie in ``games/ini_utils.py``, damit Blueprint und KI eine Datei nicht
    unterschiedlich interpretieren.
    """
    datei = "[ServerSettings]\r\nMod=1\r\nMod=2\r\n"

    ergebnis = ini_setzen(datei, "ServerSettings", "Mod", "9")

    assert "Mod=9" in ergebnis
    assert "Mod=2" in ergebnis


def test_passwortwerte_werden_abgewiesen():
    """Die Grenze aus DATEIEN gilt hier genauso.

    Sonst waere das neue Werkzeug schlicht der Umweg um eine bestehende
    Invariante: was ``propose_config_patch`` abweist, darf hier nicht durch.
    """
    with pytest.raises(AiActionValidationError):
        ini_setzen("[ServerSettings]\r\n", "ServerSettings", "ServerAdminPassword", "geheim")


def test_passwort_auch_im_wert_abgewiesen():
    with pytest.raises(AiActionValidationError):
        ini_setzen(
            "[ServerSettings]\r\n", "ServerSettings", "Motd", "ServerPassword=hunter2"
        )


def test_sektionsname_mit_klammer_wird_abgewiesen():
    """Eine Sektion, die selbst eine Klammer traegt, koennte eine zweite
    Sektionszeile in die Datei schreiben — Struktur-Injektion."""
    with pytest.raises(AiActionValidationError):
        ini_setzen("", "Server]\r\n[Other", "Key", "1")


def test_schluessel_mit_zeilenumbruch_wird_abgewiesen():
    with pytest.raises(AiActionValidationError):
        ini_setzen("", "ServerSettings", "Key\r\nInjected", "1")


def test_wert_mit_zeilenumbruch_wird_abgewiesen():
    """Ohne diese Grenze schreibt ein einziger Wert beliebig viele Zeilen —
    inklusive einer neuen Sektion."""
    with pytest.raises(AiActionValidationError):
        ini_setzen("", "ServerSettings", "Motd", "harmlos\r\n[ServerSettings]\r\nXP=99")


def test_gleicher_wert_bleibt_unveraendert():
    """Setzen ist idempotent: derselbe Wert erzeugt keine Aenderung.

    Das traegt die spaetere Durchsetzung beim Serverstart — sie laeuft bei
    jedem Start und darf die Datei nicht bei jedem Mal anfassen.
    """
    datei = "[ServerSettings]\r\nXPMultiplier=2.0\r\n"

    assert ini_setzen(datei, "ServerSettings", "XPMultiplier", "2.0") == datei
