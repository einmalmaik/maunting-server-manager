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


# Server-Stubs — wir brauchen keinen DB-Round-Trip, weil
# build_container_command nur server.game_port/query_port/rcon_port liest.
def _stub_server(game_port=None, query_port=None, rcon_port=None, public_bind_ip=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
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
        "/data/DayZServer",
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
        "/data/DayZServer",
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
        "/data/DayZServer",
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


def test_native_blueprints_use_generic_plugin() -> None:
    """Native Unterstuetzung bedeutet mitgelieferte Blueprint + BlueprintPlugin."""
    dayz = _native_plugin("dayz")
    conan = _native_plugin("conan_exiles_ue5")
    assert dayz.get_blueprint() is not None
    assert conan.get_blueprint() is not None
    assert dayz.docker_image == dayz.get_blueprint().runtime.image
    assert conan.docker_image == conan.get_blueprint().runtime.image
