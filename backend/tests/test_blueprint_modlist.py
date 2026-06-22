"""Tests fuer den blueprint-getriebenen Modlist-Helfer.

Schwerpunkt: Pfad-Sicherheit (Symlink-Escape, ``..``-Pfade) + Filter-Verhalten
(disabled-Mods landen nicht in der Datei).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from games.base import write_workshop_modlist


@dataclass
class _Server:
    id: int
    install_dir: str
    game_type: str = ""


def test_write_modlist_happy_path(tmp_path: Path) -> None:
    server = _Server(id=1, install_dir=str(tmp_path))
    write_workshop_modlist(server, "Mods/modlist.txt", ["a.pak", "b.pak"])
    out = (tmp_path / "Mods" / "modlist.txt").read_text(encoding="utf-8")
    assert out == "a.pak\nb.pak\n"


def test_write_modlist_conan_ue5_prefixes_asterisk(tmp_path: Path) -> None:
    server = _Server(id=10, install_dir=str(tmp_path), game_type="conan_exiles_ue5")
    write_workshop_modlist(
        server,
        "ConanSandbox/Mods/modlist.txt",
        ["Foo.pak", "*Bar.pak", "Foo.pak"],
    )
    out = (tmp_path / "ConanSandbox" / "Mods" / "modlist.txt").read_text(encoding="utf-8")
    assert out == "*Foo.pak\n*Bar.pak\n"


def test_write_modlist_rejects_absolute_path(tmp_path: Path) -> None:
    server = _Server(id=2, install_dir=str(tmp_path))
    # Darf NICHT nach /etc geschrieben werden — Helfer schlucken den Fehler in
    # das Console-Log, also pruefen wir lediglich, dass /etc nicht angefasst wird.
    write_workshop_modlist(server, "/etc/passwd_test_msm", ["x"])
    assert not Path("/etc/passwd_test_msm").exists()


def test_write_modlist_rejects_dotdot(tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    server = _Server(id=3, install_dir=str(install))
    write_workshop_modlist(server, "../outside.txt", ["x"])
    assert not (tmp_path / "outside.txt").exists()


def test_write_modlist_rejects_symlink_escape(tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Symlink innerhalb install/, der nach outside zeigt.
    (install / "evil_dir").symlink_to(outside)
    server = _Server(id=4, install_dir=str(install))

    write_workshop_modlist(server, "evil_dir/escape.txt", ["x"])
    assert not (outside / "escape.txt").exists()


def test_write_modlist_empty_lines(tmp_path: Path) -> None:
    server = _Server(id=5, install_dir=str(tmp_path))
    write_workshop_modlist(server, "Mods/empty.txt", [])
    assert (tmp_path / "Mods" / "empty.txt").read_text() == ""
