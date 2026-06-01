from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
import pytest

from blueprints.schema import load_blueprint_dict, BlueprintValidationError
from games.blueprint_plugin import BlueprintPlugin


@pytest.fixture
def minimal_blueprint_dict() -> dict:
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
            "configPatches": [],
        },
        "ports": [
            {"name": "game", "protocol": "udp"},
            {"name": "query", "protocol": "udp"},
        ],
        "source": {
            "type": "steam",
            "steam": {"appId": "12345", "platform": "linux", "compatibility": "native"},
        },
    }


def test_regex_patch_schema_validates(minimal_blueprint_dict) -> None:
    minimal_blueprint_dict["runtime"]["configPatches"] = [
        {
            "type": "regex",
            "file": "serverDZ.cfg",
            "regex": r"(steamQueryPort\s*=\s*)\d+;",
            "value": r"\g<1>{QUERY_PORT};",
        }
    ]
    bp = load_blueprint_dict(minimal_blueprint_dict)
    assert bp.runtime.configPatches[0].type.value == "regex"
    assert bp.runtime.configPatches[0].regex == r"(steamQueryPort\s*=\s*)\d+;"
    assert bp.runtime.configPatches[0].section is None
    assert bp.runtime.configPatches[0].key is None


def test_regex_patch_schema_missing_regex_fails(minimal_blueprint_dict) -> None:
    minimal_blueprint_dict["runtime"]["configPatches"] = [
        {
            "type": "regex",
            "file": "serverDZ.cfg",
            "value": "2303",
        }
    ]
    with pytest.raises(BlueprintValidationError) as exc:
        load_blueprint_dict(minimal_blueprint_dict)
    assert any("regex" in e for e in exc.value.errors)


def test_regex_patch_schema_specifying_section_or_key_fails(minimal_blueprint_dict) -> None:
    # 1) Specifying section
    d1 = minimal_blueprint_dict.copy()
    d1["runtime"]["configPatches"] = [
        {
            "type": "regex",
            "file": "serverDZ.cfg",
            "regex": r"port",
            "section": "Server",
            "value": "2303",
        }
    ]
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d1)

    # 2) Specifying key
    d2 = minimal_blueprint_dict.copy()
    d2["runtime"]["configPatches"] = [
        {
            "type": "regex",
            "file": "serverDZ.cfg",
            "regex": r"port",
            "key": "port",
            "value": "2303",
        }
    ]
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(d2)


def test_regex_patch_schema_invalid_regex_fails(minimal_blueprint_dict) -> None:
    minimal_blueprint_dict["runtime"]["configPatches"] = [
        {
            "type": "regex",
            "file": "serverDZ.cfg",
            "regex": "[invalid regex",
            "value": "2303",
        }
    ]
    with pytest.raises(BlueprintValidationError) as exc:
        load_blueprint_dict(minimal_blueprint_dict)
    assert any("regulaerer Ausdruck" in e or "regex" in e for e in exc.value.errors)


def test_regex_patch_execution_replaces_text(tmp_path, minimal_blueprint_dict) -> None:
    # Create a dummy config file
    config_file = tmp_path / "serverDZ.cfg"
    config_file.write_text("hostname = \"My DayZ Server\";\nsteamQueryPort = 2302;\nmaxPlayers = 60;", encoding="utf-8")

    # Define blueprint with regex patch
    minimal_blueprint_dict["runtime"]["configPatches"] = [
        {
            "type": "regex",
            "file": "serverDZ.cfg",
            "regex": r"(steamQueryPort\s*=\s*)\d+;",
            "value": r"\g<1>{QUERY_PORT};",
        },
        {
            "type": "regex",
            "file": "serverDZ.cfg",
            "regex": r"(hostname\s*=\s*\")[^\"]+(\";)",
            "value": r"\g<1>MSM Patched Server\g<2>",
        }
    ]

    bp = load_blueprint_dict(minimal_blueprint_dict)
    plugin = BlueprintPlugin(bp)

    server = SimpleNamespace(
        id=1,
        install_dir=str(tmp_path),
        game_port=2302,
        query_port=27016,
        rcon_port=None,
        public_bind_ip=None,
        ports=[
            SimpleNamespace(role="game", port=2302, protocol="udp"),
            SimpleNamespace(role="query", port=27016, protocol="udp"),
        ]
    )

    plugin.prepare_runtime(server)

    patched_content = config_file.read_text(encoding="utf-8")
    assert "steamQueryPort = 27016;" in patched_content
    assert "hostname = \"MSM Patched Server\";" in patched_content
    assert "maxPlayers = 60;" in patched_content
