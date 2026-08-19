from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services import file_service

#: Die Modusbits gibt es nur auf POSIX; Windows meldet für jede Datei
#: dasselbe 0o666 bzw. 0o444, und ein Test darauf wäre grün ohne etwas zu
#: belegen. Übersprungen sagt die Wahrheit.
nur_posix = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-Modusbits gibt es auf Windows nicht",
)


def test_single_upload_streams_to_atomic_temp_and_replaces(
    client: TestClient, auth_headers: dict, servers_dir: Path
) -> None:
    root = servers_dir / "22"
    root.mkdir()
    target = root / "world.dat"
    target.write_bytes(b"old")

    response = client.post(
        "/files/upload",
        params={"server_id": "22", "path": "world.dat"},
        headers=auth_headers,
        files={"file": ("world.dat", b"new-data")},
    )

    assert response.status_code == 200, response.text
    assert target.read_bytes() == b"new-data"
    assert list(root.glob(".msm-upload-*")) == []


def test_oversized_single_upload_preserves_destination_and_cleans_temp(
    client: TestClient, auth_headers: dict, servers_dir: Path, monkeypatch
) -> None:
    root = servers_dir / "23"
    root.mkdir()
    target = root / "world.dat"
    target.write_bytes(b"old")
    monkeypatch.setattr("routers.files.MAX_SINGLE_UPLOAD_SIZE", 5)

    response = client.post(
        "/files/upload",
        params={"server_id": "23", "path": "world.dat"},
        headers=auth_headers,
        files={"file": ("world.dat", b"123456")},
    )

    assert response.status_code == 413
    assert target.read_bytes() == b"old"
    assert list(root.glob(".msm-upload-*")) == []


# ── Der Modus neuer Dateien: das Gruppenmodell, nicht die Welt ────────────


@nur_posix
def test_neu_hochgeladene_datei_bekommt_das_gruppenmodell(
    client: TestClient, auth_headers: dict, servers_dir: Path
) -> None:
    """Eine neue Datei ist 0660 — Gruppe schreibt mit, die Welt bleibt draussen.

    Panel und Spielprozess laufen unter verschiedenen UIDs (Rootless Docker)
    und teilen sich die Dateien ueber die Gruppe des Serververzeichnisses;
    `docker_service` setzt beim Start dazu passend `g+rw` und `o-rwx`. Der
    Upload stand mit festem 0644 quer dazu: die Gruppe durfte nicht schreiben
    (der Spielprozess konnte eine hochgeladene Konfiguration nicht aendern),
    dafuer durfte jeder Host-Prozess mitlesen.
    """
    root = servers_dir / "31"
    root.mkdir()

    response = client.post(
        "/files/upload",
        params={"server_id": "31", "path": "neu.ini"},
        headers=auth_headers,
        files={"file": ("neu.ini", b"port=2302\n")},
    )

    assert response.status_code == 200, response.text
    modus = (root / "neu.ini").stat().st_mode & 0o777
    assert modus & 0o007 == 0, "die Welt hat auf einer Serverdatei nichts zu suchen"
    assert modus & 0o060 == 0o060, "die Gruppe ist die Bruecke zum Spielprozess"


@nur_posix
def test_upload_auf_eine_bestandsdatei_laesst_den_modus_stehen(
    client: TestClient, auth_headers: dict, servers_dir: Path
) -> None:
    """Was schon dasteht, behaelt seinen Modus.

    Der Spielprozess legt seine Dateien selbst an und weiss besser, was er
    braucht. Ein Upload ersetzt den Inhalt, nicht die Rechtevergabe — sonst
    naehme ein Wiederherstellen dem Server im Vorbeigehen den Zugriff.
    """
    root = servers_dir / "32"
    root.mkdir()
    ziel = root / "world.dat"
    ziel.write_bytes(b"alt")
    ziel.chmod(0o664)

    response = client.post(
        "/files/upload",
        params={"server_id": "32", "path": "world.dat"},
        headers=auth_headers,
        files={"file": ("world.dat", b"neu")},
    )

    assert response.status_code == 200, response.text
    assert ziel.stat().st_mode & 0o777 == 0o664


