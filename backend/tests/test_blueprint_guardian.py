"""Tests for the new Guardian Engine (Autopilot) sections in Blueprint schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from blueprints.schema import (
    Blueprint,
    BlueprintValidationError,
    load_blueprint_dict,
)
from models import Server, ServerPort
from services.guardian_runtime_compiler import compile_desired_state, compile_guardian_config


def _base_valid_dict() -> dict:
    return {
        "version": 1,
        "meta": {
            "id": "guardian_test_bp",
            "name": "Guardian Test",
            "category": "bot",
            "author": "MSM",
            "description": "",
        },
        "runtime": {
            "image": "node:22-bookworm-slim",
            "workdir": "/data",
            "env": {},
            "startup": "node index.js",
        },
        "ports": [],
        "source": {
            "type": "dockerOnly",
            "updateStrategy": "none",
        },
    }


def test_shared_maximal_guardian_blueprint_compiles_without_schema_loss() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "guardian_blueprint_maximal.json"
    )
    source = json.loads(fixture.read_text(encoding="utf-8"))

    blueprint = load_blueprint_dict(source)

    assert blueprint.model_dump(mode="json", exclude_none=True) == source


@pytest.mark.parametrize("blueprint_id", ["farming_simulator_22", "fivem"])
def test_native_application_probe_uses_allocated_game_port(blueprint_id: str) -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "blueprints"
        / "native"
        / f"{blueprint_id}.blueprint.json"
    )
    blueprint = load_blueprint_dict(json.loads(path.read_text(encoding="utf-8")))
    server = Server(
        id=101,
        name="Native Guardian compile",
        game_type=blueprint_id,
        install_dir="/tmp/native-guardian",
        public_bind_ip="127.0.0.1",
    )
    server.ports = [
        ServerPort(role="game", port=30120, protocol="tcp"),
        ServerPort(role="query", port=30121, protocol="udp"),
    ]

    guardian = compile_guardian_config(server, blueprint)

    application = next(
        check for check in guardian["health_checks"] if check["check_id"] == "application"
    )
    assert application["target_port"] == 30120


@pytest.mark.parametrize(
    "path",
    sorted(
        (Path(__file__).resolve().parents[1] / "blueprints" / "native").glob(
            "*.blueprint.json"
        )
    ),
    ids=lambda path: path.stem,
)
def test_every_native_guardian_contract_compiles(path: Path) -> None:
    blueprint = load_blueprint_dict(json.loads(path.read_text(encoding="utf-8")))
    server = Server(
        id=102,
        name="Native Guardian matrix",
        game_type=blueprint.meta.id,
        install_dir="/tmp/native-guardian-matrix",
        public_bind_ip="127.0.0.1",
    )
    server.ports = [
        ServerPort(
            role=port.name.value,
            port=31000 + index,
            protocol=port.protocol.value,
        )
        for index, port in enumerate(blueprint.ports)
    ]

    compiled = compile_guardian_config(server, blueprint)

    assert compiled["health_checks"]


@pytest.mark.parametrize(
    "path",
    sorted(
        (Path(__file__).resolve().parents[2] / "docs" / "templates").glob(
            "*.blueprint.json"
        )
    ),
    ids=lambda path: path.stem,
)
def test_documented_blueprint_examples_parse_and_compile(path: Path) -> None:
    blueprint = load_blueprint_dict(json.loads(path.read_text(encoding="utf-8")))
    server = Server(
        id=103,
        name="Documented Guardian example",
        game_type=blueprint.meta.id,
        install_dir="/tmp/documented-guardian",
        public_bind_ip="127.0.0.1",
        desired_power_state="running",
        desired_state_generation=1,
    )
    server.ports = [
        ServerPort(
            role=port.name.value,
            port=32000 + index,
            protocol=port.protocol.value,
        )
        for index, port in enumerate(blueprint.ports)
    ]

    assert compile_guardian_config(server, blueprint)["health_checks"]
    desired = compile_desired_state(server, blueprint)
    assert desired["guardian_enabled"] is (path.name != "generic_github_bot.blueprint.json")


def test_valid_blueprint_with_guardian_sections() -> None:
    data = _base_valid_dict()
    data["health"] = {
        "process": {"required": True},
        "port": {
            "protocol": "tcp",
            "port": "{{SERVER_PORT}}",
            "timeout": "5s",
        },
        "application": {
            "type": "minecraft-query",
            "id": "game-query",
            "port": "{{PORT:query}}",
            "interval": "45s",
            "timeout": "5s",
            "failure_threshold": 5,
            "success_threshold": 2,
        },
        "startup": {
            "grace_period_seconds": 20,
            "timeout_seconds": 240,
            "success_patterns": [r"Done \("],
            "failure_patterns": ["Failed to bind to port"],
        },
    }
    data["logs"] = {
        "sources": ["stdout", "logs/*.log"],
        "redact": ["discord_token"],
    }
    data["diagnostics"] = {
        "parsers": ["java-stacktrace", "linux-oom"],
    }
    data["recovery"] = {
        "policies": [
            {"match": "port-conflict", "action": "clear_declared_lock_files"},
            {"match": "linux-oom", "action": "graceful_restart"},
        ],
        "safe_lock_files": [
            {"path": "runtime/server.lock", "reason": "Known stale application lock"}
        ],
        "max_attempts": 3,
        "attempt_window_seconds": 1800,
        "cooldown_seconds": 300,
        "verification": {
            "minimum_healthy_duration_seconds": 30,
            "required_consecutive_successes": 3,
            "verification_timeout_seconds": 180,
        },
    }
    data["backups"] = {
        "before_risky_action": True,
        "protected_paths": ["config/", "saves/"],
    }

    bp = load_blueprint_dict(data)
    assert isinstance(bp, Blueprint)
    assert bp.health is not None
    assert bp.health.process.required is True
    assert bp.health.port.protocol == "tcp"
    assert bp.health.port.port == "{{SERVER_PORT}}"
    assert bp.health.port.timeout == "5s"
    assert bp.health.application.type == "minecraft-query"
    assert bp.health.application.interval == "45s"
    assert bp.health.application.failure_threshold == 5
    assert bp.health.startup.success_patterns == [r"Done \("]
    assert bp.health.startup.failure_patterns == ["Failed to bind to port"]
    assert bp.logs.sources == ["stdout", "logs/*.log"]
    assert bp.logs.redact == ["discord_token"]
    assert bp.diagnostics.parsers == ["java-stacktrace", "linux-oom"]
    assert len(bp.recovery.policies) == 2
    assert bp.recovery.policies[0].match == "port-conflict"
    assert bp.recovery.policies[0].action == "clear_declared_lock_files"
    assert bp.recovery.safe_lock_files[0].path == "runtime/server.lock"
    assert bp.recovery.verification.required_consecutive_successes == 3
    assert bp.backups.before_risky_action is True
    assert bp.backups.protected_paths == ["config/", "saves/"]


def test_invalid_recovery_policy_throws() -> None:
    data = _base_valid_dict()
    data["recovery"] = {
        "policies": [
            {"match": "   ", "action": "resolve_managed_port_conflict"},  # Empty match after strip
        ]
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(data)


def test_unknown_recovery_match_is_rejected_instead_of_becoming_inert() -> None:
    data = _base_valid_dict()
    data["recovery"] = {
        "policies": [{"match": "typoed_probe_failure", "action": "restart"}]
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(data)


def test_unimplemented_update_rollback_contract_is_rejected() -> None:
    data = _base_valid_dict()
    data["updates"] = {
        "strategy": "snapshot-then-update",
        "health_verification": "required",
        "rollback_on_failure": True,
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(data)


@pytest.mark.parametrize(
    "action",
    ["resolve_managed_port_conflict", "rollback_release", "arbitrary_shell"],
)
def test_unsupported_recovery_actions_are_rejected(action: str) -> None:
    data = _base_valid_dict()
    data["recovery"] = {"policies": [{"match": "port-conflict", "action": action}]}
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(data)


def test_unknown_probe_and_diagnostic_parser_are_rejected() -> None:
    data = _base_valid_dict()
    data["health"] = {"application": {"type": "custom-script"}}
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(data)

    data = _base_valid_dict()
    data["diagnostics"] = {"parsers": ["run-any-command"]}
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(data)


@pytest.mark.parametrize(
    "path",
    ["*.lock", "runtime/**/*.lock", "../server.lock", "/tmp/server.lock", "runtime\\server.lock"],
)
def test_unsafe_lock_file_declarations_are_rejected(path: str) -> None:
    data = _base_valid_dict()
    data["recovery"] = {
        "safe_lock_files": [{"path": path, "reason": "synthetic"}],
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(data)


def test_invalid_protected_paths_throws() -> None:
    data = _base_valid_dict()
    data["backups"] = {
        "before_risky_action": True,
        "protected_paths": ["/absolute/path"],  # Unsafe path
    }
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(data)
