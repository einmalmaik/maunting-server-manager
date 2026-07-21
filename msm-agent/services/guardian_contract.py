"""Strict, versioned runtime contract accepted by the MSM Agent."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure 'services' package path is extended to include the agent's services directory
# to prevent collision with the backend's services package during integration tests.
agent_services_dir = str(Path(__file__).resolve().parent)
if "services" in sys.modules:
    services_mod = sys.modules["services"]
    if hasattr(services_mod, "__path__"):
        if agent_services_dir not in services_mod.__path__:
            services_mod.__path__.append(agent_services_dir)
else:
    agent_dir = str(Path(__file__).resolve().parent.parent)
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)

import hashlib
import ipaddress
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


GUARDIAN_SCHEMA_VERSION = 1
class DynamicProbeTypes:
    def __contains__(self, item: Any) -> bool:
        from services.guardian_probes import discover_probes
        return item in discover_probes()

    def __sub__(self, other: Any) -> set[str]:
        from services.guardian_probes import discover_probes
        return set(discover_probes().keys()) - set(other)

    def __iter__(self):
        from services.guardian_probes import discover_probes
        return iter(discover_probes().keys())

PROBE_TYPES = DynamicProbeTypes()
DIAGNOSTIC_PARSERS = frozenset(
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
RECOVERY_ACTIONS = frozenset(
    {"restart", "graceful_restart", "clear_declared_lock_files", "quarantine"}
)
BUILTIN_REDACTORS = frozenset(
    {"discord_token", "api_key", "authorization_header", "database_url", "jwt"}
)
MAX_SUSPENSION_SECONDS = 4 * 60 * 60
_PLACEHOLDER_RE = re.compile(r"{{[^{}]+}}")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class GuardianContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_PLACEHOLDER_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    return False


def _safe_relative_path(value: str, *, allow_stdout: bool = False, allow_final_glob: bool = False) -> str:
    raw = str(value or "").strip()
    if allow_stdout and raw == "stdout":
        return raw
    if not raw or len(raw) > 512 or "\x00" in raw or "\\" in raw or raw.startswith("/"):
        raise ValueError("path must be a safe relative POSIX path")
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("path traversal is not allowed")
    parts = PurePosixPath(raw).parts
    glob_chars = set("*?[")
    for index, part in enumerate(parts):
        chars = glob_chars.intersection(part)
        if not chars:
            continue
        if not allow_final_glob or index != len(parts) - 1 or chars != {"*"} or part.count("*") > 1:
            raise ValueError("only one final filename wildcard is allowed")
    return raw


def validate_safe_regex(pattern: str) -> str:
    """Accept a deliberately small regex subset with bounded input length.

    Python's stdlib regex engine has no execution timeout.  Guardian therefore
    rejects constructs commonly used for catastrophic backtracking instead of
    pretending an async timeout could stop a CPU-bound match.
    """
    value = str(pattern)
    if not value or len(value) > 256 or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("regex is empty or too long")
    if re.search(r"\\[1-9]|\(\?[=!<]|\(\?P|\(\?>|\(\?\(", value):
        raise ValueError("regex uses an unsupported advanced construct")
    if re.search(r"(?:\*|\+|\{\d+(?:,\d*)?\})\s*(?:\*|\+|\{)", value):
        raise ValueError("regex contains nested or repeated quantifiers")
    if re.search(r"\([^)]*(?:\*|\+|\{\d+(?:,\d*)?\})[^)]*\)(?:\*|\+|\{)", value):
        raise ValueError("regex contains a quantified group with inner quantifiers")
    re.compile(value)
    return value


class RecoverySuspension(StrictModel):
    operation_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    suspend_until: datetime

    @field_validator("operation_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        import uuid

        if str(uuid.UUID(value)) != value.lower():
            raise ValueError("operation_id must be a canonical UUID")
        return value.lower()

    @field_validator("suspend_until")
    @classmethod
    def _bounded_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("suspend_until must be UTC")
        if value > datetime.now(timezone.utc) + timedelta(seconds=MAX_SUSPENSION_SECONDS + 300):
            raise ValueError("recovery suspension exceeds the maximum lease")
        return value


class QuarantineControl(StrictModel):
    clear: Literal[True]
    operation_id: str = Field(min_length=36, max_length=36)

    @field_validator("operation_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        import uuid

        if str(uuid.UUID(value)) != value.lower():
            raise ValueError("operation_id must be a canonical UUID")
        return value.lower()


class ProbeConfig(StrictModel):
    check_id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=64)
    interval_seconds: float = Field(default=30, ge=1, le=3600)
    timeout_seconds: float = Field(default=3, ge=0.1, le=30)
    failure_threshold: int = Field(default=3, ge=1, le=20)
    success_threshold: int = Field(default=1, ge=1, le=20)
    required_for_startup: bool = False
    required_for_verification: bool = True
    target_host: str | None = Field(default=None, max_length=64)
    target_port: int | None = Field(default=None, ge=1, le=65535)
    path: str | None = Field(default=None, max_length=512)
    expected_statuses: list[int] = Field(default_factory=lambda: [200], max_length=16)
    follow_redirects: bool = False
    max_response_bytes: int = Field(default=4096, ge=1, le=1_048_576)

    @field_validator("check_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError("check_id is invalid")
        return value

    @field_validator("target_host")
    @classmethod
    def _target_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("target_host must be a concrete IP address") from exc
        if address.is_unspecified or address.is_link_local or address.is_multicast or address.is_reserved:
            raise ValueError("target_host is not allowed")
        return str(address)

    @field_validator("expected_statuses")
    @classmethod
    def _statuses(cls, values: list[int]) -> list[int]:
        if not values or any(value < 100 or value > 599 for value in values):
            raise ValueError("expected_statuses must contain valid HTTP status codes")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def _type_fields(self) -> "ProbeConfig":
        if self.type not in PROBE_TYPES:
            raise ValueError(f"unsupported probe type: {self.type}")
        network_types = PROBE_TYPES - {"process", "udp_port_mapping"}
        if self.type in network_types and (not self.target_host or self.target_port is None):
            raise ValueError(f"{self.type} requires target_host and target_port")
        if self.type == "udp_port_mapping" and self.target_port is None:
            raise ValueError("udp_port_mapping requires target_port")
        if self.type == "http-ping":
            if not self.path or not self.path.startswith("/") or self.path.startswith("//"):
                raise ValueError("http-ping path must be relative to the trusted target")
            if "://" in self.path or "#" in self.path:
                raise ValueError("http-ping path must not contain a URL or fragment")
            if self.follow_redirects:
                raise ValueError("http redirects are not supported by the local Guardian")
        elif self.path is not None:
            raise ValueError("path is only supported for http-ping")
        return self


class StartupConfig(StrictModel):
    grace_period_seconds: int = Field(default=30, ge=0, le=600)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    success_patterns: list[str] = Field(default_factory=list, max_length=16)
    failure_patterns: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("success_patterns", "failure_patterns")
    @classmethod
    def _patterns(cls, values: list[str]) -> list[str]:
        return [validate_safe_regex(value) for value in values]

    @model_validator(mode="after")
    def _timeout_after_grace(self) -> "StartupConfig":
        if self.timeout_seconds <= self.grace_period_seconds:
            raise ValueError("startup timeout must exceed the grace period")
        return self


class VerificationConfig(StrictModel):
    minimum_healthy_duration_seconds: int = Field(default=30, ge=0, le=600)
    required_consecutive_successes: int = Field(default=3, ge=1, le=20)
    verification_timeout_seconds: int = Field(default=180, ge=5, le=3600)


class LogsConfig(StrictModel):
    sources: list[str] = Field(default_factory=list, max_length=16)
    redact: list[str] = Field(default_factory=list, max_length=32)
    max_tail_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)

    @field_validator("sources")
    @classmethod
    def _sources(cls, values: list[str]) -> list[str]:
        return [
            _safe_relative_path(value, allow_stdout=True, allow_final_glob=True)
            for value in values
        ]

    @field_validator("redact")
    @classmethod
    def _redactors(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = str(value).strip()
            if item in BUILTIN_REDACTORS:
                normalized.append(item)
            elif item.startswith("regex:"):
                normalized.append(f"regex:{validate_safe_regex(item[6:])}")
            else:
                raise ValueError(f"unsupported redactor: {item}")
        return list(dict.fromkeys(normalized))


class DiagnosticsConfig(StrictModel):
    parsers: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("parsers")
    @classmethod
    def _parsers(cls, values: list[str]) -> list[str]:
        unsupported = sorted(set(values) - DIAGNOSTIC_PARSERS)
        if unsupported:
            raise ValueError(f"unsupported diagnostic parsers: {', '.join(unsupported)}")
        return list(dict.fromkeys(values))


class SafeLockFile(StrictModel):
    path: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=256)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _safe_relative_path(value)


class RecoveryPolicy(StrictModel):
    match: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    action: Literal[
        "restart", "graceful_restart", "clear_declared_lock_files", "quarantine"
    ]


class RecoveryConfig(StrictModel):
    policies: list[RecoveryPolicy] = Field(default_factory=list, max_length=16)
    safe_lock_files: list[SafeLockFile] = Field(default_factory=list, max_length=32)
    max_attempts: int = Field(default=3, ge=1, le=10)
    attempt_window_seconds: int = Field(default=1800, ge=60, le=86_400)
    cooldown_seconds: int = Field(default=300, ge=1, le=3600)

    @model_validator(mode="after")
    def _unique_paths(self) -> "RecoveryConfig":
        paths = [item.path for item in self.safe_lock_files]
        if len(paths) != len(set(paths)):
            raise ValueError("safe lock file paths must be unique")
        return self


class BackupsConfig(StrictModel):
    before_risky_action: bool = True
    protected_paths: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("protected_paths")
    @classmethod
    def _paths(cls, values: list[str]) -> list[str]:
        return [_safe_relative_path(value.rstrip("/")) for value in values]


class GuardianConfig(StrictModel):
    health_checks: list[ProbeConfig] = Field(default_factory=list, max_length=32)
    startup: StartupConfig = Field(default_factory=StartupConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    logs: LogsConfig = Field(default_factory=LogsConfig)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    backups: BackupsConfig = Field(default_factory=BackupsConfig)

    @model_validator(mode="after")
    def _unique_checks(self) -> "GuardianConfig":
        ids = [check.check_id for check in self.health_checks]
        if len(ids) != len(set(ids)):
            raise ValueError("health check IDs must be unique")
        return self


class DesiredState(StrictModel):
    schema_version: Literal[1]
    server_id: int = Field(ge=1, le=9_223_372_036_854_775_807)
    generation: int = Field(ge=1, le=9_223_372_036_854_775_807)
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    desired_power_state: Literal["running", "stopped"]
    recovery_suspension: RecoverySuspension | None = None
    quarantine_control: QuarantineControl | None = None
    guardian: GuardianConfig


def validate_desired_state(payload: dict[str, Any], *, expected_server_id: int) -> DesiredState:
    if not isinstance(payload, dict):
        raise GuardianContractError("invalid_payload", "desired state must be an object")
    if payload.get("schema_version") != GUARDIAN_SCHEMA_VERSION:
        raise GuardianContractError("unsupported_schema_version", "unsupported Guardian schema version")
    if _contains_placeholder(payload):
        raise GuardianContractError("unresolved_placeholder", "desired state contains an unresolved placeholder")
    supplied_hash = payload.get("payload_hash")
    if not isinstance(supplied_hash, str) or supplied_hash != canonical_payload_hash(payload):
        raise GuardianContractError("invalid_payload_hash", "desired state payload hash is invalid")
    try:
        desired = DesiredState.model_validate(payload)
    except Exception as exc:
        message = str(exc)
        code = "invalid_desired_state"
        if "recovery action" in message or "action" in message:
            code = "unsupported_recovery_action"
        elif "diagnostic" in message or "parsers" in message:
            code = "unsupported_diagnostic_parser"
        elif "type" in message and "health_checks" in message:
            code = "unsupported_probe_type"
        raise GuardianContractError(code, "desired state validation failed") from exc
    if desired.server_id != expected_server_id:
        raise GuardianContractError("server_id_mismatch", "server ID does not match container name")
    return desired
