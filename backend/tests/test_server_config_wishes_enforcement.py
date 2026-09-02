"""Die gewuenschten Werte stehen nach jedem Start wieder da.

Das ist der Test, der die eigentliche Zusage prueft. Der Wunschspeicher allein
haelt nur eine Absicht fest; erst die Durchsetzung beim Start macht daraus die
Eigenschaft, die der Betreiber verlangt hat: *die Aenderung bleibt*.

Der gemessene Vorfall dahinter: Server 107, Vorschlag am 15.08.2026 ausgefuehrt,
am 19.08. war der Wert weg. Der laufende ARK-Prozess hatte
``GameUserSettings.ini`` neu geschrieben. Genau dieser Verlauf wird hier
nachgestellt — Wert setzen, Datei vom "Spiel" ueberschreiben lassen, starten,
Wert muss wieder dastehen.
"""
from __future__ import annotations

from pathlib import Path

from services import server_config_wishes as wuensche
from services.server_config_wishes import wuensche_durchsetzen


class _Server:
    """Nur die Felder, die der Durchsetzer anfasst."""

    def __init__(self, install_dir: str, config_wishes_json: str | None, node=None):
        self.id = 1
        self.install_dir = install_dir
        self.config_wishes_json = config_wishes_json
        self.node = node


class _Node:
    def __init__(self, is_local: bool):
        self.is_local = is_local


def _ark_datei(basis: Path) -> Path:
    ziel = basis / "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    return ziel


def test_ueberschriebener_wert_steht_nach_dem_start_wieder_da(tmp_path):
    """Der gemessene Vorfall, nachgestellt."""
    datei = _ark_datei(tmp_path)
    pfad = "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
    datei.write_bytes(b"[ServerSettings]\r\nTamingSpeedMultiplier=5.0\r\n")

    server = _Server(
        str(tmp_path),
        wuensche.setze(None, datei=pfad, eintraege=[("ServerSettings", "TamingSpeedMultiplier", "5.0")]),
    )

    # Das Spiel schreibt die Datei beim Autosave neu und verwirft den Wert.
    datei.write_bytes(b"[ServerSettings]\r\nXPMultiplier=1.0\r\n")
    assert b"TamingSpeedMultiplier" not in datei.read_bytes()

    wuensche_durchsetzen(server)

    inhalt = datei.read_bytes()
    assert b"TamingSpeedMultiplier=5.0" in inhalt
    # Und der Wert, den das Spiel selbst geschrieben hat, bleibt stehen.
    assert b"XPMultiplier=1.0" in inhalt


def test_zeilenenden_der_datei_bleiben(tmp_path):
    datei = _ark_datei(tmp_path)
    pfad = "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
    datei.write_bytes(b"[ServerSettings]\r\nXPMultiplier=1.0\r\n")

    server = _Server(
        str(tmp_path),
        wuensche.setze(None, datei=pfad, eintraege=[("ServerSettings", "Taming", "5.0")]),
    )
    wuensche_durchsetzen(server)

    roh = datei.read_bytes()
    assert b"\r\n" in roh
    assert roh.replace(b"\r\n", b"").count(b"\n") == 0


def test_ohne_wuensche_bleibt_die_datei_unberuehrt(tmp_path):
    """Byte-genau unberuehrt: der Durchsetzer laeuft bei jedem Start, auch auf
    Servern, die ihn nie benutzt haben."""
    datei = _ark_datei(tmp_path)
    vorher = b"[ServerSettings]\r\nXPMultiplier=1.0\r\n"
    datei.write_bytes(vorher)

    wuensche_durchsetzen(_Server(str(tmp_path), None))

    assert datei.read_bytes() == vorher


def test_erfuellter_wunsch_fasst_die_datei_nicht_an(tmp_path):
    """Idempotenz mit Nachweis ueber die mtime.

    Wichtig, weil der Durchsetzer bei jedem Start laeuft: ein Schreibvorgang
    ohne Aenderung wuerde bei jedem Start die Datei-mtime bewegen und damit
    jede spaetere Diagnose ("wer hat die Datei zuletzt angefasst?") wertlos
    machen — genau die Diagnose, mit der dieser Fehler gefunden wurde.
    """
    datei = _ark_datei(tmp_path)
    pfad = "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
    datei.write_bytes(b"[ServerSettings]\r\nTaming=5.0\r\n")
    server = _Server(
        str(tmp_path),
        wuensche.setze(None, datei=pfad, eintraege=[("ServerSettings", "Taming", "5.0")]),
    )

    vorher = datei.stat().st_mtime_ns
    wuensche_durchsetzen(server)

    assert datei.stat().st_mtime_ns == vorher


def test_fehlende_datei_wird_angelegt(tmp_path):
    """Ein Wunsch fuer eine Datei, die es noch nicht gibt, ist der Normalfall
    beim frisch installierten Server."""
    pfad = "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
    server = _Server(
        str(tmp_path),
        wuensche.setze(None, datei=pfad, eintraege=[("ServerSettings", "Taming", "5.0")]),
    )

    wuensche_durchsetzen(server)

    assert (tmp_path / pfad).read_text(encoding="utf-8").startswith("[ServerSettings]")


def test_symlink_wird_nicht_beschrieben(tmp_path):
    """Missbrauchsfall: ein Symlink im Serververzeichnis darf den Durchsetzer
    nicht dazu bringen, ausserhalb zu schreiben.

    Der Durchsetzer laeuft im Startpfad des Panels, also mit dessen Rechten.
    Ohne diese Pruefung waere ein Symlink im Serververzeichnis ein Schreibrecht
    auf jede Datei, die das Panel anfassen darf.
    """
    aussen = tmp_path / "aussen.ini"
    aussen.write_bytes(b"[Geheim]\r\nWert=1\r\n")
    innen = tmp_path / "server"
    (innen / "cfg").mkdir(parents=True)
    (innen / "cfg/link.ini").symlink_to(aussen)

    server = _Server(
        str(innen),
        wuensche.setze(None, datei="cfg/link.ini", eintraege=[("Geheim", "Wert", "99")]),
    )

    wuensche_durchsetzen(server)

    assert aussen.read_bytes() == b"[Geheim]\r\nWert=1\r\n"


