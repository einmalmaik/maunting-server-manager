"""Tests for the new Guardian Engine (Autopilot) sections in Blueprint schema."""

from __future__ import annotations

import pytest
from blueprints.schema import (
    Blueprint,
    BlueprintValidationError,
    load_blueprint_dict,
)


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
    data["updates"] = {
        "strategy": "snapshot-then-update",
        "health_verification": "required",
        "rollback_on_failure": True,
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
    assert bp.updates.strategy == "snapshot-then-update"
    assert bp.updates.health_verification == "required"
    assert bp.updates.rollback_on_failure is True
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
