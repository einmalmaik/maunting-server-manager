"""Gewuenschte Konfigurationswerte ueberleben den Spielprozess.

**Der Anlass, woertlich gemessen.** Auf Server 107 stand am 15.08.2026 ein
ausgefuehrter Konfigurationsvorschlag (`status=succeeded`). Am 19.08. war der
Wert nicht mehr in der Datei; `GameUserSettings.ini` trug eine mtime von 16:16,
zu der niemand sie editiert hatte. ARK haelt seine Einstellungen im Speicher und
schreibt die Datei beim Autosave vollstaendig neu — alles, was der laufende
Prozess nicht kennt, verwirft er dabei.

Die KI hatte also nicht gelogen. Sie hatte in eine Datei geschrieben, deren
Eigentuemer gerade lief.

**Warum nicht einfach den Server stoppen.** Weil das die Ursache nicht trifft.
Die Mehrheit der Spiele liest ihre Konfiguration beim Start und schreibt sie nie
zurueck; dort ist eine Stoppflicht reine Schikane. Und bei ARK reicht sie nicht:
Der Wert ueberlebt zwar den einen Neustart, aber nicht den naechsten Autosave,
wenn der Prozess ihn nicht kennt.

**Die Loesung ohne Spielwissen.** Der gewuenschte Wert wird am Server
gespeichert und vor **jedem** Start in die Datei geschrieben. Ob das Spiel
zurueckschreibt, muss dann niemand mehr wissen — beim naechsten Start steht der
Wert wieder da. Keine Liste, die gepflegt werden muss, kein Spiel, das jemand
vergessen kann.
"""
from __future__ import annotations

import json

import pytest

from services.ai_action_errors import AiActionValidationError
from services import server_config_wishes as wuensche


def test_leerer_speicher_liefert_leere_liste():
    assert wuensche.lese(None) == []
    assert wuensche.lese("") == []


def test_wunsch_wird_gespeichert_und_gelesen():
    roh = wuensche.setze(
        None,
        datei="ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini",
        eintraege=[("ServerSettings", "TamingSpeedMultiplier", "5.0")],
    )

    gelesen = wuensche.lese(roh)
    assert len(gelesen) == 1
    assert gelesen[0].sektion == "ServerSettings"
    assert gelesen[0].schluessel == "TamingSpeedMultiplier"
    assert gelesen[0].wert == "5.0"


def test_gleicher_schluessel_wird_ersetzt_statt_gedoppelt():
    """Sonst wachsen die Wuensche bei jeder Aenderung, und beim Start gewinnt
    ein zufaelliger Eintrag."""
    roh = wuensche.setze(
        None,
        datei="a.ini",
        eintraege=[("ServerSettings", "XPMultiplier", "2.0")],
    )
    roh = wuensche.setze(
        roh,
        datei="a.ini",
        eintraege=[("ServerSettings", "XPMultiplier", "3.0")],
    )

    gelesen = wuensche.lese(roh)
    assert len(gelesen) == 1
    assert gelesen[0].wert == "3.0"


def test_gleicher_schluessel_in_anderer_datei_ist_ein_eigener_wunsch():
    roh = wuensche.setze(None, datei="a.ini", eintraege=[("S", "K", "1")])
    roh = wuensche.setze(roh, datei="b.ini", eintraege=[("S", "K", "2")])

    assert len(wuensche.lese(roh)) == 2


def test_sektionsvergleich_ist_schreibweisenunabhaengig():
    """ASA schreibt die Sektion klein, die Wikis gross — sonst entstehen zwei
    Wuensche fuer denselben Wert, und beim Start gewinnt der letzte."""
    roh = wuensche.setze(
        None, datei="a.ini", eintraege=[("/script/shootergame.shootergamemode", "K", "1")]
    )
    roh = wuensche.setze(
        roh, datei="a.ini", eintraege=[("/Script/ShooterGame.ShooterGameMode", "K", "2")]
    )

    gelesen = wuensche.lese(roh)
    assert len(gelesen) == 1
    assert gelesen[0].wert == "2"


def test_wunsch_kann_entfernt_werden():
    roh = wuensche.setze(None, datei="a.ini", eintraege=[("S", "K", "1")])

    roh = wuensche.entferne(roh, datei="a.ini", sektion="S", schluessel="K")

    assert wuensche.lese(roh) == []


def test_entfernen_eines_unbekannten_wunsches_ist_kein_fehler():
    roh = wuensche.setze(None, datei="a.ini", eintraege=[("S", "K", "1")])

    assert len(wuensche.lese(wuensche.entferne(roh, datei="x.ini", sektion="S", schluessel="K"))) == 1


def test_passwortwerte_werden_abgewiesen():
    """Die Geheimnisgrenze gilt hier wie ueberall sonst.

    Ohne sie waere der Wunschspeicher der Umweg: ein Passwort, das
    ``propose_config_patch`` abweist, laege hier dauerhaft im Klartext in der
    Datenbank und wuerde bei jedem Start neu geschrieben.
    """
    with pytest.raises(AiActionValidationError):
        wuensche.setze(
            None,
            datei="a.ini",
            eintraege=[("ServerSettings", "ServerAdminPassword", "geheim")],
        )


def test_pfad_ausserhalb_des_serververzeichnisses_wird_abgewiesen():
    for boeser_pfad in ("../../etc/passwd", "/etc/passwd", "a/../../b.ini"):
        with pytest.raises(AiActionValidationError):
            wuensche.setze(None, datei=boeser_pfad, eintraege=[("S", "K", "1")])


def test_anzahl_ist_begrenzt():
    """Ein unbegrenzter Speicher waechst still, bis der Start langsam wird.

    Die Grenze ist wie bei den Guardian-Stellschrauben eine Zahl, kein Gefuehl.
    """
    roh = None
    for i in range(wuensche.MAX_WUENSCHE):
        roh = wuensche.setze(roh, datei="a.ini", eintraege=[("S", f"K{i}", "1")])

    with pytest.raises(AiActionValidationError):
        wuensche.setze(roh, datei="a.ini", eintraege=[("S", "zuviel", "1")])


def test_kaputter_speicher_liefert_leere_liste_statt_zu_werfen():
    """Ein unlesbarer Wunschspeicher darf den Serverstart nicht verhindern.

    Der Start ist der falsche Ort fuer eine Ausnahme: der Benutzer will spielen,
    und ein defekter Zusatzwunsch ist kein Grund, ihm den Server zu verweigern.
    """
    assert wuensche.lese("kein json") == []
    assert wuensche.lese(json.dumps({"nicht": "eine liste"})) == []
    assert wuensche.lese(json.dumps([{"unvollstaendig": True}])) == []


def test_nur_ini_dateien():
    """Der Durchsetzer kann heute nur INI. Ein Wunsch fuer eine JSON-Datei
    waere ein Versprechen, das beim Start still gebrochen wird."""
    with pytest.raises(AiActionValidationError):
        wuensche.setze(None, datei="config.json", eintraege=[("S", "K", "1")])
