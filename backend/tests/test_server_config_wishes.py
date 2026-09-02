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
import re

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


def test_das_dateiformat_entscheidet_nicht():
    """Die Endungsliste ist ersatzlos weg — auf Vorgabe des Betreibers.

    Eine erste Fassung liess dauerhafte Werte nur fuer ``.ini``/``.cfg``/``.conf``
    zu. Das war dieselbe Sorte Einschraenkung, die MSM schon einmal abgeraeumt
    hat: eine Liste, die gepflegt werden muss, versagt still. Ein vergessenes
    Format wirft keinen Fehler — es laesst nur irgendwann wieder eine Aenderung
    verschwinden, und niemand sieht den Zusammenhang.

    Was ein Format braucht, ist kein Eintrag, sondern ein passender Anker:
    Sektion und Schluessel bei INI, exakter Text bei allem anderen.
    """
    # INI-Anker in einer Datei mit fremder Endung: erlaubt. Ob er passt, zeigt
    # der Durchsetzer, nicht eine Endungstabelle.
    wuensche.setze(None, datei="config.json", eintraege=[("S", "K", "1")])
    wuensche.setze(None, datei="server.properties", eintraege=[("S", "K", "1")])
    # Und der Textanker gilt ohnehin ueberall.
    wuensche.setze_text(None, datei="Data/buffs.xml", ersetzungen=[("a", "b")])


# ── Jedes Dateiformat, nicht nur INI ──────────────────────────────────────


def test_textwunsch_gilt_fuer_jedes_format():
    """Die Vorgabe des Betreibers: ausnahmslos jedes Dateiformat.

    Eine erste Fassung nahm nur INI-artige Endungen an. Das war dieselbe Sorte
    Einschraenkung eine Ebene tiefer — Spiele schreiben auch XML, JSON und
    YAML beim Start zurueck, und wer eine Endungsliste pflegen muss, vergisst
    sie. Entscheidend ist nicht das Format, sondern der Anker.
    """
    for pfad in ("Data/buffs.xml", "config.json", "settings.yaml",
                 "server.properties", "mods/init.lua", "irgendwas.dat"):
        roh = wuensche.setze_text(
            None, datei=pfad, ersetzungen=[("<xp>1.0</xp>", "<xp>2.0</xp>")]
        )
        gelesen = wuensche.lese(roh)
        assert len(gelesen) == 1, pfad
        assert gelesen[0].art == "text"


def test_textwunsch_wird_durchgesetzt(tmp_path):
    """Der gemessene Vorfall, uebertragen auf XML."""
    from services.server_config_wishes import wuensche_durchsetzen

    class _S:
        id = 1

    server = _S()
    server.install_dir = str(tmp_path)
    server.node = None
    (tmp_path / "Data").mkdir()
    datei = tmp_path / "Data/buffs.xml"
    datei.write_text('<buffs>\n  <buff name="xp" value="1.0"/>\n</buffs>\n', encoding="utf-8")

    server.config_wishes_json = wuensche.setze_text(
        None, datei="Data/buffs.xml",
        ersetzungen=[('<buff name="xp" value="1.0"/>', '<buff name="xp" value="3.0"/>')],
    )

    wuensche_durchsetzen(server)
    assert '<buff name="xp" value="3.0"/>' in datei.read_text(encoding="utf-8")

    # Das Spiel schreibt beim Start zurueck — der naechste Start stellt es her.
    datei.write_text('<buffs>\n  <buff name="xp" value="1.0"/>\n</buffs>\n', encoding="utf-8")
    wuensche_durchsetzen(server)
    assert '<buff name="xp" value="3.0"/>' in datei.read_text(encoding="utf-8")


def test_textwunsch_ist_idempotent(tmp_path):
    """Steht das Ziel schon da, wird die Datei nicht angefasst.

    Sonst bewegte jeder Start die mtime — und genau daran haengt die Diagnose,
    mit der der urspruengliche Fehler gefunden wurde.
    """
    from services.server_config_wishes import wuensche_durchsetzen

    class _S:
        id = 1

    server = _S()
    server.install_dir = str(tmp_path)
    server.node = None
    datei = tmp_path / "c.json"
    datei.write_text('{"xp": 2.0}\n', encoding="utf-8")
    server.config_wishes_json = wuensche.setze_text(
        None, datei="c.json", ersetzungen=[('"xp": 1.0', '"xp": 2.0')]
    )

    vorher = datei.stat().st_mtime_ns
    wuensche_durchsetzen(server)
    assert datei.stat().st_mtime_ns == vorher


