"""Der Blueprint-Wechsel muss aufraeumen — und es beweisen koennen.

Anlass ist ein Betriebsfall: ein Minecraft-Server wurde auf eine andere Version
umgestellt, der Wechsel meldete Erfolg, und der Start scheiterte daran, dass die
**alte Welt noch dalag**.

Die Ursache war kein Randfall: `switch_server_blueprint` rief `os.path.exists`
und `shutil.rmtree` in einem Modul, das weder `os` noch `shutil` importiert. Der
lokale Zweig warf `NameError`, das umgebende `except Exception` machte eine
Logzeile daraus, und der Wechsel lief weiter. Auf lokalen Nodes hat er damit nie
eine Datei entfernt.

Die Tests hier halten fest, was seitdem gilt: ein misslungenes Aufraeumen ist ein
**Abbruch**, kein Randvermerk im Log — und es wird nachgesehen, nicht angenommen.
"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Server
from services import server_file_access_service
from services.node_client import NodeClientError


def _server(db: Session, install_dir: str) -> Server:
    server = Server(
        name="Wechselkandidat",
        game_type="minecraft_vanilla",
        install_dir=install_dir,
        container_name="msm-srv-wechsel",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _befuellen(wurzel) -> None:
    (wurzel / "server.properties").write_text("motd=alt\n", encoding="utf-8")
    welt = wurzel / "world"
    welt.mkdir()
    (welt / "level.dat").write_bytes(b"\x00\x01")
    (welt / "region").mkdir()
    (welt / "region" / "r.0.0.mca").write_bytes(b"\x00")


# ── wipe_server_root ──────────────────────────────────────────────────


def test_wipe_removes_everything_and_reports_how_much(db: Session, tmp_path):
    """Der Normalfall: leer danach, und die Zahl stimmt."""
    _befuellen(tmp_path)
    server = _server(db, str(tmp_path))

    with patch.object(
        server_file_access_service.docker_service, "remove", return_value={"ok": True}
    ):
        entfernt = server_file_access_service.wipe_server_root(db, server)

    assert entfernt == 2  # server.properties und world/
    assert os.listdir(tmp_path) == []


def test_wipe_repairs_permissions_and_tries_again(db: Session, tmp_path):
    """Der eigentliche Betriebsfall — container-eigene Dateien.

    Der erste Loeschversuch scheitert an den Rechten. Genau dafuer gibt es
    `repair_bind_mount_permissions`; der Loeschpfad ruft es seit jeher, der
    Wechsel tat es nie. Hier wird bewiesen, dass er es jetzt tut **und** danach
    erneut loescht, statt den Fehler zu verschlucken.
    """
    import shutil as _shutil

    _befuellen(tmp_path)
    server = _server(db, str(tmp_path))
    echtes_rmtree = _shutil.rmtree
    ablauf: list[str] = []

    def _erst_verweigern(pfad, *args, **kwargs):
        ablauf.append("loeschen")
        if ablauf.count("reparieren") == 0:
            raise PermissionError("gehoert dem Container")
        echtes_rmtree(pfad, *args, **kwargs)

    def _reparieren(pfad: str, **kwargs):
        ablauf.append("reparieren")
        return {"ok": True}

    with (
        patch.object(server_file_access_service.docker_service, "remove", return_value={"ok": True}),
        patch.object(
            server_file_access_service.docker_service,
            "repair_bind_mount_permissions",
            side_effect=_reparieren,
        ),
        patch("shutil.rmtree", side_effect=_erst_verweigern),
    ):
        entfernt = server_file_access_service.wipe_server_root(db, server)

    assert ablauf == ["loeschen", "reparieren", "loeschen"]
    assert entfernt == 1  # nur `world/`, die Datei war beim ersten Lauf schon weg
    assert os.listdir(tmp_path) == []


def test_wipe_fails_loudly_when_something_survives(db: Session, tmp_path):
    """Bleibt etwas liegen, ist das ein Fehler — kein Logeintrag."""
    _befuellen(tmp_path)
    server = _server(db, str(tmp_path))

    with (
        patch.object(server_file_access_service.docker_service, "remove", return_value={"ok": True}),
        patch.object(
            server_file_access_service.docker_service,
            "repair_bind_mount_permissions",
            return_value={"ok": False, "error": "kein Docker"},
        ),
        patch("shutil.rmtree", side_effect=PermissionError("gehoert dem Container")),
    ):
        with pytest.raises(HTTPException) as fehler:
            server_file_access_service.wipe_server_root(db, server)

    assert fehler.value.detail["code"] == "server_root_wipe_failed"
    assert (tmp_path / "world" / "level.dat").exists()


def test_wipe_refuses_when_the_container_stays(db: Session, tmp_path):
    """Ein Container, der nicht weichen will, haelt den Bind-Mount.

    Dann wird gar nicht erst geloescht: halb geleert waere schlimmer als
    unberuehrt, denn das Backup ist zwar da, der Server aber unbrauchbar.
    """
    _befuellen(tmp_path)
    server = _server(db, str(tmp_path))

    with patch.object(
        server_file_access_service.docker_service,
        "remove",
        return_value={"ok": False, "error": "docker weg"},
    ):
        with pytest.raises(HTTPException) as fehler:
            server_file_access_service.wipe_server_root(db, server)

    assert fehler.value.status_code == 503
    assert fehler.value.detail["code"] == "server_container_remove_failed"
    assert (tmp_path / "server.properties").exists()


def test_wipe_uses_the_agent_when_the_directory_is_not_visible(db: Session, tmp_path):
    """Ein **lokaler** Node, dessen Verzeichnis der Panel-Prozess nicht sieht.

    Das war die zweite stille Luecke: der alte Code nahm den Agenten nur bei
    *entfernten* Nodes und tat lokal schlicht nichts. Jeder andere Dateizugriff
    dieses Moduls entscheidet ueber `_agent` — dieser jetzt auch.
    """
    server = _server(db, str(tmp_path / "gibtesnicht"))
    agent = MagicMock()

    with (
        patch.object(server_file_access_service.docker_service, "remove", return_value={"ok": True}),
        patch.object(server_file_access_service, "_agent", return_value=agent),
    ):
        assert server_file_access_service.wipe_server_root(db, server) is None

    agent.files_delete_server_root.assert_called_once()
    # Und das Verzeichnis wird wieder angelegt: ein Bind-Mount ohne Quelle
    # laesst Docker als root anlegen, und dann kommt der Spielprozess nicht
    # hinein.
    agent.files_ensure_server_root.assert_called_once()


def test_wipe_reports_an_agent_failure_instead_of_swallowing_it(db: Session, tmp_path):
    server = _server(db, str(tmp_path / "gibtesnicht"))
    agent = MagicMock()
    agent.files_delete_server_root.side_effect = NodeClientError("Agent weg", status_code=503)

    with (
        patch.object(server_file_access_service.docker_service, "remove", return_value={"ok": True}),
        patch.object(server_file_access_service, "_agent", return_value=agent),
    ):
        with pytest.raises(HTTPException) as fehler:
            server_file_access_service.wipe_server_root(db, server)

    assert fehler.value.detail["code"] == "server_root_wipe_failed"


def test_wipe_creates_the_root_when_it_is_missing(db: Session, tmp_path):
    """Nichts zu leeren ist kein Fehler — aber das Verzeichnis muss existieren."""
    ziel = tmp_path / "neu"
    server = _server(db, str(ziel))

    with (
        patch.object(server_file_access_service.docker_service, "remove", return_value={"ok": True}),
        patch.object(server_file_access_service, "_agent", return_value=None),
    ):
        assert server_file_access_service.wipe_server_root(db, server) == 0

    assert ziel.is_dir()


# ── switch_server_blueprint ───────────────────────────────────────────


def _blueprint_registry():
    blueprint = SimpleNamespace(ports=[SimpleNamespace(name="game", protocol="tcp")])
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(blueprint=blueprint)
    return registry


def _nachgewiesenes_backup():
    """Ein Backup-Datensatz, wie ihn `create_server_backup` bei Erfolg liefert.

    Hier stand frueher `status="completed"` — ein Feld, das `Backup` nie hatte.
    Der Stub bestaetigte damit eine Pruefung, die im Betrieb nie zutraf
    (`getattr(..., "status", None) == "failed"` war immer `None`). Der Nachweis
    heisst `verified_at` und wird nur gesetzt, wenn das Archiv nach dem
    Schreiben nachgemessen wurde.
    """
    return SimpleNamespace(id=7, verified_at=datetime.now(timezone.utc))


def test_switch_aborts_when_the_wipe_fails(db: Session, tmp_path):
    """Der Kern des Betriebsfalls.

    Scheitert das Aufraeumen, darf `game_type` **nicht** umgestellt werden. Sonst
    zeigt das Panel den neuen Blueprint an, waehrend die alten Daten noch
    daliegen — und die KI berichtet einen Erfolg, den es nicht gab.
    """
    from services import server_lifecycle_service

    server = _server(db, str(tmp_path))
    backup = _nachgewiesenes_backup()

    with (
        patch("services.backup_orchestrator.create_server_backup", return_value=backup),
        patch("blueprints.get_registry", return_value=_blueprint_registry()),
        patch.object(
            server_file_access_service,
            "wipe_server_root",
            side_effect=HTTPException(
                status_code=500,
                detail={"code": "server_root_wipe_failed", "message": "Uebrig: world"},
            ),
        ),
    ):
        with pytest.raises(HTTPException) as fehler:
            server_lifecycle_service.switch_server_blueprint(db, server, "minecraft_forge")

    assert fehler.value.detail["code"] == "server_root_wipe_failed"
    db.refresh(server)
    assert server.game_type == "minecraft_vanilla"


def test_switch_aborts_when_the_backup_is_unproven(db: Session, tmp_path):
    """Ohne Nachweis kein Wechsel — und keine geloeschte Datei.

    Das Pflicht-Backup prueft sich seit jeher selbst, aber es pruefte auf ein
    Feld, das `Backup` nie besass: `getattr(backup_record, "status", None) ==
    "failed"` lieferte immer `None`. Ein Archiv, das gar nicht geschrieben wurde,
    kam damit durch, und unmittelbar danach wurde das gesamte Serververzeichnis
    geleert.

    `verified_at is None` heisst "nicht nachgemessen" und ist damit dasselbe wie
    "nicht vorhanden": beides berechtigt nicht zum Loeschen. Der Test haelt
    zusaetzlich fest, dass `wipe_server_root` gar nicht erst gerufen wird.
    """
    from services import server_lifecycle_service

    server = _server(db, str(tmp_path))
    ohne_nachweis = SimpleNamespace(id=7, verified_at=None)
    wipe = MagicMock()

    with (
        patch("services.backup_orchestrator.create_server_backup", return_value=ohne_nachweis),
        patch("blueprints.get_registry", return_value=_blueprint_registry()),
        patch.object(server_file_access_service, "wipe_server_root", wipe),
    ):
        with pytest.raises(HTTPException) as fehler:
            server_lifecycle_service.switch_server_blueprint(db, server, "minecraft_forge")

    assert fehler.value.detail["code"] == "pre_switch_backup_failed"
    wipe.assert_not_called()
    db.refresh(server)
    assert server.game_type == "minecraft_vanilla"


def test_switch_reports_what_it_removed(db: Session, tmp_path):
    """Erfolg heisst: mit Zahl. Eine Behauptung war der Fehler."""
    from services import server_lifecycle_service

    server = _server(db, str(tmp_path))
    backup = _nachgewiesenes_backup()

    with (
        patch("services.backup_orchestrator.create_server_backup", return_value=backup),
        patch("blueprints.get_registry", return_value=_blueprint_registry()),
        patch.object(server_file_access_service, "wipe_server_root", return_value=4),
        patch("services.port_allocation_service.allocate_ports", return_value=[]),
        patch("games.get_plugin", return_value=None),
        patch.object(server_lifecycle_service, "sync_desired_state_to_agent", return_value=True),
    ):
        ergebnis = server_lifecycle_service.switch_server_blueprint(db, server, "minecraft_forge")

    assert ergebnis["files_removed"] == 4
    assert ergebnis["new_blueprint"] == "minecraft_forge"
    db.refresh(server)
    assert server.game_type == "minecraft_forge"
