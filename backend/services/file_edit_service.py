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


def _lock_for(target: Path) -> threading.Lock:
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
    with _lock_for(target):
        if create_only and target.exists():
            raise FileExistsError("Target file already exists")
        current_revision = content_revision(target.read_bytes()) if target.is_file() else None
        if expected_revision is not None and current_revision != expected_revision:
            raise FileRevisionConflict(current_revision)

        previous_mode = stat_module.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
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
