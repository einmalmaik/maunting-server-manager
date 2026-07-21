"""Compile Blueprint Guardian intent into the concrete Agent runtime contract."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Any

from blueprints.schema import Blueprint
from models import Server


GUARDIAN_SCHEMA_VERSION = 1
SUPPORTED_PROBES = frozenset(
    {
        "process",
        "tcp",
        "udp_port_mapping",
        "http-ping",
        "minecraft-status",
        "minecraft-query",
        "source-query",
    }
)
SUPPORTED_DIAGNOSTICS = frozenset(
    {
        "linux-oom",
        "java-stacktrace",
        "nodejs-stacktrace",
        "port-conflict",
        "missing-runtime",
        "corrupted-config",
        "startup-pattern",
    }
)
SUPPORTED_ACTIONS = frozenset(
    {"restart", "graceful_restart", "clear_declared_lock_files", "quarantine"}
)
_TOKEN_RE = re.compile(r"{{(SERVER_PORT|PORT:([a-zA-Z0-9_.-]{1,64}))}}")
_ANY_TOKEN_RE = re.compile(r"{{[^{}]+}}")


class GuardianCompileError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class GuardianRequirements:
    schema_version: int
    probe_types: frozenset[str]
    diagnostic_parsers: frozenset[str]
    recovery_actions: frozenset[str]


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key not in {"payload_hash", "sent_at"}
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _duration_seconds(value: str, *, minimum: float = 0.1, maximum: float = 3600) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)", str(value).strip())
    if not match:
        raise GuardianCompileError("invalid_duration", f"Invalid Guardian duration: {value}")
    amount = float(match.group(1))
    unit = match.group(2)
    seconds = amount / 1000 if unit == "ms" else amount * 60 if unit == "m" else amount
    if seconds < minimum or seconds > maximum:
        raise GuardianCompileError("invalid_duration", f"Guardian duration outside safe bounds: {value}")
    return seconds


def _port_map(server: Server) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in getattr(server, "ports", []) or []:
        role = str(getattr(item, "role", "") or "").strip()
        port = getattr(item, "port", None)
        if role and isinstance(port, int) and 1 <= port <= 65535:
            result[role] = port
    # Fallback to server properties if missing from ports relation
    if "game" not in result:
        g = getattr(server, "game_port", None)
        if isinstance(g, int) and 1 <= g <= 65535:
            result["game"] = g
    if "query" not in result:
        q = getattr(server, "query_port", None)
        if isinstance(q, int) and 1 <= q <= 65535:
            result["query"] = q
    if "rcon" not in result:
        r = getattr(server, "rcon_port", None)
        if isinstance(r, int) and 1 <= r <= 65535:
            result["rcon"] = r
    return result


def _resolve_tokens(value: str, ports: dict[str, int]) -> str:
    # 1. Find all placeholders matching {{...}}
    placeholders = re.findall(r"{{([^{}]+)}}", value)
    for raw_placeholder in placeholders:
        placeholder = raw_placeholder.strip()
        role = None
        
        # Check standard whitelisted direct placeholders
        if placeholder in ("SERVER_PORT", "GAME_PORT"):
            role = "game"
        elif placeholder == "QUERY_PORT":
            role = "query"
        elif placeholder == "RCON_PORT":
            role = "rcon"
        elif placeholder == "VOICE_PORT":
            role = "voice"
        elif placeholder == "WEB_PORT":
            role = "web"
        elif placeholder.startswith("CUSTOM_PORT_"):
            num = placeholder[12:]
            if num.isdigit():
                role = f"custom_port_{num}"
                if role not in ports and f"custom_{num}" in ports:
                    role = f"custom_{num}"
            else:
                raise GuardianCompileError(
                    "unresolved_placeholder",
                    f"Guardian placeholder CUSTOM_PORT_ suffix must be numeric: {placeholder}",
                )
        # Check legacy PORT:role syntax if role is in whitelist
        elif placeholder.startswith("PORT:"):
            inner = placeholder[5:]
            if inner in ("game", "query", "rcon", "voice", "web"):
                role = inner
            elif inner.startswith("custom_port_") and inner[12:].isdigit():
                role = inner
            elif inner.startswith("custom_") and inner[7:].isdigit():
                role = inner
            else:
                raise GuardianCompileError(
                    "unresolved_placeholder",
                    f"Guardian placeholder role is not in the whitelist: {inner}",
                )
        else:
            raise GuardianCompileError(
                "unresolved_placeholder",
                f"Guardian placeholder is not whitelisted: {placeholder}",
            )
            
        port = ports.get(role)
        if port is None:
            raise GuardianCompileError(
                "unresolved_placeholder",
                f"Guardian port role is not allocated: {role}",
                details={"role": role},
            )
        
        value = value.replace(f"{{{{{raw_placeholder}}}}}", str(port))
        
    if "{{" in value or "}}" in value:
        raise GuardianCompileError(
            "unresolved_placeholder",
            "Guardian configuration contains an unsupported or stray placeholder",
        )
    return value


def _resolved_port(value: str, ports: dict[str, int]) -> int:
    resolved = _resolve_tokens(value, ports)
    if not resolved.isdigit() or not 1 <= int(resolved) <= 65535:
        raise GuardianCompileError("invalid_probe_port", "Guardian probe port is invalid")
    return int(resolved)


def _target_host(server: Server) -> str:
    value = str(getattr(server, "public_bind_ip", "") or "").strip()
    if not value:
        raise GuardianCompileError(
            "probe_target_unavailable",
            "A concrete public_bind_ip is required for host-bound Guardian probes",
        )
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise GuardianCompileError("invalid_probe_target", "Guardian probe target is invalid") from exc
    if address.is_unspecified:
        return "127.0.0.1" if address.version == 4 else "::1"
    if address.is_link_local or address.is_multicast or address.is_reserved:
        raise GuardianCompileError("invalid_probe_target", "Guardian probe target is unsafe")
    return str(address)


def _application_port(application: Any, ports: dict[str, int]) -> int:
    if application.port:
        return _resolved_port(application.port, ports)
        
    if application.type == "minecraft-status":
        port = ports.get("game")
        if port is None:
            raise GuardianCompileError("probe_port_unavailable", "minecraft-status requires game port")
        return port
    elif application.type == "minecraft-query":
        port = ports.get("query")
        if port is None:
            port = ports.get("game")
        if port is None:
            raise GuardianCompileError("probe_port_unavailable", "minecraft-query requires query or game port")
        return port
    elif application.type == "source-query":
        port = ports.get("query")
        if port is None:
            port = ports.get("game")
        if port is None:
            raise GuardianCompileError("probe_port_unavailable", "source-query requires query or game port")
        return port
    elif application.type == "http-ping":
        port = ports.get("web")
        if port is None:
            raise GuardianCompileError("probe_port_unavailable", "http-ping requires web port or an explicit port")
        return port
    else:
        default_roles = {
            "tcp": "game",
            "udp_port_mapping": "game",
        }
        role = default_roles.get(application.type, "game")
        port = ports.get(role)
        if port is None:
            raise GuardianCompileError(
                "probe_port_unavailable",
                f"Guardian probe {application.id} requires an explicit port or allocated role {role}",
                details={"check_id": application.id, "role": role},
            )
        return port


def compile_guardian_config(server: Server, blueprint: Blueprint) -> dict[str, Any]:
    ports = _port_map(server)
    health = blueprint.health
    checks: list[dict[str, Any]] = []
    if health and health.process:
        process = health.process
        if process.required:
            checks.append(
                {
                    "check_id": process.id,
                    "type": "process",
                    "interval_seconds": _duration_seconds(process.interval, minimum=1),
                    "timeout_seconds": 1,
                    "failure_threshold": process.failure_threshold,
                    "success_threshold": process.success_threshold,
                    "required_for_startup": process.required_for_startup,
                    "required_for_verification": process.required_for_verification,
                }
            )
    if health and health.port:
        port_check = health.port
        probe_type = "tcp" if port_check.protocol == "tcp" else "udp_port_mapping"
        target_port = _resolved_port(port_check.port, ports)
        compiled: dict[str, Any] = {
            "check_id": port_check.id,
            "type": probe_type,
            "interval_seconds": _duration_seconds(port_check.interval, minimum=1),
            "timeout_seconds": _duration_seconds(port_check.timeout),
            "failure_threshold": port_check.failure_threshold,
            "success_threshold": port_check.success_threshold,
            "required_for_startup": port_check.required_for_startup,
            "required_for_verification": port_check.required_for_verification,
            "target_port": target_port,
        }
        if probe_type == "tcp":
            compiled["target_host"] = _target_host(server)
        checks.append(compiled)
    if health and health.application:
        application = health.application
        compiled = {
            "check_id": application.id,
            "type": application.type,
            "interval_seconds": _duration_seconds(application.interval, minimum=1),
            "timeout_seconds": _duration_seconds(application.timeout),
            "failure_threshold": application.failure_threshold,
            "success_threshold": application.success_threshold,
            "required_for_startup": application.required_for_startup,
            "required_for_verification": application.required_for_verification,
            "target_host": _target_host(server),
            "target_port": _application_port(application, ports),
        }
        if application.type == "http-ping":
            compiled.update(
                {
                    "path": application.path,
                    "expected_statuses": application.expected_statuses,
                    "follow_redirects": application.follow_redirects,
                    "max_response_bytes": application.max_response_bytes,
                }
            )
        checks.append(compiled)

    # A Guardian configuration with no health signal cannot verify recovery.
    if not checks:
        checks.append(
            {
                "check_id": "process",
                "type": "process",
                "interval_seconds": 15,
                "timeout_seconds": 1,
                "failure_threshold": 1,
                "success_threshold": 1,
                "required_for_startup": True,
                "required_for_verification": True,
            }
        )

    startup = health.startup if health and health.startup else None
    recovery = blueprint.recovery
    logs = blueprint.logs
    diagnostics = blueprint.diagnostics
    backups = blueprint.backups
    verification = recovery.verification if recovery else None
    return {
        "health_checks": checks,
        "startup": {
            "grace_period_seconds": startup.grace_period_seconds if startup else 30,
            "timeout_seconds": startup.timeout_seconds if startup else 300,
            "success_patterns": startup.success_patterns if startup else [],
            "failure_patterns": startup.failure_patterns if startup else [],
        },
        "verification": {
            "minimum_healthy_duration_seconds": (
                verification.minimum_healthy_duration_seconds if verification else 30
            ),
            "required_consecutive_successes": (
                verification.required_consecutive_successes if verification else 3
            ),
            "verification_timeout_seconds": (
                verification.verification_timeout_seconds if verification else 180
            ),
        },
        "logs": {
            "sources": logs.sources if logs else [],
            "redact": logs.redact if logs else [],
            "max_tail_bytes": logs.max_tail_bytes if logs else 65_536,
        },
        "diagnostics": {"parsers": diagnostics.parsers if diagnostics else []},
        "recovery": {
            "policies": [policy.model_dump() for policy in recovery.policies] if recovery else [],
            "safe_lock_files": [entry.model_dump() for entry in recovery.safe_lock_files] if recovery else [],
            "max_attempts": recovery.max_attempts if recovery else 3,
            "attempt_window_seconds": recovery.attempt_window_seconds if recovery else 1800,
            "cooldown_seconds": recovery.cooldown_seconds if recovery else 300,
        },
        "backups": {
            "before_risky_action": backups.before_risky_action if backups else True,
            "protected_paths": backups.protected_paths if backups else [],
        },
    }


def guardian_config_hash(server: Server, blueprint: Blueprint) -> str:
    encoded = json.dumps(
        compile_guardian_config(server, blueprint),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _load_optional_json(raw: str | None, field: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GuardianCompileError("invalid_persisted_state", f"Invalid persisted {field}") from exc
    if not isinstance(value, dict):
        raise GuardianCompileError("invalid_persisted_state", f"Invalid persisted {field}")
    return value


def compile_desired_state(server: Server, blueprint: Blueprint) -> dict[str, Any]:
    desired_power_state = str(getattr(server, "desired_power_state", "stopped"))
    if desired_power_state not in {"running", "stopped"}:
        raise GuardianCompileError("invalid_desired_power_state", "Invalid desired power state")
    generation = int(getattr(server, "desired_state_generation", 1) or 0)
    if generation < 1:
        raise GuardianCompileError("invalid_generation", "Invalid desired state generation")
    payload: dict[str, Any] = {
        "schema_version": GUARDIAN_SCHEMA_VERSION,
        "server_id": int(server.id),
        "generation": generation,
        "desired_power_state": desired_power_state,
        "recovery_suspension": _load_optional_json(
            getattr(server, "guardian_recovery_suspension", None),
            "Guardian recovery suspension",
        ),
        "quarantine_control": _load_optional_json(
            getattr(server, "guardian_quarantine_control", None),
            "Guardian quarantine control",
        ),
        "guardian": compile_guardian_config(server, blueprint),
    }
    if _ANY_TOKEN_RE.search(json.dumps(payload, ensure_ascii=False)):
        raise GuardianCompileError(
            "unresolved_placeholder",
            "Guardian payload contains an unresolved Blueprint placeholder",
        )
    payload["payload_hash"] = canonical_payload_hash(payload)
    return payload


def required_capabilities(payload: dict[str, Any]) -> GuardianRequirements:
    guardian = payload.get("guardian") or {}
    return GuardianRequirements(
        schema_version=int(payload.get("schema_version") or 0),
        probe_types=frozenset(
            str(check.get("type")) for check in guardian.get("health_checks", [])
        ),
        diagnostic_parsers=frozenset(
            str(value) for value in (guardian.get("diagnostics") or {}).get("parsers", [])
        ),
        recovery_actions=frozenset(
            str(policy.get("action")) for policy in (guardian.get("recovery") or {}).get("policies", [])
        ),
    )


def validate_agent_capabilities(payload: dict[str, Any], capabilities: dict[str, Any]) -> None:
    required = required_capabilities(payload)
    missing: dict[str, list[Any]] = {}
    schemas = {int(value) for value in capabilities.get("guardian_schema_versions", [])}
    if required.schema_version not in schemas:
        missing["guardian_schema_versions"] = [required.schema_version]
    probe_missing = sorted(required.probe_types - set(capabilities.get("probe_types", [])))
    parser_missing = sorted(
        required.diagnostic_parsers - set(capabilities.get("diagnostic_parsers", []))
    )
    action_missing = sorted(
        required.recovery_actions - set(capabilities.get("recovery_actions", []))
    )
    if probe_missing:
        missing["probe_types"] = probe_missing
    if parser_missing:
        missing["diagnostic_parsers"] = parser_missing
    if action_missing:
        missing["recovery_actions"] = action_missing
    if missing:
        raise GuardianCompileError(
            "guardian_capability_mismatch",
            "Agent does not support the required Guardian capabilities",
            details={"unsupported": missing},
        )