def test_mehrdeutiger_anker_wird_nicht_geraten(tmp_path):
    """`value="1"` kommt in einer XML-Konfiguration hundertfach vor.

    Wuerde die erste Fundstelle gewinnen, aendert der Durchsetzer bei jedem
    Start etwas anderes als gemeint — schlimmer als gar nichts zu tun.
    """
    from services.server_config_wishes import wuensche_durchsetzen

    class _S:
        id = 1

    server = _S()
    server.install_dir = str(tmp_path)
    server.node = None
    datei = tmp_path / "c.xml"
    vorher = '<a v="1"/>\n<b v="1"/>\n'
    datei.write_text(vorher, encoding="utf-8")
    server.config_wishes_json = wuensche.setze_text(
        None, datei="c.xml", ersetzungen=[('v="1"', 'v="9"')]
    )

    wuensche_durchsetzen(server)

    assert datei.read_text(encoding="utf-8") == vorher


def test_textwunsch_weist_passwoerter_ab():
    with pytest.raises(AiActionValidationError):
        wuensche.setze_text(
            None, datei="c.xml",
            ersetzungen=[("<pw>alt</pw>", "<ServerAdminPassword>geheim</ServerAdminPassword>")],
        )


def test_leerer_suchtext_wird_abgewiesen():
    """Ein leerer Anker kommt unendlich oft vor."""
    with pytest.raises(AiActionValidationError):
        wuensche.setze_text(None, datei="c.xml", ersetzungen=[("", "x")])


def test_wiederholtes_aendern_haengt_die_wuensche_nicht_aneinander():
    """1.0 -> 2.0, spaeter 2.0 -> 3.0: der zweite loest den ersten ab.

    Ohne die Ablaufkette staenden beide Wuensche nebeneinander: der erste
    suchte 1.0 (nicht mehr da), der zweite 2.0. Der Speicher liefe voll, und
    das Konsolenlog meldete bei jedem Start einen unerfuellbaren Wunsch.
    """
    roh = wuensche.setze_text(None, datei="c.xml", ersetzungen=[("xp=1.0", "xp=2.0")])
    roh = wuensche.setze_text(roh, datei="c.xml", ersetzungen=[("xp=2.0", "xp=3.0")])

    gelesen = wuensche.lese(roh)
    assert len(gelesen) == 1
    assert gelesen[0].finde == "xp=2.0"
    assert gelesen[0].setze == "xp=3.0"


def test_alte_eintraege_ohne_art_bleiben_lesbar():
    """Die erste Fassung kannte kein `art`-Feld.

    Ein Server, der schon Wuensche hat, darf sie durch das Feature-Update
    nicht verlieren — deshalb ist INI der Vorgabewert und kein Pflichtfeld.
    """
    alt = json.dumps([
        {"datei": "a.ini", "sektion": "S", "schluessel": "K", "wert": "1"}
    ])

    gelesen = wuensche.lese(alt)

    assert len(gelesen) == 1
    assert gelesen[0].art == "ini"
    assert gelesen[0].schluessel == "K"


def test_textwunsch_als_patch_ist_literal_escaped():
    """Der Regex fuer die entfernte Node kommt nie vom Modell.

    `re.escape` macht aus dem exakten Suchtext ein literales Muster: keine
    Zeichenklasse, kein Quantor, kein ReDoS. Ohne das waere ein Suchtext wie
    `(a+)+$` auf einer entfernten Node eine Rechenfalle im Startpfad.
    """
    roh = wuensche.setze_text(
        None, datei="c.xml", ersetzungen=[('<v x="1.0"/>', '<v x="2.0"/>')]
    )

    patch = wuensche.als_patches(wuensche.lese(roh))[0]

    assert patch["type"] == "regex"
    assert patch["regex"] == re.escape('<v x="1.0"/>')
    # Der Punkt ist escaped — sonst traefe er jedes Zeichen.
    assert r"\." in str(patch["regex"])


def test_ersatztext_mit_backslash_bleibt_woertlich():
    """`\\1` waere in `re.sub` ein Rueckwaertsverweis, kein Text.

    Ein Wert wie `C:\\1` wuerde unterwegs zu etwas anderem — auf der
    entfernten Node, also genau dort, wo es niemand sieht.
    """
    roh = wuensche.setze_text(None, datei="c.ini", ersetzungen=[("p=alt", r"p=C:\1")])

    patch = wuensche.als_patches(wuensche.lese(roh))[0]

    assert patch["value"] == r"p=C:\\1"
