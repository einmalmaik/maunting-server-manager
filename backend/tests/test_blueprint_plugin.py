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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blueprints.schema import load_blueprint_dict, load_blueprint_file
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


def test_runtime_workdir_controls_mount_workdir_and_install_dir_token() -> None:
    """Blueprint-Images wie Pterodactyl-Yolks erwarten die Dateien unter
    ``/home/container``. Dann muss MSM den Server-Ordner auch dort mounten und
    ``{INSTALL_DIR}`` auf denselben Container-Pfad rendern.
    """
    bp_dict = _mc_paper_blueprint()
    bp_dict["runtime"]["workdir"] = "/home/container"
    bp_dict["runtime"]["startup"] = "{INSTALL_DIR}/start.sh"
    bp = load_blueprint_dict(bp_dict)
    plugin = BlueprintPlugin(bp)
    server = _FakeServer(install_dir="/srv/msm/server-1")

    with patch("games.blueprint_plugin.active_mod_ids", return_value=[]):
        assert plugin.build_container_command(server) == ["/home/container/start.sh"]

    volumes = plugin.build_volume_binds(server)
    assert len(volumes) == 1
    assert volumes[0].host_path == "/srv/msm/server-1"
    assert volumes[0].container_path == "/home/container"
    assert volumes[0].read_only is False
    assert plugin.container_workdir(server) == "/home/container"


def test_windows_steam_compatibility_wraps_exe_with_wine() -> None:
    """``platform=windows`` steuert nur SteamCMD. Fuer den Container-Start
    braucht eine .exe einen Windows-Kompatibilitaetsrunner.
    """
    bp_dict = {
        "version": 1,
        "meta": {"id": "windows_test", "name": "Windows Test", "category": "steam_game"},
        "runtime": {
            "image": "ghcr.io/ptero-eggs/yolks:wine_staging",
            "workdir": "/home/container",
            "env": {"MAX_PLAYERS": "64"},
            "startup": "./Server/Binaries/Win64/GameServer.exe -port={GAME_PORT} -MaxPlayers={ENV.MAX_PLAYERS}",
        },
        "ports": [{"name": "game", "protocol": "udp"}],
        "source": {
            "type": "steam",
            "steam": {
                "appId": "123",
                "platform": "windows",
                "compatibility": "proton",
                "requiresLogin": False,
            },
        },
        "mods": None,
    }
    plugin = BlueprintPlugin(load_blueprint_dict(bp_dict))

    with patch("games.blueprint_plugin.active_mod_ids", return_value=[]):
        argv = plugin.build_container_command(_FakeServer(game_port=7777))

    assert argv == [
        "wine",
        "./Server/Binaries/Win64/GameServer.exe",
        "-port=7777",
        "-MaxPlayers=64",
    ]


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


def _native_plugin(blueprint_id: str) -> BlueprintPlugin:
    path = Path(__file__).resolve().parents[1] / "blueprints" / "native" / f"{blueprint_id}.blueprint.json"
    return BlueprintPlugin(load_blueprint_file(path))


def test_dayz_blueprint_post_install_symlinks_mod_and_keys(tmp_path) -> None:
    plugin = _native_plugin("dayz")
    server = _FakeServer(id=77, install_dir=str(tmp_path))
    workshop_dir = tmp_path / "steamapps" / "workshop" / "content" / "221100" / "12345"
    keys_dir = workshop_dir / "keys"
    keys_dir.mkdir(parents=True)
    key = keys_dir / "test.bikey"
    key.write_text("key", encoding="utf-8")

    with patch("games.blueprint_plugin.run_steamcmd_workshop_download", return_value={"ok": True}), \
         patch.object(plugin, "update_modlist"):
        result = plugin.install_mod(server, "12345")

    assert result == {}
    assert (tmp_path / "12345").is_symlink()
    assert (tmp_path / "keys" / "test.bikey").is_symlink()


def test_conan_blueprint_post_install_copies_paks_and_formats_modlist(tmp_path) -> None:
    plugin = _native_plugin("conan_exiles_ue5")
    server = _FakeServer(id=78, install_dir=str(tmp_path))
    workshop_dir = tmp_path / "steamapps" / "workshop" / "content" / "440900" / "999" / "nested"
    workshop_dir.mkdir(parents=True)
    pak = workshop_dir / "Example.pak"
    pak.write_text("pak", encoding="utf-8")

    with patch("games.blueprint_plugin.run_steamcmd_workshop_download", return_value={"ok": True}), \
         patch.object(plugin, "update_modlist"):
        result = plugin.install_mod(server, "999")

    copied = tmp_path / "ConanSandbox" / "Mods" / "Example.pak"
    assert result == {}
    assert copied.read_text(encoding="utf-8") == "pak"
    assert plugin.format_modlist_lines(server, [SimpleNamespace(workshop_id="999")]) == ["Example.pak"]
