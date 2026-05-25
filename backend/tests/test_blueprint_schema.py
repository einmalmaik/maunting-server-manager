"""Tests fuer das Blueprint-Schema.

Schwerpunkt: Sicherheits-Invarianten (Shell-Metas, Pfad-Escape, HTTPS-only,
Port-Konsistenz). Happy-Path-Coverage ueber das native DayZ/Conan-JSON.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from blueprints.schema import (
    EMPTY_TEMPLATE,
    Blueprint,
    BlueprintValidationError,
    load_blueprint_dict,
    load_blueprint_file,
)


_NATIVE_DIR = Path(__file__).resolve().parents[1] / "blueprints" / "native"


def _minimal_valid_dict() -> dict:
    return {
        "version": 1,
        "meta": {
            "id": "test_bp",
            "name": "Test",
            "category": "steam_game",
            "author": "MSM",
            "description": "",
        },
        "runtime": {
            "image": "cm2network/steamcmd:root",
            "workdir": "/data",
            "env": {},
            "startup": "/data/server -port={GAME_PORT}",
        },
        "ports": [
            {"name": "game", "protocol": "udp"},
        ],
        "source": {
            "type": "steam",
            "steam": {"appId": "12345", "platform": "linux", "compatibility": "native"},
        },
        "mods": None,
    }


# ── Happy Path ────────────────────────────────────────────────────────────


def test_native_dayz_validates() -> None:
    blueprint = load_blueprint_file(_NATIVE_DIR / "dayz.blueprint.json")
    assert blueprint.meta.id == "dayz"
    assert blueprint.runtime.image == "cm2network/steamcmd:root"
    assert blueprint.effective_mods().modInjection.value == "startupArg"


def test_native_conan_validates() -> None:
    blueprint = load_blueprint_file(_NATIVE_DIR / "conan_exiles_ue5.blueprint.json")
    assert blueprint.meta.id == "conan_exiles_ue5"
    bp_mods = blueprint.effective_mods()
    assert bp_mods.modInjection.value == "file"
    assert bp_mods.modListFilePath == "ConanSandbox/Mods/modlist.txt"


def test_minimal_blueprint_is_valid() -> None:
    bp = load_blueprint_dict(_minimal_valid_dict())
    assert isinstance(bp, Blueprint)
    assert bp.source.type.value == "steam"


def test_empty_template_round_trip() -> None:
    """Das Default-Template hat leere Strings — es darf NICHT validieren
    (User soll bewusst Felder ausfuellen). Stattdessen testen wir, dass eine
    minimal ausgefuellte Variante davon validiert."""
    template = deepcopy(EMPTY_TEMPLATE)
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(template)


# ── Shell-Metas ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("startup", [
    "/data/server -port={GAME_PORT}; rm -rf /",  # nur ; — jetzt erlaubt (argv-safe)
])
def test_startup_with_argv_safe_meta_is_allowed(startup: str) -> None:
    """``;`` ist in argv harmlos (kein sh -c). Schema laesst das durch."""
    d = _minimal_valid_dict()
    d["runtime"]["startup"] = startup
    bp = load_blueprint_dict(d)
    assert bp.runtime.startup == startup


@pytest.mark.parametrize("startup", [
    "/data/server $HOME",                       # $-Substitution
    "/data/server `id`",                        # backtick command sub
    "/data/server -port={GAME_PORT} $(whoami)", # $( substitution
    "/data/server ${EVIL}",                     # ${ substitution
    "/data/server && id",                       # && sequence
    "/data/server || id",                       # || sequence
])
def test_startup_with_substitution_meta_rejected(startup: str) -> None:
    d = _minimal_valid_dict()
    d["runtime"]["startup"] = startup
    with pytest.raises(BlueprintValidationError) as exc:
        load_blueprint_dict(d)
    assert any("Shell" in e or "shell" in e for e in exc.value.errors)


def test_startup_unknown_token_rejected() -> None:
    d = _minimal_valid_dict()
    d["runtime"]["startup"] = "/data/server -port={SECRET_TOKEN}"
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


# ── Pfade / Sicherheits-Validatoren ────────────────────────────────────────


def test_modlist_path_dotdot_rejected() -> None:
    d = _minimal_valid_dict()
    d["mods"] = {
        "supportsMods": True,
        "supportsSteamWorkshop": True,
        "workshopAppId": "111",
        "modInjection": "file",
        "modStartupArgumentFormat": None,
        "modListFilePath": "../../etc/passwd",
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_modlist_path_absolute_rejected() -> None:
    d = _minimal_valid_dict()
    d["mods"] = {
        "supportsMods": True,
        "supportsSteamWorkshop": True,
        "workshopAppId": "111",
        "modInjection": "file",
        "modStartupArgumentFormat": None,
        "modListFilePath": "/etc/passwd",
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_http_url_must_be_https() -> None:
    d = _minimal_valid_dict()
    d["source"] = {
        "type": "http",
        "http": {"url": "http://example.org/server.zip", "archiveType": "zip"},
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_http_url_https_accepted() -> None:
    d = _minimal_valid_dict()
    d["source"] = {
        "type": "http",
        "http": {"url": "https://example.org/server.zip", "archiveType": "zip"},
    }
    bp = load_blueprint_dict(d)
    assert bp.source.http is not None
    assert bp.source.http.url.startswith("https://")


def test_extract_to_dotdot_rejected() -> None:
    d = _minimal_valid_dict()
    d["source"] = {
        "type": "http",
        "http": {"url": "https://example.org/x.zip", "extractTo": "../escape"},
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


# ── Ports / Konsistenz ─────────────────────────────────────────────────────


def test_duplicate_port_role_rejected() -> None:
    d = _minimal_valid_dict()
    d["ports"] = [
        {"name": "game", "protocol": "udp"},
        {"name": "game", "protocol": "udp"},
    ]
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_unknown_field_rejected() -> None:
    d = _minimal_valid_dict()
    d["evilField"] = "x"
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_unknown_field_in_meta_rejected() -> None:
    d = _minimal_valid_dict()
    d["meta"]["secretKey"] = "deadbeef"  # noqa: S105 — test of attribute, not a real secret
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_unsupported_version_rejected() -> None:
    d = _minimal_valid_dict()
    d["version"] = 99
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_steam_windows_requires_compat() -> None:
    d = _minimal_valid_dict()
    d["source"]["steam"]["platform"] = "windows"
    d["source"]["steam"]["compatibility"] = "native"
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_mods_startup_format_requires_placeholder() -> None:
    d = _minimal_valid_dict()
    d["mods"] = {
        "supportsMods": True,
        "supportsSteamWorkshop": True,
        "workshopAppId": "111",
        "modInjection": "startupArg",
        "modStartupArgumentFormat": "-mod=NOPLACEHOLDER",
        "modListFilePath": None,
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_workshop_app_id_required_when_workshop_on() -> None:
    d = _minimal_valid_dict()
    d["mods"] = {
        "supportsMods": True,
        "supportsSteamWorkshop": True,
        "workshopAppId": None,
        "modInjection": "none",
        "modStartupArgumentFormat": None,
        "modListFilePath": None,
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d)


def test_template_is_valid_json() -> None:
    # EMPTY_TEMPLATE muss zumindest serialisierbar sein.
    payload = json.dumps(EMPTY_TEMPLATE)
    assert "version" in payload
    assert "modInjection" in payload
