"""Tests fuer den Blueprint-Renderer.

Sicherheits-Schwerpunkt: Mod-IDs mit Shell-Sonderzeichen koennen NIE neue
argv-Tokens erzeugen — das Tokenisieren passiert *vor* der Substitution.
"""

from __future__ import annotations

import pytest

from blueprints import render_argv
from blueprints.schema import (
    BlueprintValidationError,
    load_blueprint_dict,
)


def _bp_with_startup(startup: str, mods: dict | None = None) -> dict:
    return {
        "version": 1,
        "meta": {
            "id": "renderer_test",
            "name": "T",
            "category": "steam_game",
            "author": "MSM",
            "description": "",
        },
        "runtime": {
            "image": "alpine",
            "workdir": "/data",
            "env": {},
            "startup": startup,
        },
        "ports": [{"name": "game", "protocol": "udp"}],
        "source": {
            "type": "steam",
            "steam": {"appId": "1", "platform": "linux", "compatibility": "native"},
        },
        "mods": mods,
    }


def test_dayz_renders_without_mods() -> None:
    bp = load_blueprint_dict(_bp_with_startup(
        "/data/DayZServer -port={GAME_PORT} {MOD_ARG}",
        mods={
            "supportsMods": True,
            "supportsSteamWorkshop": True,
            "workshopAppId": "221100",
            "modInjection": "startupArg",
            "modStartupArgumentFormat": "-mod={mods};",
            "modListFilePath": None,
        },
    ))
    argv = render_argv(bp, install_dir="/data", ports={"game": 2302, "query": None, "rcon": None}, active_mod_ids=[])
    assert argv == ["/data/DayZServer", "-port=2302"]


def test_dayz_renders_with_mods() -> None:
    bp = load_blueprint_dict(_bp_with_startup(
        "/data/DayZServer -port={GAME_PORT} {MOD_ARG}",
        mods={
            "supportsMods": True,
            "supportsSteamWorkshop": True,
            "workshopAppId": "221100",
            "modInjection": "startupArg",
            "modStartupArgumentFormat": "-mod={mods};",
            "modListFilePath": None,
        },
    ))
    argv = render_argv(bp, install_dir="/data", ports={"game": 2302, "query": None, "rcon": None}, active_mod_ids=["1234", "5678"])
    assert argv == ["/data/DayZServer", "-port=2302", "-mod=1234;5678;"]


def test_evil_mod_id_cannot_split_argv() -> None:
    """Mod-ID mit '; rm -rf /' darf NICHT als zweites argv-Element auftauchen."""
    bp = load_blueprint_dict(_bp_with_startup(
        "/data/server -port={GAME_PORT} {MOD_ARG}",
        mods={
            "supportsMods": True,
            "supportsSteamWorkshop": True,
            "workshopAppId": "111",
            "modInjection": "startupArg",
            "modStartupArgumentFormat": "-mod={mods}",
            "modListFilePath": None,
        },
    ))
    evil = "1234; rm -rf /"
    argv = render_argv(bp, install_dir="/data", ports={"game": 1, "query": None, "rcon": None}, active_mod_ids=[evil])
    # genau drei Tokens — der ;-Trick erzeugt KEIN viertes argv-Element.
    assert argv == ["/data/server", "-port=1", f"-mod={evil}"]
    assert all("rm" not in tok or "; rm" in tok for tok in argv)


def test_unknown_token_raises() -> None:
    """Renderer akzeptiert NUR Tokens aus der Whitelist."""
    # Schema laesst nur whitelisted Tokens durch — wir testen Defense-in-Depth.
    # Pruefe: Wenn ein Token irgendwie reinkommt, raised der Renderer.
    bp = load_blueprint_dict(_bp_with_startup("/data/server -port={GAME_PORT}"))
    # Direktes Modifizieren des Pydantic-Objekts (Defense-in-Depth-Test).
    object.__setattr__(bp.runtime, "startup", "/data/server -port={UNKNOWN}")
    with pytest.raises(BlueprintValidationError):
        render_argv(bp, install_dir="/data", ports={"game": 1, "query": None, "rcon": None}, active_mod_ids=[])


def test_empty_mod_arg_token_filtered_out() -> None:
    """Wenn {MOD_ARG} leer ist, darf das argv kein '' enthalten."""
    bp = load_blueprint_dict(_bp_with_startup(
        "/data/server {MOD_ARG} -port={GAME_PORT}",
        mods={
            "supportsMods": True,
            "supportsSteamWorkshop": True,
            "workshopAppId": "111",
            "modInjection": "startupArg",
            "modStartupArgumentFormat": "-mod={mods}",
            "modListFilePath": None,
        },
    ))
    argv = render_argv(bp, install_dir="/data", ports={"game": 1, "query": None, "rcon": None}, active_mod_ids=[])
    assert argv == ["/data/server", "-port=1"]


def test_install_dir_substitution() -> None:
    bp = load_blueprint_dict(_bp_with_startup("{INSTALL_DIR}/server -port={GAME_PORT}"))
    argv = render_argv(bp, install_dir="/srv/app", ports={"game": 80, "query": None, "rcon": None})
    assert argv == ["/srv/app/server", "-port=80"]


def test_env_token_substitution() -> None:
    bp = load_blueprint_dict({
        "version": 1,
        "meta": {"id": "env_test", "name": "E", "category": "steam_game", "author": "M", "description": ""},
        "runtime": {
            "image": "alpine",
            "workdir": "/data",
            "env": {"MOTD": "hi"},
            "startup": "/data/server --motd={ENV.MOTD} -port={GAME_PORT}",
        },
        "ports": [{"name": "game", "protocol": "udp"}],
        "source": {"type": "steam", "steam": {"appId": "1", "platform": "linux", "compatibility": "native"}},
        "mods": None,
    })
    argv = render_argv(
        bp,
        install_dir="/data",
        ports={"game": 7777, "query": None, "rcon": None},
        extra_env={"MOTD": "hello"},
    )
    assert argv == ["/data/server", "--motd=hello", "-port=7777"]


def test_workshop_disabled_means_no_mod_arg() -> None:
    bp = load_blueprint_dict(_bp_with_startup(
        "/data/server {MOD_ARG} -port={GAME_PORT}",
        mods={
            "supportsMods": False,
            "supportsSteamWorkshop": False,
            "workshopAppId": None,
            "modInjection": "none",
            "modStartupArgumentFormat": None,
            "modListFilePath": None,
        },
    ))
    argv = render_argv(bp, install_dir="/data", ports={"game": 1, "query": None, "rcon": None}, active_mod_ids=["12345"])
    assert "12345" not in " ".join(argv)
