"""Was das Panel schreibt, muss der Spielprozess danach noch lesen koennen.

Unter Rootless Docker sind Panel und Spielprozess **verschiedene Benutzer**:
das Panel laeuft als ``msm``, der Server als gemappte UID aus ``/etc/subuid``.
Zwei Stellen haben das ignoriert, und beide zusammen haben am 18.08.2026einen
laufenden Betrieb lahmgelegt:

1. ``file_edit_service.write_text`` schreibt ueber eine temporaere Datei und
   ersetzt das Ziel per ``os.replace``. Die neue Datei gehoerte damit dem
   Panel — jeder Speichervorgang enteignete den Spielprozess still.
2. ``server_file_access_service._apply_permissions`` setzte danach hart
   ``0640``. Fuer eine Datei, die nun dem Panel gehoert, heisst das: der
   Server kann seine eigene Konfiguration nicht mehr lesen.

Der ARK-Server startete daraufhin nicht mehr:

    Permission denied: '…/WindowsServer/GameUserSettings.ini'

Diese Datei haelt beide Haelften fest. Sie prueft absichtlich **ohne** echten
Benutzerwechsel — das ginge nur als root — sondern an dem, was der Code
nachweislich tut: Eigentuemer uebertragen und Modus nicht zurueckdrehen.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services import file_edit_service
from services.server_file_access_service import _apply_permissions


# ── Der Eigentuemer bleibt erhalten ───────────────────────────────────


def test_write_text_keeps_the_previous_owner(tmp_path: Path, monkeypatch) -> None:
    """Beim Ersetzen wird der alte Eigentuemer auf die neue Datei uebertragen.

    Ohne diesen Schritt gehoert jede gespeicherte Datei dem Panel, und der
    Spielprozess verliert den Zugriff auf seine eigene Konfiguration.
    """
    ziel = tmp_path / "GameUserSettings.ini"
    ziel.write_text("[ServerSettings]\nWert=1\n", encoding="utf-8")

    gerufen: list[tuple[int, int]] = []

    def chown_aufzeichnen(pfad, uid, gid):
        gerufen.append((uid, gid))

    monkeypatch.setattr(file_edit_service.os, "chown", chown_aufzeichnen)

    file_edit_service.write_text(ziel, "[ServerSettings]\nWert=2\n", expected_revision=None)

    assert gerufen, "der Eigentuemer wurde gar nicht erst uebertragen"
    # Und zwar der, der vorher dranstand.
    info = os.stat(tmp_path)
    assert gerufen[0] == (os.getuid(), os.getgid()) or gerufen[0] == (info.st_uid, info.st_gid)
    assert ziel.read_text(encoding="utf-8") == "[ServerSettings]\nWert=2\n"


def test_a_failing_chown_does_not_lose_the_content(tmp_path: Path, monkeypatch) -> None:
    """Kein CAP_CHOWN? Dann wird trotzdem gespeichert.

    Der Inhalt ist das, worauf der Mensch wartet. Ein nicht uebertragbarer
    Eigentuemer ist ein Schoenheitsfehler, kein Grund fuer einen 500er —
    zumal der Modus die Datei danach fuer den Server lesbar haelt.
    """
    ziel = tmp_path / "server.ini"
    ziel.write_text("alt\n", encoding="utf-8")

    def chown_verweigern(pfad, uid, gid):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(file_edit_service.os, "chown", chown_verweigern)

    ergebnis = file_edit_service.write_text(ziel, "neu\n", expected_revision=None)

    assert ziel.read_text(encoding="utf-8") == "neu\n"
    assert ergebnis["revision"]


def test_a_symlink_does_not_donate_a_foreign_owner(tmp_path: Path, monkeypatch) -> None:
    """Der Eigentuemer stammt vom **Link**, nicht von seinem Ziel.

    ``os.stat`` folgt Symlinks, ``os.lstat`` nicht. Mit ``stat`` haette ein
    Link auf eine fremde Datei deren ``uid``/``gid`` auf die neu geschriebene
    Datei uebertragen — wer im Serververzeichnis schreiben darf, koennte damit
    eine stille Rechteverschiebung ausloesen.

    ``os.replace`` ersetzt ohnehin den Link selbst und nicht sein Ziel; die
    gelesenen Werte muessen deshalb auch vom Link kommen, damit beide Seiten
    dieselbe Sache meinen.
    """
    ziel = tmp_path / "fremd.conf"
    ziel.write_text("fremd\n", encoding="utf-8")
    link = tmp_path / "server.ini"
    link.symlink_to(ziel)

    # Fremde Eigentuemer lassen sich ohne root nicht erzeugen — also wird
    # beobachtet, **welcher Pfad** befragt wird. Genau daran haengt der Fehler.
    befragt: list[str] = []
    echtes_lstat = Path.lstat
    echtes_stat = Path.stat

    def lstat_merken(self):
        befragt.append(f"lstat:{self.name}")
        return echtes_lstat(self)

    def stat_merken(self, *a, **k):
        befragt.append(f"stat:{self.name}")
        return echtes_stat(self, *a, **k)

    monkeypatch.setattr(Path, "lstat", lstat_merken)
    monkeypatch.setattr(Path, "stat", stat_merken)
    monkeypatch.setattr(file_edit_service.os, "chown", lambda *a: None)

    file_edit_service.write_text(link, "neu\n", expected_revision=None)

    assert any(e == "lstat:server.ini" for e in befragt), (
        "der Link selbst wurde nie mit lstat befragt — dann stammen Modus und "
        "Eigentuemer vom Ziel des Symlinks"
    )
    # Und die Fremddatei ist unangetastet: `os.replace` hat den Link ersetzt.
    assert ziel.read_text(encoding="utf-8") == "fremd\n"


def test_a_new_file_has_no_owner_to_preserve(tmp_path: Path, monkeypatch) -> None:
    """Bei einer neuen Datei gibt es nichts zu erhalten — also kein `chown`."""
    ziel = tmp_path / "neu.ini"
    gerufen: list[tuple] = []

    monkeypatch.setattr(
        file_edit_service.os, "chown", lambda *a: gerufen.append(a)
    )

    file_edit_service.write_text(ziel, "inhalt\n", expected_revision=None)

    assert gerufen == []
    assert ziel.read_text(encoding="utf-8") == "inhalt\n"


# ── Der Modus wird nur verschaerft, nie zurueckgedreht ────────────────


def test_a_panel_owned_file_stays_reachable_for_the_game_process(
    tmp_path: Path,
) -> None:
    """Gehoert die Datei dem Panel, muss der Modus die Bruecke schlagen.

    ``chown`` scheitert fuer einen unprivilegierten Prozess — Linux erlaubt
    kein Verschenken von Dateien. Bleibt das Panel also Eigentuemer, ist der
    Modus das Einzige, was den Spielprozess noch hereinlaesst. Ohne diese
    Regel war die Folge genau der gemeldete Ausfall: ARK startete nicht mehr,
    weil es seine eigene ``GameUserSettings.ini`` nicht lesen konnte.
    """
    install = tmp_path / "srv"
    install.mkdir()
    datei = install / "GameUserSettings.ini"
    datei.write_text("x=1\n", encoding="utf-8")
    datei.chmod(0o600)  # nur der Eigentuemer — der Server kaeme nicht heran

    _apply_permissions(str(install), datei)

    modus = datei.stat().st_mode & 0o777
    assert modus & 0o066 == 0o066, (
        "eine Datei des Panels muss fuer Gruppe und Andere les- und "
        "schreibbar sein, sonst sperrt sie den Spielprozess aus"
    )


def test_a_shared_group_makes_world_permissions_unnecessary(
    tmp_path: Path, monkeypatch
) -> None:
    """Teilen sich Panel und Spielprozess eine Gruppe, bleibt die Welt aussen vor.

    Das ist der Unterschied zwischen "funktioniert" und "sauber". Weltweite
    Schreibrechte loesen das Problem auch, aber sie oeffnen die Datei fuer
    jeden Prozess auf dem Host. `scripts/fix-server-permissions.sh` legt
    stattdessen je gemappter GID eine Gruppe an und macht `msm` zum Mitglied —
    danach reichen Gruppenrechte.
    """
    install = tmp_path / "srv"
    install.mkdir()
    datei = install / "GameUserSettings.ini"
    datei.write_text("x=1\n", encoding="utf-8")
    datei.chmod(0o600)

    # Der Prozess ist Mitglied in der Gruppe der Datei.
    monkeypatch.setattr(
        file_edit_service.os, "getgroups", lambda: [datei.stat().st_gid]
    )
    monkeypatch.setattr(
        "services.server_file_access_service.os.getgroups",
        lambda: [datei.stat().st_gid],
    )

    _apply_permissions(str(install), datei)

    modus = datei.stat().st_mode & 0o777
    assert modus & 0o060 == 0o060, "die Gruppe muss lesen und schreiben duerfen"
    assert modus & 0o007 == 0, (
        "mit geteilter Gruppe sind Weltrechte unnoetig — und damit falsch"
    )


def test_without_a_shared_group_the_world_bit_is_the_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """Fehlt die Gruppe, bleibt nur der Notnagel — sonst startet der Server nicht.

    Das ist der Zustand eines frisch angelegten Servers, bevor
    `fix-server-permissions.sh` ihn eingesammelt hat. Ohne Weltrechte koennte
    der Spielprozess seine eigene Konfiguration nicht lesen; genau daran ist
    am 18.08.2026 ein ARK-Server nicht mehr gestartet.
    """
    install = tmp_path / "srv"
    install.mkdir()
    datei = install / "GameUserSettings.ini"
    datei.write_text("x=1\n", encoding="utf-8")
    datei.chmod(0o600)

    # Keine gemeinsame Gruppe.
    monkeypatch.setattr(
        "services.server_file_access_service.os.getgroups", lambda: [4242]
    )

    _apply_permissions(str(install), datei)

    modus = datei.stat().st_mode & 0o777
    assert modus & 0o006 == 0o006, (
        "ohne gemeinsame Gruppe sperrt die Datei den Spielprozess aus"
    )


def test_permissions_are_never_narrowed_below_what_the_game_needs(
    tmp_path: Path,
) -> None:
    """Bestehende Zugriffsbits werden nie **entfernt**.

    Das ist der Fall aus der Meldung: der Spielprozess legt seine Dateien mit
    Gruppen- und Weltrechten an, weil das Panel sie sonst nicht lesen koennte.
    Ein hartes ``0640`` danach dreht genau das zurueck und sperrt ihn aus.

    Hinzukommen duerfen Rechte (siehe der Test darueber) — verschwinden nicht.
    """
    install = tmp_path / "srv"
    unterordner = install / "Config"
    unterordner.mkdir(parents=True)
    datei = unterordner / "GameUserSettings.ini"
    datei.write_text("x=1\n", encoding="utf-8")
    datei.chmod(0o664)
    unterordner.chmod(0o775)

    _apply_permissions(str(install), datei)

    # Kein einziges Bit ist verlorengegangen.
    assert datei.stat().st_mode & 0o664 == 0o664
    assert unterordner.stat().st_mode & 0o775 == 0o775


def test_permissions_are_widened_when_the_owner_cannot_write(tmp_path: Path) -> None:
    """Fehlt dem Eigentuemer das Schreibrecht, kommt es hinzu.

    Der Block heisst nicht „fass nichts an“, sondern „nimm nichts weg“. Eine
    Datei, in die das Panel selbst nicht schreiben koennte, waere beim
    naechsten Speichern ein Fehler.
    """
    install = tmp_path / "srv"
    install.mkdir()
    datei = install / "server.cfg"
    datei.write_text("x=1\n", encoding="utf-8")
    datei.chmod(0o400)

    _apply_permissions(str(install), datei)

    modus = datei.stat().st_mode & 0o777
    assert modus & 0o600 == 0o600, "der Eigentuemer muss lesen und schreiben koennen"


def test_a_foreign_owner_does_not_break_the_save(tmp_path: Path, monkeypatch) -> None:
    """``chmod`` auf fremdem Eigentuemer wirft — das darf nichts abbrechen."""
    install = tmp_path / "srv"
    install.mkdir()
    datei = install / "server.cfg"
    datei.write_text("x=1\n", encoding="utf-8")
    datei.chmod(0o400)

    def chmod_verweigern(self, modus):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "chmod", chmod_verweigern)

    # Kein Fehler nach aussen.
    _apply_permissions(str(install), datei)
