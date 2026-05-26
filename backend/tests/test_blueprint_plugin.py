"""Tests fuer den generischen ``BlueprintPlugin``.

Schwerpunkt:

1. ``build_port_publishes`` muss das Protokoll aus der Blueprint lesen — nicht
   pauschal UDP wie der Default. Sonst funktionieren TCP-Spiele (Minecraft) in
   nativer und Community-Form nicht.
2. ``build_container_env`` muss Port-Tokens in Env-Werten aufloesen — sonst
   bekommt z. B. ``itzg/minecraft-server`` die Variable ``SERVER_PORT={GAME_PORT}``
   literal und bindet einen falschen Port.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import patch

from blueprints.schema import load_blueprint_dict
from games.blueprint_plugin import BlueprintPlugin


@dataclass
class _FakeServer:
    """Minimal-Server-Stub fuer Plugin-Tests."""
    id: int = 1
    install_dir: str = "/srv/test"
    game_port: int | None = 25566
    query_port: int | None = None
    rcon_port: int | None = 25579
    public_bind_ip: str | None = "192.0.2.10"


def _mc_paper_blueprint() -> dict:
    return {
        "version": 1,
        "meta": {
            "id": "mc_paper_test",
            "name": "MC Paper Test",
            "category": "non_steam_game",
        },
        "runtime": {
            "image": "itzg/minecraft-server:latest",
            "workdir": "/data",
            "env": {
                "EULA": "TRUE",
                "TYPE": "PAPER",
                "SERVER_PORT": "{GAME_PORT}",
                "RCON_PORT": "{RCON_PORT}",
            },
            "startup": "/start",
        },
        "ports": [
            {"name": "game", "protocol": "tcp"},
            {"name": "rcon", "protocol": "tcp"},
        ],
        "source": {"type": "dockerOnly", "steam": None, "http": None},
        "mods": None,
    }


def test_build_port_publishes_honors_blueprint_protocol() -> None:
    """Minecraft = TCP. Der Default-Plugin-Pfad publiziert UDP — der Blueprint-
    Override muss TCP zurueckgeben."""
    bp = load_blueprint_dict(_mc_paper_blueprint())
    plugin = BlueprintPlugin(bp)
    publishes = plugin.build_port_publishes(_FakeServer())

    by_port = {p.host_port: p for p in publishes}
    assert by_port[25566].protocol == "tcp"
    assert by_port[25579].protocol == "tcp"
    # Bind-IP-Pflicht aus games.base bleibt erhalten — kein 0.0.0.0-Leak.
    assert all(p.host_ip == "192.0.2.10" for p in publishes)


def test_build_port_publishes_skips_unassigned_roles() -> None:
    """Wenn ``query_port`` None ist, darf keine Query-Publish entstehen."""
    bp = load_blueprint_dict(_mc_paper_blueprint())
    plugin = BlueprintPlugin(bp)
    publishes = plugin.build_port_publishes(_FakeServer(query_port=None))
    assert all(p.host_port != 0 for p in publishes)
    assert {p.host_port for p in publishes} == {25566, 25579}


def test_build_container_env_substitutes_port_tokens() -> None:
    """``SERVER_PORT={GAME_PORT}`` muss zu der konkreten Portnummer werden."""
    bp = load_blueprint_dict(_mc_paper_blueprint())
    plugin = BlueprintPlugin(bp)
    env = plugin.build_container_env(_FakeServer())
    assert env["SERVER_PORT"] == "25566"
    assert env["RCON_PORT"] == "25579"
    assert env["EULA"] == "TRUE"
    assert env["TYPE"] == "PAPER"


def test_docker_only_install_writes_console_feedback(tmp_path, monkeypatch) -> None:
    """Docker-only Install darf nicht stumm sein.

    Bug-Report (User, 2026-05): "Hytale/Minecraft startet den Install nicht".
    Tatsaechlich ist der Install fuer ``source.type == dockerOnly`` ein No-op
    (Image bringt alles mit). Ohne sichtbare Console-Zeile wirkt das im UI so,
    als waere nichts passiert. Diese Regression-Sperre garantiert eine
    klare Feedback-Zeile.
    """
    from games.base import _console_log_path

    bp_dict = _mc_paper_blueprint()
    bp_dict["meta"]["id"] = "minecraft_paper_feedback"
    bp = load_blueprint_dict(bp_dict)
    plugin = BlueprintPlugin(bp)

    server = _FakeServer(id=4242)
    # Sicherheit: vorhandenes Log-File leeren, damit der Test wirklich nur die
    # neue Zeile sieht.
    log_path = _console_log_path(server.id)
    if os.path.exists(log_path):
        os.remove(log_path)

    try:
        with patch("games.blueprint_plugin.finish_install") as mock_finish:
            result = plugin.install(server)

        assert "Installation nicht erforderlich" in result["message"]
        mock_finish.assert_called_once_with(server.id, {"ok": True})

        # Console-Log muss eine eindeutige Notiz enthalten — der User soll sehen,
        # dass der Klick auf "Installieren" verarbeitet wurde.
        with open(log_path, "r", encoding="utf-8") as f:
            log = f.read()
        assert "Docker-only" in log
        assert "bereit zum Starten" in log
        assert bp.meta.id in log
    finally:
        if os.path.exists(log_path):
            os.remove(log_path)


def test_native_minecraft_blueprints_load() -> None:
    """Smoke-Test: alle native Minecraft-Varianten + Hytale validieren gegen
    das Schema und sind via BlueprintPlugin instanziierbar."""
    import glob
    import json
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    paths = sorted(glob.glob(str(backend_dir / "blueprints/native/*.blueprint.json")))
    minecraft_ids = []
    for path in paths:
        bp = load_blueprint_dict(json.load(open(path)))
        plugin = BlueprintPlugin(bp)
        assert plugin.game_id == bp.meta.id
        assert plugin.docker_image == bp.runtime.image
        if bp.meta.id.startswith("minecraft_"):
            minecraft_ids.append(bp.meta.id)

    # Vorgegebene Auswahl aus dem Plan — Regression-Schutz, falls jemand eine
    # Variante loescht oder umbenennt.
    expected = {
        "minecraft_vanilla", "minecraft_paper", "minecraft_spigot",
        "minecraft_purpur", "minecraft_fabric", "minecraft_forge",
        "minecraft_neoforge", "minecraft_sponge",
    }
    assert set(minecraft_ids) == expected
