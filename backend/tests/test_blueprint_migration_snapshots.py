"""Snapshot-Tests: DayZ + Conan-Plugins produzieren *nach* der Blueprint-Migration
die geschützte ``build_container_command``-Ausgabe (post-evolution baseline).

Diese Tests sind die Regressionsschranke gegen unbeabsichtigte Verhaltens-
aenderungen beim Wechsel von hartcodierten Kommandos auf den Renderer.
Intentional baseline evolutions (z. B. DayZ -profiles) werden mit Kommentar
dokumentiert und die Assertions entsprechend aktualisiert.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from games.conan_exiles_ue5.plugin import ConanExilesUE5Plugin
from games.dayz.plugin import DayZPlugin


# Server-Stubs — wir brauchen keinen DB-Round-Trip, weil
# build_container_command nur server.game_port/query_port/rcon_port liest.
def _stub_server(game_port=None, query_port=None, rcon_port=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        install_dir="/tmp/srv",
        game_port=game_port,
        query_port=query_port,
        rcon_port=rcon_port,
    )


def test_dayz_no_mods_matches_legacy_argv() -> None:
    plugin = DayZPlugin()
    server = _stub_server(game_port=2302)
    with patch("games.dayz.plugin.active_mod_ids", return_value=[]):
        argv = plugin.build_container_command(server)
    # NOTE: -profiles=/data/profiles is the new intentional baseline (standard DayZ practice;
    # see dayz.blueprint.json). The snapshot contract was deliberately evolved; this is NOT a regression.
    # Decision: keep the profiles flag (improves server file layout, matches community hosting docs).
    assert argv == ["/data/DayZServer", "-profiles=/data/profiles", "-port=2302"]


def test_dayz_with_mods_matches_legacy_argv() -> None:
    plugin = DayZPlugin()
    server = _stub_server(game_port=2302)
    with patch("games.dayz.plugin.active_mod_ids", return_value=["12345", "67890"]):
        argv = plugin.build_container_command(server)
    assert argv == ["/data/DayZServer", "-profiles=/data/profiles", "-port=2302", "-mod=12345;67890;"]


def test_dayz_without_game_port_omits_port_arg() -> None:
    plugin = DayZPlugin()
    server = _stub_server(game_port=None)
    with patch("games.dayz.plugin.active_mod_ids", return_value=[]):
        argv = plugin.build_container_command(server)
    assert argv == ["/data/DayZServer", "-profiles=/data/profiles"]


def test_conan_full_argv_matches_legacy() -> None:
    plugin = ConanExilesUE5Plugin()
    server = _stub_server(game_port=27015, query_port=27016, rcon_port=27017)
    with patch("games.conan_exiles_ue5.plugin.active_mod_ids", return_value=[]):
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
    plugin = ConanExilesUE5Plugin()
    server = _stub_server(game_port=27015, query_port=None, rcon_port=27017)
    with patch("games.conan_exiles_ue5.plugin.active_mod_ids", return_value=[]):
        argv = plugin.build_container_command(server)
    assert argv == [
        "/bin/bash",
        "/data/ConanSandboxServer.sh",
        "-log",
        "-Port=27015",
        "-RconPort=27017",
    ]


def test_native_plugins_expose_blueprint() -> None:
    """Sanity-Check: beide Plugins gehen ueber ihre Native-Blueprint, nicht
    ueber alten Inline-Code."""
    dayz = DayZPlugin()
    conan = ConanExilesUE5Plugin()
    assert dayz.get_blueprint() is not None
    assert conan.get_blueprint() is not None
    assert dayz.docker_image == dayz.get_blueprint().runtime.image
    assert conan.docker_image == conan.get_blueprint().runtime.image
