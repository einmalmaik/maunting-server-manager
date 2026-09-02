"""Small helpers for conflict-safe text editing and filesystem metadata.

Callers must resolve and authorize the target path before invoking this module.
No absolute path is ever returned to API clients.
"""
from __future__ import annotations

import hashlib
import os
import stat as stat_module
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any


class FileRevisionConflict(Exception):
    def __init__(self, current_revision: str | None) -> None:
        super().__init__("File changed since it was opened")
        self.current_revision = current_revision


class EditNotApplicable(Exception):
    """Ein Suchtext passt nicht genau einmal.

    ``index`` ist die Nummer des Edits in der uebergebenen Liste, ``count`` die
    tatsaechliche Trefferzahl. Beides gehoert in die Fehlermeldung, weil der
    Aufrufer daraus eine brauchbare Auskunft bauen kann: bei null Treffern
    stimmt der Suchtext nicht, bei mehreren fehlt ihm Kontext.
    """

    def __init__(self, index: int, count: int) -> None:
        super().__init__(f"Edit {index} matched {count} times, expected exactly 1")
        self.index = index
        self.count = count


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def lock_for(target: Path) -> threading.Lock:
    """Die Sperre zu einem Pfad — dieselbe für jeden, der sie anfasst.

    Öffentlich, weil nicht nur das Schreiben sie braucht: `delete_server_text`
    liest, sichert einen Versionsschnappschuss und löscht danach, und in dem
    Zeitfenster darf kein Schreibvorgang dazwischenrutschen. Zwei getrennte
    Sperren wären keine.
    """
    key = str(target)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def content_revision(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


@lru_cache(maxsize=256)
def _identity_name(value: int, *, group: bool) -> str | None:
    """Nummer zu Anzeigename, einmal je uid/gid und Prozess.

    In einem Serververzeichnis gehören praktisch alle Dateien derselben uid.
    Ohne Zwischenspeicher kostet eine Auflistung mit zehntausend Einträgen
    zwanzigtausend NSS-Abfragen; mit ihm zwei. Der Preis ist ein veralteter
    Anzeigename, falls jemand den Benutzer auf dem Host umbenennt — für ein
    reines Anzeigefeld vertretbar, ein Geheimnis steckt hier nicht drin.
    """
    try:
        if os.name != "posix":
            return None
        if group:
            import grp

            return grp.getgrgid(value).gr_name
        import pwd

        return pwd.getpwuid(value).pw_name
    except (KeyError, ImportError, OSError):
        return None


def metadata(target: Path) -> dict[str, Any]:
    info = target.stat(follow_symlinks=False)
    return {
        "size": info.st_size if target.is_file() else 0,
        "modified": info.st_mtime,
        "mode": format(stat_module.S_IMODE(info.st_mode), "04o"),
        "owner": _identity_name(info.st_uid, group=False),
        "group": _identity_name(info.st_gid, group=True),
    }


def read_text(target: Path) -> dict[str, Any]:
    data = target.read_bytes()
    return {
        "content": data.decode("utf-8", errors="replace"),
        "revision": content_revision(data),
        **metadata(target),
    }


def apply_edits(content: str, edits: list[tuple[str, str]]) -> str:
    """Ersetzt Textstellen der Reihe nach; jede muss genau einmal vorkommen.

    Das Gegenstueck zur Vollersetzung. Wer eine Datei ganz zurueckschreibt, muss
    sie ganz gesehen haben — bei einer Megabyte grossen Spielkonfiguration ist
    das unmoeglich. Wer eine Stelle ersetzt, muss nur diese Stelle kennen; alles
    uebrige bleibt Byte fuer Byte stehen, gerade weil es hier nicht durchlaeuft.

    **Genau einmal** ist die eigentliche Zusage, nicht "mindestens einmal".
    ``value="1"`` kommt in einer XML-Konfiguration hundertfach vor; ein
    ``replace`` darauf traefe neunundneunzig unbeteiligte Stellen mit. Die
    Eindeutigkeit muss deshalb der Suchtext selbst herstellen, indem er genug
    Umgebung mitbringt — und ob er das tut, ist hier pruefbar. Bei mehreren
    Treffern wird abgebrochen, nicht geraten.

    Die Edits laufen **nacheinander**, jeder auf dem Ergebnis des vorigen. Das
    ist die Reihenfolge, die ein Mensch erwartet, und sie macht aufeinander
    aufbauende Aenderungen an derselben Stelle moeglich. Es heisst aber auch:
    die Eindeutigkeit gilt zum Zeitpunkt des jeweiligen Edits, nicht gegen den
    Ausgangstext.

    Kein regulaerer Ausdruck. Exakter Text ist nicht nur einfacher, sondern die
    Voraussetzung dafuer, dass "genau einmal" ueberhaupt zaehlbar bleibt.
    """
    result = content
    for index, (find, replace) in enumerate(edits):
        count = result.count(find)
        if count != 1:
            raise EditNotApplicable(index, count)
        result = result.replace(find, replace, 1)
    return result


def write_text(
    target: Path,
    content: str,
    *,
    expected_revision: str | None = None,
    create_only: bool = False,
) -> dict[str, Any]:
    """Atomically replace a text file after an optimistic revision check."""
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    with lock_for(target):
        if create_only and target.exists():
            raise FileExistsError("Target file already exists")
        current_revision = content_revision(target.read_bytes()) if target.is_file() else None
        if expected_revision is not None and current_revision != expected_revision:
            raise FileRevisionConflict(current_revision)

        # `lstat` und nicht `stat`: Letzteres **folgt Symlinks**, und dann
        # stammten Modus und Eigentuemer von der Datei, auf die der Link
        # zeigt. Wer im Serververzeichnis schreiben darf, koennte damit einen
        # fremden Eigentuemer auf die neu geschriebene Datei uebertragen —
        # eine stille Rechteverschiebung. `os.replace` ersetzt ohnehin den
        # Link selbst und nicht sein Ziel; die Werte muessen deshalb vom Link
        # kommen, damit beides dieselbe Sache meint.
        vorheriges: os.stat_result | None = None
        if target.is_symlink() or target.exists():
            try:
                vorheriges = target.lstat()
            except OSError:
                vorheriges = None

        previous_mode = (
            stat_module.S_IMODE(vorheriges.st_mode) if vorheriges is not None else 0o644
        )
        # Wem die Datei bisher gehoerte. Unter Rootless Docker ist das der
        # **Spielprozess** (gemappte UID aus /etc/subuid) und nicht das Panel.
        #
        # Geschrieben wird ueber eine temporaere Datei mit `os.replace`, und
        # die neue gehoert dem, der sie angelegt hat — also dem Panel. Ohne
        # das `chown` unten wechselt jede gespeicherte Datei stillschweigend
        # den Eigentuemer, und der Server kann seine eigene Konfiguration
        # danach nicht mehr lesen. Genau so ist am 18.08.2026 ein ARK-Server
        # nach einer Konfigurationsaenderung nicht mehr gestartet:
        #
        #   Permission denied: '…/WindowsServer/GameUserSettings.ini'
        #
        # `None` heisst „Datei ist neu“ — dann gibt es keinen vorherigen
        # Eigentuemer, den man erhalten koennte.
        previous_owner: tuple[int, int] | None = (
            (vorheriges.st_uid, vorheriges.st_gid) if vorheriges is not None else None
        )
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=target.parent,
                prefix=".msm-edit-",
            ) as temp_file:
                os.fchmod(temp_file.fileno(), 0o600)
                temp_file.write(encoded)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)
            # Eigentuemer **vor** dem Ersetzen setzen: danach traegt das Ziel
            # schon den endgueltigen Namen, und ein Fehlschlag hinterliesse
            # eine Datei, die der Server nicht lesen kann.
            #
            # Scheitert es (kein CAP_CHOWN, fremder Eigentuemer), bleibt es
            # beim Panel als Eigentuemer — der Modus unten oeffnet die Datei
            # dann fuer Gruppe und Andere, damit der Server sie trotzdem
            # lesen kann. Ein harter Abbruch waere hier falsch: der Inhalt
            # ist geschrieben, und der Mensch wartet auf ein Ergebnis.
            #
            # ``os.chown`` gibt es nur auf POSIX. Ohne diese Abfrage brach das
            # Speichern auf der Entwicklungsmaschine mit einem
            # ``AttributeError`` ab — der ist kein ``OSError`` und lief am
            # ``except`` darunter vorbei, obwohl der Inhalt laengst auf der
            # Platte lag.
            if previous_owner is not None and os.name == "posix":
                try:
                    os.chown(temp_path, previous_owner[0], previous_owner[1])
                except (PermissionError, OSError):
                    pass
            os.replace(temp_path, target)
            temp_path = None
            os.chmod(target, previous_mode)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    return {
        "revision": content_revision(encoded),
        **metadata(target),
    }