def test_symlink_im_verzeichnis_wird_nicht_beschrieben(tmp_path):
    """Dieselbe Grenze, eine Ebene hoeher: nicht die Datei ist der Symlink,
    sondern ein Verzeichnis auf dem Weg dorthin."""
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (aussen / "ziel.ini").write_bytes(b"[A]\r\nB=1\r\n")
    innen = tmp_path / "server"
    innen.mkdir()
    (innen / "cfg").symlink_to(aussen, target_is_directory=True)

    server = _Server(
        str(innen),
        wuensche.setze(None, datei="cfg/ziel.ini", eintraege=[("A", "B", "99")]),
    )

    wuensche_durchsetzen(server)

    assert (aussen / "ziel.ini").read_bytes() == b"[A]\r\nB=1\r\n"


def test_ein_kaputter_wunsch_stoppt_die_uebrigen_nicht(tmp_path):
    """Der Durchsetzer laeuft im Startpfad. Eine Ausnahme dort wuerde dem
    Benutzer den Server verweigern — wegen eines Zusatzwunsches."""
    datei = _ark_datei(tmp_path)
    pfad = "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
    datei.write_bytes(b"[ServerSettings]\r\n")

    roh = wuensche.setze(None, datei=pfad, eintraege=[("ServerSettings", "Gut", "1")])
    # Ein Eintrag, der auf ein Verzeichnis zeigt: das Schreiben scheitert.
    (tmp_path / "ordner.ini").mkdir()
    kaputt = wuensche.setze(roh, datei="ordner.ini", eintraege=[("S", "K", "1")])

    wuensche_durchsetzen(_Server(str(tmp_path), kaputt))

    assert b"Gut=1" in datei.read_bytes()


def test_mehrere_wuensche_in_einer_datei_werden_zusammen_geschrieben(tmp_path):
    datei = _ark_datei(tmp_path)
    pfad = "ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini"
    datei.write_bytes(b"[ServerSettings]\r\n")

    roh = wuensche.setze(
        None,
        datei=pfad,
        eintraege=[
            ("ServerSettings", "TamingSpeedMultiplier", "5.0"),
            ("ServerSettings", "XPMultiplier", "2.0"),
            ("/Script/ShooterGame.ShooterGameMode", "BabyMatureSpeedMultiplier", "10.0"),
        ],
    )

    wuensche_durchsetzen(_Server(str(tmp_path), roh))

    inhalt = datei.read_text(encoding="utf-8")
    assert "TamingSpeedMultiplier=5.0" in inhalt
    assert "XPMultiplier=2.0" in inhalt
    assert "BabyMatureSpeedMultiplier=10.0" in inhalt
    assert inhalt.count("[ServerSettings]") == 1


def test_wuensche_als_patches_haben_das_agent_format():
    """Auf einer entfernten Node liegen die Dateien nicht hier.

    Der Durchsetzer oben schreibt lokal — auf einer Remote-Node waere das ein
    **stiller** Fehlschlag: kein Fehler, keine Datei, und der Benutzer sieht
    erst Wochen spaeter, dass sein Wert dort nie ankam. Statt dafuer einen
    zweiten Uebertragungsweg zu bauen, reisen die Wuensche als das, was sie
    ohnehin sind: INI-Patches im vorhandenen ``prepare_runtime``-Kanal, den der
    Agent seit jeher anwendet.

    Dieser Test haelt genau die Formatgleichheit fest. Weicht sie ab, weist der
    Agent den Patch ab (``Unknown runtime patch type``) und der Start scheitert
    sichtbar — aber lieber faellt es hier auf.
    """
    roh = wuensche.setze(
        None, datei="a.ini", eintraege=[("ServerSettings", "XPMultiplier", "2.0")]
    )

    patches = wuensche.als_patches(wuensche.lese(roh))

    assert patches == [
        {
            "type": "ini",
            "file": "a.ini",
            "section": "ServerSettings",
            "key": "XPMultiplier",
            "regex": None,
            "value": "2.0",
        }
    ]


def test_entfernte_node_wird_lokal_nicht_geschrieben(tmp_path):
    """Gegenprobe zur Formatgleichheit oben.

    Liefe der lokale Durchsetzer auch fuer einen Remote-Server, legte er auf
    dem Panel-Host ein Verzeichnis an, das dort niemand liest — und verdeckte
    damit genau den Fehlschlag, den er verhindern soll.
    """
    pfad = "cfg/game.ini"
    roh = wuensche.setze(None, datei=pfad, eintraege=[("S", "K", "1")])

    wuensche_durchsetzen(_Server(str(tmp_path), roh, node=_Node(is_local=False)))

    assert not (tmp_path / "cfg").exists()


def test_lokale_node_wird_geschrieben(tmp_path):
    """Und die Gegenprobe zur Gegenprobe: eine als lokal markierte Node ist
    dieser Host, dort schreibt der Durchsetzer sehr wohl."""
    pfad = "cfg/game.ini"
    roh = wuensche.setze(None, datei=pfad, eintraege=[("S", "K", "1")])

    wuensche_durchsetzen(_Server(str(tmp_path), roh, node=_Node(is_local=True)))

    assert "K=1" in (tmp_path / pfad).read_text(encoding="utf-8")
