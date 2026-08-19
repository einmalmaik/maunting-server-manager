"""Snapshot-Tests: DayZ + Conan-Plugins produzieren *nach* der Blueprint-Migration
die geschützte ``build_container_command``-Ausgabe (post-evolution baseline).

Diese Tests sind die Regressionsschranke gegen unbeabsichtigte Verhaltens-
aenderungen beim Wechsel von hartcodierten Kommandos auf den Renderer.
Intentional baseline evolutions (z. B. DayZ -profiles) werden mit Kommentar
dokumentiert und die Assertions entsprechend aktualisiert.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blueprints.schema import load_blueprint_file
from games.blueprint_plugin import BlueprintPlugin


# Server-Stubs — kein DB-Round-Trip nötig, weil build_container_command nur
# lesend auf wenige Felder zugreift: die drei Ports, install_dir, public_bind_ip
# — und seit 980c7ce4 zusätzlich den Namen.
#
# Der Name gehört hier hinein, obwohl dieser Test ihn nirgends erwartet: Der
# Renderer bindet ihn seit 980c7ce4 an das Token ``{SERVER_NAME}`` (für ASAs
# ``?SessionName=``), und blueprint_plugin liest ihn deshalb bei JEDEM Blueprint
# aus — auch bei denen, deren Startzeile das Token gar nicht kennt. Genau so
# liegt der Fall bei dayz und conan: Beide Startzeilen enthalten kein
# ``{SERVER_NAME}``, ihre argv sind vom Namen also nachweislich unabhängig. Der
# Legacy-Pfad rief denselben Renderer ohne ``server_name`` auf und kam auf
# dasselbe Ergebnis — die Zusage dieser Datei ist damit unberührt. Fehlte das
# Feld, scheiterten die Snapshots an einem AttributeError statt an einem echten
# Unterschied. ``Server.name`` ist in der Datenbank ``nullable=False``; der
# direkte Zugriff im Produktivcode ist korrekt, nur der Stub war unvollständig.
def _stub_server(game_port=None, query_port=None, rcon_port=None, public_bind_ip=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        # Auffälliger Name mit Absicht: Taucht er je in einem der Snapshots
        # unten auf, hat jemand ``{SERVER_NAME}`` in eine dieser Startzeilen
        # geholt und damit die Legacy-Gleichheit gebrochen.
        name="MSM Snapshot Server",
        install_dir="/tmp/srv",
        game_port=game_port,
        query_port=query_port,
        rcon_port=rcon_port,
        public_bind_ip=public_bind_ip,
    )


def _native_plugin(blueprint_id: str) -> BlueprintPlugin:
    path = Path(__file__).resolve().parents[1] / "blueprints" / "native" / f"{blueprint_id}.blueprint.json"
    return BlueprintPlugin(load_blueprint_file(path))


def test_dayz_no_mods_matches_legacy_argv() -> None:
    plugin = _native_plugin("dayz")
    server = _stub_server(game_port=2302)
    with patch("games.blueprint_plugin.active_mod_ids", return_value=[]):
        argv = plugin.build_container_command(server)
    # NOTE: DayZ Linux documents -profiles=profiles relative to the server workdir.
    # MSM creates install_dir/profiles before start so the bind-mount path exists.
    assert argv == [
        "./DayZServer",
        "-config=serverDZ.cfg",
        "-port=2302",
        "-BEpath=battleye",
        "-profiles=profiles",
        "-dologs",
        "-adminlog",
        "-netlog",
        "-freezecheck",
    ]


def test_dayz_with_mods_matches_legacy_argv() -> None:
    plugin = _native_plugin("dayz")
    server = _stub_server(game_port=2302)
    with patch("games.blueprint_plugin.active_mod_ids", return_value=["12345", "67890"]):
        argv = plugin.build_container_command(server)
    assert argv == [
        "./DayZServer",
        "-config=serverDZ.cfg",
        "-port=2302",
        "-BEpath=battleye",
        "-profiles=profiles",
        "-dologs",
        "-adminlog",
        "-netlog",
        "-freezecheck",
        "-mod=12345;67890;",
    ]


def test_dayz_without_game_port_omits_port_arg() -> None:
    plugin = _native_plugin("dayz")
    server = _stub_server(game_port=None)
    with patch("games.blueprint_plugin.active_mod_ids", return_value=[]):
        argv = plugin.build_container_command(server)
    assert argv == [
        "./DayZServer",
        "-config=serverDZ.cfg",
        "-BEpath=battleye",
        "-profiles=profiles",
        "-dologs",
        "-adminlog",
        "-netlog",
        "-freezecheck",
    ]


def test_conan_full_argv_matches_legacy() -> None:
    plugin = _native_plugin("conan_exiles_ue5")
    server = _stub_server(game_port=27015, query_port=27016, rcon_port=27017)
    with patch("games.blueprint_plugin.active_mod_ids", return_value=[]):
        argv = plugin.build_container_command(server)
    assert argv == [
        "/bin/bash",
        "/data/ConanSandboxServer.sh",
        "-log",
        "-Port=27015",
        "-QueryPort=27016",
        "-RconPort=27017",
    ]


def test_conan_missing_query_omits_arg() -> None:
    plugin = _native_plugin("conan_exiles_ue5")
    server = _stub_server(game_port=27015, query_port=None, rcon_port=27017)
    with patch("games.blueprint_plugin.active_mod_ids", return_value=[]):
        argv = plugin.build_container_command(server)
    assert argv == [
        "/bin/bash",
        "/data/ConanSandboxServer.sh",
        "-log",
        "-Port=27015",
        "-RconPort=27017",
    ]


def test_server_name_does_not_change_dayz_or_conan_argv() -> None:
    """Der Servername darf diese beiden argv nicht beeinflussen.

    Das ist die Zusage, die der Stub oben stillschweigend voraussetzt: Weil der
    Legacy-Pfad den Renderer ohne ``server_name`` aufrief, sind die Snapshots
    nur dann weiterhin Legacy-gleich, wenn der Name folgenlos bleibt. Holt
    jemand ``{SERVER_NAME}`` in eine dieser Startzeilen, fällt es hier auf und
    nicht erst im Betrieb.
    """
    for blueprint_id, server in (
        ("dayz", _stub_server(game_port=2302)),
        ("conan_exiles_ue5", _stub_server(game_port=27015, query_port=27016, rcon_port=27017)),
    ):
        plugin = _native_plugin(blueprint_id)
        with patch("games.blueprint_plugin.active_mod_ids", return_value=[]):
            server.name = "Server A"
            argv_a = plugin.build_container_command(server)
            server.name = "Ein völlig anderer Name"
            argv_b = plugin.build_container_command(server)
        assert argv_a == argv_b, f"{blueprint_id}: argv hängt am Servernamen"


def test_native_blueprints_use_generic_plugin() -> None:
    """Native Unterstuetzung bedeutet mitgelieferte Blueprint + BlueprintPlugin."""
    dayz = _native_plugin("dayz")
    conan = _native_plugin("conan_exiles_ue5")
    assert dayz.get_blueprint() is not None
    assert conan.get_blueprint() is not None
    assert dayz.docker_image == dayz.get_blueprint().runtime.image
    assert conan.docker_image == conan.get_blueprint().runtime.image