@nur_posix
def test_neu_geschriebene_datei_bekommt_das_gruppenmodell(
    servers_dir: Path,
) -> None:
    """Dieselbe Zusage fuer den Schreibweg — bisher stand sie nur im Kommentar.

    `write_text_if_revision` legt neue Dateien mit 0660 an. Ohne Test faellt
    das beim naechsten Umbau unbemerkt auf 0644 zurueck, und genau von dort
    kam es.
    """
    (servers_dir / "33").mkdir()

    file_service.write_text_if_revision("33", "config/neu.ini", "a=1\n")

    modus = (servers_dir / "33" / "config" / "neu.ini").stat().st_mode & 0o777
    assert modus & 0o007 == 0
    assert modus & 0o060 == 0o060


# ── Loeschen mit Revision: das Zeitfenster zwischen Lesen und Loeschen ────


def test_delete_mit_veralteter_revision_laesst_die_datei_liegen(
    client: TestClient, auth_headers: dict, servers_dir: Path
) -> None:
    """Zwischen Lesen und Loeschen kann der Spielprozess geschrieben haben.

    Das Panel liest die Datei, legt daraus einen Versionsschnappschuss an und
    loescht erst danach. Faellt der Schreibvorgang des Spielprozesses in
    dieses Fenster, zeigte der Rueckweg auf den alten Inhalt und der neue war
    ersatzlos weg — bei einer Handlung, die ohne Bestaetigung laeuft, weil sie
    mit genau diesem Rueckweg begruendet ist.

    Deshalb nennt das Panel die Revision, die es gesichert hat. Stimmt sie
    nicht mehr, bleibt die Datei liegen und der Agent meldet 409.
    """
    root = servers_dir / "34"
    root.mkdir()
    ziel = root / "server.cfg"
    ziel.write_bytes(b"port=2302\n")

    response = client.request(
        "DELETE",
        "/files/delete",
        params={
            "server_id": "34",
            "path": "server.cfg",
            "expected_revision": "sha256:passtnicht",
        },
        headers=auth_headers,
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "FILE_REVISION_CONFLICT"
    assert ziel.read_bytes() == b"port=2302\n"


def test_delete_mit_passender_revision_loescht(
    client: TestClient, auth_headers: dict, servers_dir: Path
) -> None:
    """Gegenprobe: mit der richtigen Kennung geht das Loeschen durch.

    Ohne sie waere die Zusage darueber auch dann erfuellt, wenn der Agent
    grundsaetzlich 409 antwortete.
    """
    root = servers_dir / "35"
    root.mkdir()
    ziel = root / "server.cfg"
    ziel.write_bytes(b"port=2302\n")
    revision = file_service.read_text_with_metadata("35", "server.cfg")["revision"]

    response = client.request(
        "DELETE",
        "/files/delete",
        params={"server_id": "35", "path": "server.cfg", "expected_revision": revision},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert not ziel.exists()


def test_delete_ohne_revision_loescht_weiterhin(
    client: TestClient, auth_headers: dict, servers_dir: Path
) -> None:
    """Aufraeumpfade nennen keine Revision — sie haben nichts gelesen.

    Manifeste und `.bak`-Reste werden geloescht, ohne dass jemand ihren Inhalt
    gesehen haette. Die Pruefung ist deshalb freiwillig und nicht Pflicht;
    waere sie Pflicht, brauchte jeder dieser Pfade vorher einen ueberfluessigen
    Lesevorgang.
    """
    root = servers_dir / "36"
    root.mkdir()
    ziel = root / "manifest.bak"
    ziel.write_bytes(b"egal")

    response = client.request(
        "DELETE",
        "/files/delete",
        params={"server_id": "36", "path": "manifest.bak"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert not ziel.exists()
