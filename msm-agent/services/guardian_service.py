"""Durable Guardian observation, recovery and verification state machine."""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config import settings
from services import docker_service, file_service
from services.guardian_action_registry import (
    RecoveryContext,
    execute_action,
)
from services.agent_operation_coordinator import is_operation_active, operation
from services.guardian_contract import (
    DIAGNOSTIC_PARSERS,
    GUARDIAN_SCHEMA_VERSION,
    DesiredState,
    GuardianContractError,
    ProbeConfig,
    canonical_payload_hash,
    validate_desired_state,
)
from services.guardian_incident_store import GuardianIncidentStore
from services.guardian_probes import ProbeResult, execute_probe
from services.guardian_state_store import (
    CorruptedGuardianStateError,
    GuardianStateError,
    GuardianStateStore,
)


logger = logging.getLogger("msm-agent.guardian")

OBSERVED_STATES = frozenset(
    {
        "unknown",
        "stopped",
        "starting",
        "healthy",
        "degraded",
        "unhealthy",
        "recovering",
        "verifying",
        "quarantined",
    }
)

_STATE_STORE: GuardianStateStore | None = None
_running = False


class DesiredStateRejected(GuardianStateError):
    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_state_store() -> GuardianStateStore:
    global _STATE_STORE
    configured = os.path.abspath(settings.guardian_state_dir)
    if _STATE_STORE is None or os.fspath(_STATE_STORE.root) != configured:
        _STATE_STORE = GuardianStateStore(configured)
    return _STATE_STORE


def reset_guardian_service_for_tests() -> None:
    global _STATE_STORE, _running
    _STATE_STORE = None
    _running = False


def _default_runtime(server_id: int, desired: DesiredState) -> dict[str, Any]:
    initial = "starting" if desired.desired_power_state == "running" else "stopped"
    now = _iso()
    return {
        "schema_version": 1,
        "server_id": server_id,
        "accepted_generation": desired.generation,
        "state": initial,
        "state_entered_at": now,
        "startup_started_at": now if initial == "starting" else None,
        "verification_started_at": None,
        "verification_healthy_since": None,
        "verification_successes": 0,
        "active_incident_uuid": None,
        "active_incident_type": None,
        "recovery_stage": 0,
        "attempts": [],
        "last_recovery_at": None,
        "probe_states": {},
        "last_probe_at": None,
        "quarantine": None,
        "last_quarantine_clear_operation_id": None,
        "transition_history": [
            {"from": "unknown", "to": initial, "at": now, "reason": "desired_state_accepted"}
        ],
    }


def _load_runtime(server_id: int, desired: DesiredState) -> dict[str, Any]:
    store = get_state_store()
    raw = store.read_json(server_id, "runtime-state.json")
    if raw is None:
        return _default_runtime(server_id, desired)
    if raw.get("server_id") != server_id:
        raise GuardianStateError("runtime state server ID mismatch")
    if raw.get("state") not in OBSERVED_STATES:
        raise GuardianStateError("runtime state contains an unknown state")
    raw.setdefault("probe_states", {})
    raw.setdefault("attempts", [])
    raw.setdefault("transition_history", [])
    raw.setdefault("recovery_stage", 0)
    raw.setdefault("verification_successes", 0)
    if not raw.get("state_entered_at"):
        raw["state_entered_at"] = _iso()
    return raw


def _save_runtime(server_id: int, runtime: dict[str, Any]) -> None:
    runtime["schema_version"] = 1
    runtime["server_id"] = server_id
    get_state_store().write_json(server_id, "runtime-state.json", runtime)


def _transition(runtime: dict[str, Any], new_state: str, reason: str) -> None:
    if new_state not in OBSERVED_STATES:
        raise GuardianStateError(f"unknown Guardian state: {new_state}")
    previous = str(runtime.get("state") or "unknown")
    if previous == new_state:
        return
    now = _iso()
    runtime["state"] = new_state
    runtime["state_entered_at"] = now
    history = runtime.setdefault("transition_history", [])
    history.append({"from": previous, "to": new_state, "at": now, "reason": reason[:64]})
    # The complete incident attempt history is durable in SQLite.  Runtime
    # keeps only a bounded operational tail to prevent an unbounded JSON file.
    runtime["transition_history"] = history[-200:]


def _write_observed(
    desired: DesiredState,
    runtime: dict[str, Any],
    container_state: dict[str, Any] | None,
) -> dict[str, Any]:
    suspension = desired.recovery_suspension
    local_suspension = runtime.get("local_recovery_suspension")
    observed = {
        "schema_version": 1,
        "server_id": desired.server_id,
        "accepted_generation": desired.generation,
        "payload_hash": desired.payload_hash,
        "guardian_observed_state": runtime.get("state", "unknown"),
        "observed_runtime_state": runtime.get("state", "unknown"),
        "container_state": (container_state or {}).get("status", "missing"),
        "active_incident_uuid": runtime.get("active_incident_uuid"),
        "last_probe_at": runtime.get("last_probe_at"),
        "last_transition_at": runtime.get("state_entered_at") or _iso(),
        "quarantine": runtime.get("quarantine"),
        "recovery_suspension": (
            suspension.model_dump(mode="json") if suspension is not None else local_suspension
        ),
        "supported_schema_version": GUARDIAN_SCHEMA_VERSION,
    }
    get_state_store().write_json(desired.server_id, "observed-state.json", observed)
    return observed


def _load_desired(server_id: int) -> DesiredState | None:
    raw = get_state_store().read_json(server_id, "desired-state.json")
    if raw is None:
        return None
    try:
        return validate_desired_state(raw, expected_server_id=server_id)
    except GuardianContractError as exc:
        raise GuardianStateError(f"persisted desired state is invalid: {exc.code}") from exc


def _record_state_corruption(server_id: int, error: CorruptedGuardianStateError) -> None:
    incident_store = GuardianIncidentStore(get_state_store(), server_id)
    incident_store.create(
        incident_type="guardian_state_corruption",
        status="open",
        fingerprint=f"guardian_state_corruption:{error.path.name}",
        payload={
            "schema_version": 1,
            "message": "Guardian state corruption requires administrator inspection",
            "file": error.path.name,
            "retained_file": error.retained_path.name,
            "attempts": [],
        },
    )


def accept_desired_state(
    server_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        desired = validate_desired_state(payload, expected_server_id=server_id)
    except GuardianContractError as exc:
        raise DesiredStateRejected(exc.code, 422, exc.message) from exc

    store = get_state_store()
    try:
        current_raw = store.read_json(server_id, "desired-state.json")
    except CorruptedGuardianStateError as exc:
        _record_state_corruption(server_id, exc)
        raise DesiredStateRejected(
            "stored_state_corrupted",
            409,
            "stored desired state is corrupted and requires administrator inspection",
        ) from exc

    if current_raw is not None:
        current_generation = int(current_raw.get("generation") or 0)
        current_hash = current_raw.get("payload_hash")
        if desired.generation < current_generation:
            raise DesiredStateRejected("stale_generation", 409, "desired state generation is stale")
        if desired.generation == current_generation:
            if desired.payload_hash == current_hash:
                return {
                    "ok": True,
                    "result": "unchanged",
                    "generation": desired.generation,
                    "payload_hash": desired.payload_hash,
                }
            raise DesiredStateRejected(
                "generation_conflict",
                409,
                "equal desired state generation has a different payload hash",
            )

    runtime = _load_runtime(server_id, desired)
    previous_power = current_raw.get("desired_power_state") if current_raw else None
    runtime["accepted_generation"] = desired.generation

    if desired.quarantine_control is not None:
        operation_id = desired.quarantine_control.operation_id
        if runtime.get("last_quarantine_clear_operation_id") != operation_id:
            runtime["quarantine"] = None
            runtime["attempts"] = []
            runtime["recovery_stage"] = 0
            runtime["active_incident_uuid"] = None
            runtime["active_incident_type"] = None
            runtime["last_quarantine_clear_operation_id"] = operation_id
            target = "starting" if desired.desired_power_state == "running" else "stopped"
            _transition(runtime, target, "quarantine_cleared")
            if target == "starting":
                runtime["startup_started_at"] = _iso()

    if runtime.get("quarantine") is None and previous_power != desired.desired_power_state:
        if desired.desired_power_state == "running":
            runtime["startup_started_at"] = _iso()
            _transition(runtime, "starting", "desired_power_running")
        else:
            _transition(runtime, "stopped", "desired_power_stopped")

    # Persist the validated contract first.  If the following runtime write is
    # interrupted, the next loop deterministically repairs accepted_generation.
    store.write_json(server_id, "desired-state.json", payload)
    _save_runtime(server_id, runtime)
    return {
        "ok": True,
        "result": "updated",
        "generation": desired.generation,
        "payload_hash": desired.payload_hash,
    }


def observed_state(server_id: int) -> dict[str, Any]:
    desired = _load_desired(server_id)
    if desired is None:
        raise FileNotFoundError("Guardian desired state not found")
    runtime = _load_runtime(server_id, desired)
    container = docker_service.inspect_container_state(f"{settings.container_name_prefix}{server_id}")
    return _write_observed(desired, runtime, container)


def list_incidents(server_id: int) -> list[dict[str, Any]]:
    return GuardianIncidentStore(get_state_store(), server_id).list_unacknowledged()


def acknowledge_incidents(server_id: int, incident_uuids: list[str]) -> list[str]:
    return GuardianIncidentStore(get_state_store(), server_id).acknowledge(incident_uuids)


_BUILTIN_REDACTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "discord_token": re.compile(r"(?i)(?:mfa\.[\w-]{20,}|[\w-]{20,}\.[\w-]{6,}\.[\w-]{20,})"),
    "api_key": re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+"),
    "authorization_header": re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)\S+"),
    "database_url": re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql)://[^\s]+"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
}


def _redact(text: str, redactors: list[str]) -> str:
    result = text
    for redactor in redactors:
        if redactor.startswith("regex:"):
            pattern = re.compile(redactor[6:])
        else:
            pattern = _BUILTIN_REDACTION_PATTERNS[redactor]
        result = pattern.sub("[REDACTED]", result)
    return result


def _tail_file(path: Path, max_bytes: int) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    with path.open("rb") as stream:
        size = path.stat().st_size
        stream.seek(max(0, size - max_bytes))
        return stream.read(max_bytes).decode("utf-8", errors="replace")


def _collect_logs(server_id: int, container_name: str, desired: DesiredState) -> str:
    logs_config = desired.guardian.logs
    pieces: list[str] = []
    remaining = logs_config.max_tail_bytes
    root = file_service.server_root(server_id)
    for source in logs_config.sources:
        if remaining <= 0:
            break
        if source == "stdout":
            try:
                value = docker_service.container_logs(container_name, tail=200)
            except Exception:
                value = ""
            encoded = value.encode("utf-8", errors="replace")[-remaining:]
            text = encoded.decode("utf-8", errors="replace")
            pieces.append(text)
            remaining -= len(encoded)
            continue
        matches = glob.glob(str(root / source), recursive=False)[:8]
        for raw in matches:
            candidate = Path(raw)
            if candidate.is_symlink():
                continue
            try:
                candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
            except (FileNotFoundError, ValueError):
                continue
            text = _tail_file(candidate, remaining)
            pieces.append(text)
            remaining -= len(text.encode("utf-8", errors="replace"))
            if remaining <= 0:
                break
    return _redact("\n".join(pieces), logs_config.redact)


def _diagnose(
    desired: DesiredState,
    container: dict[str, Any] | None,
    logs: str,
    fallback: str,
) -> dict[str, str]:
    lower = logs.lower()
    parsers = set(desired.guardian.diagnostics.parsers)
    if "linux-oom" in parsers and (
        bool((container or {}).get("oom_killed"))
        or "oom-killer" in lower
        or "out of memory" in lower
        or "java.lang.outofmemoryerror" in lower
    ):
        return {"type": "linux-oom", "confidence": "high", "evidence": "verified OOM signature"}
    if "port-conflict" in parsers and any(
        pattern in lower for pattern in ("address already in use", "bindexception", "could not bind")
    ):
        return {"type": "port-conflict", "confidence": "high", "evidence": "bind failure signature"}
    if "java-stacktrace" in parsers and re.search(r"(?m)^\s*at\s+[\w.$]+\([^\n]+\.java:\d+\)", logs):
        return {"type": "java-stacktrace", "confidence": "medium", "evidence": "Java stack trace"}
    if "nodejs-stacktrace" in parsers and " at " in lower and ("node:" in lower or ".js:" in lower):
        return {"type": "nodejs-stacktrace", "confidence": "medium", "evidence": "Node.js stack trace"}
    if "missing-runtime" in parsers and any(
        pattern in lower for pattern in ("unable to access jarfile", "noclassdeffounderror", "command not found")
    ):
        return {"type": "missing-runtime", "confidence": "high", "evidence": "runtime missing signature"}
    if "corrupted-config" in parsers and any(
        pattern in lower for pattern in ("configuration parse error", "invalid configuration", "unexpected token")
    ):
        return {"type": "corrupted-config", "confidence": "medium", "evidence": "configuration error signature"}
    return {"type": fallback, "confidence": "high", "evidence": "health probe result"}


async def _run_checks(
    desired: DesiredState,
    runtime: dict[str, Any],
    container_name: str,
    *,
    force: bool = False,
    startup_only: bool = False,
    verification_only: bool = False,
) -> list[tuple[ProbeConfig, ProbeResult, dict[str, Any]]]:
    now = _utcnow()
    results: list[tuple[ProbeConfig, ProbeResult, dict[str, Any]]] = []
    probe_states = runtime.setdefault("probe_states", {})
    for check in desired.guardian.health_checks:
        if startup_only and not check.required_for_startup:
            continue
        if verification_only and not check.required_for_verification:
            continue
        state = probe_states.setdefault(
            check.check_id,
            {
                "check_id": check.check_id,
                "last_run_at": None,
                "next_run_at": None,
                "consecutive_failures": 0,
                "consecutive_successes": 0,
                "last_result": "unknown",
            },
        )
        next_run = _parse_time(state.get("next_run_at"))
        if not force and next_run is not None and next_run > now:
            continue
        result = await execute_probe(check, container_name)
        if result.healthy:
            state["consecutive_successes"] = int(state.get("consecutive_successes") or 0) + 1
            state["consecutive_failures"] = 0
            state["last_result"] = "healthy"
        else:
            state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
            state["consecutive_successes"] = 0
            state["last_result"] = "unhealthy"
        state["last_run_at"] = _iso(now)
        state["next_run_at"] = _iso(now + timedelta(seconds=check.interval_seconds))
        state["last_code"] = result.code
        runtime["last_probe_at"] = state["last_run_at"]
        results.append((check, result, state))
    return results


def _required_check_status(
    desired: DesiredState,
    runtime: dict[str, Any],
    *,
    verification: bool = False,
    startup: bool = False,
) -> tuple[bool, bool, str]:
    required: list[ProbeConfig] = []
    for check in desired.guardian.health_checks:
        if verification and check.required_for_verification:
            required.append(check)
        elif startup and check.required_for_startup:
            required.append(check)
        elif not verification and not startup:
            required.append(check)
    if not required:
        return True, False, "no_required_checks"
    any_degraded = False
    for check in required:
        state = runtime.get("probe_states", {}).get(check.check_id, {})
        if int(state.get("consecutive_failures") or 0) >= check.failure_threshold:
            return False, False, str(state.get("last_code") or "probe_failed")
        if state.get("last_result") != "healthy" or int(state.get("consecutive_successes") or 0) < check.success_threshold:
            any_degraded = True
    return not any_degraded, any_degraded, "probe_threshold"


def _suspension_active(desired: DesiredState, runtime: dict[str, Any] | None = None) -> bool:
    lease = desired.recovery_suspension
    if lease is not None and lease.suspend_until > _utcnow():
        return True
    local = (runtime or {}).get("local_recovery_suspension")
    return bool(local and (_parse_time(local.get("suspend_until")) or datetime.min.replace(tzinfo=timezone.utc)) > _utcnow())


@contextmanager
def planned_operation(
    server_id: int,
    reason: str,
    *,
    lease_seconds: int = 3600,
):
    """Serialize a planned mutation and protect recovery with an expiring lease."""
    if lease_seconds < 1 or lease_seconds > 4 * 60 * 60:
        raise ValueError("planned operation lease is outside the safe bound")
    operation_id = str(uuid.uuid4())
    with operation(server_id):
        desired = _load_desired(server_id)
        if desired is None:
            yield operation_id
            return
        runtime = _load_runtime(server_id, desired)
        runtime["local_recovery_suspension"] = {
            "operation_id": operation_id,
            "reason": reason[:64],
            "suspend_until": _iso(_utcnow() + timedelta(seconds=lease_seconds)),
        }
        _save_runtime(server_id, runtime)
        try:
            yield operation_id
        finally:
            latest = _load_runtime(server_id, desired)
            current = latest.get("local_recovery_suspension") or {}
            if current.get("operation_id") == operation_id:
                latest["local_recovery_suspension"] = None
                _save_runtime(server_id, latest)


def _prune_attempts(runtime: dict[str, Any], window_seconds: int) -> list[dict[str, Any]]:
    cutoff = _utcnow() - timedelta(seconds=window_seconds)
    attempts = [
        item
        for item in runtime.get("attempts", [])
        if (_parse_time(item.get("at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    ]
    runtime["attempts"] = attempts
    return attempts


def _incident_payload(
    runtime: dict[str, Any],
    incident_type: str,
    status: str,
    diagnostic: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "message": "Guardian detected an unhealthy server state",
        "type": incident_type,
        "status": status,
        "diagnostic": diagnostic,
        "recovery_stage": runtime.get("recovery_stage", 0),
        "attempts": runtime.get("attempts", []),
        "transitions": runtime.get("transition_history", [])[-20:],
    }


def _upsert_active_incident(
    desired: DesiredState,
    runtime: dict[str, Any],
    incident_type: str,
    status: str,
    diagnostic: dict[str, str],
) -> str:
    incident_uuid = runtime.get("active_incident_uuid")
    if not incident_uuid:
        incident_uuid = str(uuid.uuid4())
        runtime["active_incident_uuid"] = incident_uuid
        runtime["active_incident_type"] = incident_type
    store = GuardianIncidentStore(get_state_store(), desired.server_id)
    store.upsert(
        incident_uuid=incident_uuid,
        incident_type=runtime.get("active_incident_type") or incident_type,
        status=status,
        fingerprint=f"guardian:{desired.server_id}:{runtime.get('active_incident_type') or incident_type}",
        payload=_incident_payload(runtime, incident_type, status, diagnostic),
    )
    return incident_uuid


def _quarantine(
    desired: DesiredState,
    runtime: dict[str, Any],
    diagnostic: dict[str, str],
    reason: str,
) -> None:
    runtime["quarantine"] = {"at": _iso(), "reason": reason[:64]}
    _transition(runtime, "quarantined", reason)
    _upsert_active_incident(desired, runtime, diagnostic["type"], "quarantined", diagnostic)


async def _attempt_recovery(
    desired: DesiredState,
    runtime: dict[str, Any],
    container_name: str,
    diagnostic: dict[str, str],
) -> None:
    recovery = desired.guardian.recovery
    attempts = _prune_attempts(runtime, recovery.attempt_window_seconds)
    if len(attempts) >= recovery.max_attempts:
        _quarantine(desired, runtime, diagnostic, "attempt_limit_exceeded")
        return
    last_recovery = _parse_time(runtime.get("last_recovery_at"))
    if last_recovery is not None and (_utcnow() - last_recovery).total_seconds() < recovery.cooldown_seconds:
        return
    matching = [policy for policy in recovery.policies if policy.match == diagnostic["type"]]
    if not matching:
        _upsert_active_incident(desired, runtime, diagnostic["type"], "open", diagnostic)
        return
    stage = int(runtime.get("recovery_stage") or 0)
    if stage >= len(matching):
        _quarantine(desired, runtime, diagnostic, "recovery_stages_exhausted")
        return
    policy = matching[stage]
    attempt = {
        "attempt": len(attempts) + 1,
        "stage": stage,
        "action": policy.action,
        "at": _iso(),
        "result": "running",
    }
    attempts.append(attempt)
    runtime["attempts"] = attempts
    runtime["last_recovery_at"] = attempt["at"]
    _transition(runtime, "recovering", "recovery_action_started")
    _upsert_active_incident(desired, runtime, diagnostic["type"], "recovering", diagnostic)
    _save_runtime(desired.server_id, runtime)

    try:
        result = await execute_action(
            policy.action,
            RecoveryContext(desired.server_id, container_name, desired.guardian),
        )
        attempt["result"] = "executed" if result.ok else "failed"
        attempt["details"] = result.details
    except (Exception, asyncio.TimeoutError):
        logger.warning(
            "Guardian recovery action failed for server_id=%s action=%s",
            desired.server_id,
            policy.action,
        )
        attempt["result"] = "failed"
    if policy.action == "quarantine" or attempt["result"] == "failed":
        runtime["recovery_stage"] = stage + 1
        if policy.action == "quarantine":
            _quarantine(desired, runtime, diagnostic, "quarantine_action")
        else:
            _transition(runtime, "unhealthy", "recovery_action_failed")
            _upsert_active_incident(desired, runtime, diagnostic["type"], "open", diagnostic)
        return
    runtime["verification_started_at"] = _iso()
    runtime["verification_healthy_since"] = None
    runtime["verification_successes"] = 0
    _transition(runtime, "verifying", "recovery_action_executed")
    _upsert_active_incident(desired, runtime, diagnostic["type"], "verifying", diagnostic)


async def _handle_verification(
    desired: DesiredState,
    runtime: dict[str, Any],
    container_name: str,
    logs: str,
) -> None:
    await _run_checks(desired, runtime, container_name, force=True, verification_only=True)
    healthy, degraded, failure = _required_check_status(desired, runtime, verification=True)
    failure_pattern = next(
        (pattern for pattern in desired.guardian.startup.failure_patterns if re.search(pattern, logs)),
        None,
    )
    now = _utcnow()
    if healthy and failure_pattern is None:
        runtime["verification_successes"] = int(runtime.get("verification_successes") or 0) + 1
        if runtime.get("verification_healthy_since") is None:
            runtime["verification_healthy_since"] = _iso(now)
        healthy_since = _parse_time(runtime.get("verification_healthy_since")) or now
        config = desired.guardian.verification
        if (
            runtime["verification_successes"] >= config.required_consecutive_successes
            and (now - healthy_since).total_seconds() >= config.minimum_healthy_duration_seconds
        ):
            diagnostic = {
                "type": runtime.get("active_incident_type") or "recovery",
                "confidence": "high",
                "evidence": "required health stabilization completed",
            }
            _transition(runtime, "healthy", "verification_succeeded")
            _upsert_active_incident(desired, runtime, diagnostic["type"], "resolved", diagnostic)
            runtime["active_incident_uuid"] = None
            runtime["active_incident_type"] = None
            runtime["attempts"] = []
            runtime["recovery_stage"] = 0
            runtime["verification_started_at"] = None
            return
    else:
        runtime["verification_successes"] = 0
        runtime["verification_healthy_since"] = None

    started = _parse_time(runtime.get("verification_started_at")) or now
    if (now - started).total_seconds() >= desired.guardian.verification.verification_timeout_seconds:
        runtime["recovery_stage"] = int(runtime.get("recovery_stage") or 0) + 1
        diagnostic = {
            "type": runtime.get("active_incident_type") or "verification_failed",
            "confidence": "high",
            "evidence": "verification timeout without stable health",
        }
        _transition(runtime, "unhealthy", "verification_failed")
        _upsert_active_incident(desired, runtime, diagnostic["type"], "open", diagnostic)


async def reconcile_server(server_id: int) -> None:
    try:
        desired = _load_desired(server_id)
    except CorruptedGuardianStateError as exc:
        _record_state_corruption(server_id, exc)
        return
    if desired is None:
        return
    try:
        runtime = _load_runtime(server_id, desired)
    except CorruptedGuardianStateError as exc:
        _record_state_corruption(server_id, exc)
        return
    runtime["accepted_generation"] = desired.generation
    container_name = f"{settings.container_name_prefix}{server_id}"
    try:
        container = await asyncio.to_thread(docker_service.inspect_container_state, container_name)
    except Exception:
        logger.warning("Guardian could not inspect server_id=%s", server_id)
        container = None

    # A planned route owns the same operation lock and has already persisted a
    # bounded suspension lease.  Observation remains available, but Guardian
    # must not race the mutation or rewrite its runtime lease.
    if is_operation_active(server_id):
        _write_observed(desired, runtime, container)
        return

    if desired.desired_power_state == "stopped":
        _transition(runtime, "stopped", "desired_power_stopped")
        _save_runtime(server_id, runtime)
        _write_observed(desired, runtime, container)
        return

    if runtime.get("quarantine") is not None:
        _transition(runtime, "quarantined", "quarantine_persisted")
        await _run_checks(desired, runtime, container_name)
        _save_runtime(server_id, runtime)
        _write_observed(desired, runtime, container)
        return

    if runtime.get("state") in {"stopped", "unknown"}:
        runtime["startup_started_at"] = _iso()
        _transition(runtime, "starting", "desired_power_running")

    logs = await asyncio.to_thread(_collect_logs, server_id, container_name, desired)
    now = _utcnow()

    if runtime.get("state") == "starting":
        started = _parse_time(runtime.get("startup_started_at")) or now
        startup = desired.guardian.startup
        failure_pattern = next((p for p in startup.failure_patterns if re.search(p, logs)), None)
        if failure_pattern is not None:
            diagnostic = {"type": "startup-pattern", "confidence": "high", "evidence": "startup failure pattern"}
            _transition(runtime, "unhealthy", "startup_failure_pattern")
            _upsert_active_incident(desired, runtime, diagnostic["type"], "open", diagnostic)
        elif (now - started).total_seconds() < startup.grace_period_seconds:
            pass
        else:
            await _run_checks(desired, runtime, container_name, force=True, startup_only=True)
            probes_healthy, _, _ = _required_check_status(desired, runtime, startup=True)
            pattern_ready = not startup.success_patterns or any(
                re.search(pattern, logs) for pattern in startup.success_patterns
            )
            process_running = bool(container and container.get("running"))
            if process_running and probes_healthy and pattern_ready:
                _transition(runtime, "healthy", "startup_ready")
            elif (now - started).total_seconds() >= startup.timeout_seconds:
                diagnostic = _diagnose(desired, container, logs, "startup_timeout")
                _transition(runtime, "unhealthy", "startup_timeout")
                _upsert_active_incident(desired, runtime, diagnostic["type"], "open", diagnostic)

    elif runtime.get("state") == "verifying":
        await _handle_verification(desired, runtime, container_name, logs)

    elif runtime.get("state") in {"healthy", "degraded", "unhealthy"}:
        await _run_checks(desired, runtime, container_name)
        healthy, degraded, failure = _required_check_status(desired, runtime)
        if healthy:
            _transition(runtime, "healthy", "health_checks_passed")
        elif degraded:
            _transition(runtime, "degraded", "health_threshold_pending")
        else:
            diagnostic = _diagnose(desired, container, logs, failure)
            _transition(runtime, "unhealthy", "health_threshold_failed")
            _upsert_active_incident(desired, runtime, diagnostic["type"], "open", diagnostic)

    if runtime.get("state") == "unhealthy" and not _suspension_active(desired, runtime):
        diagnostic_type = runtime.get("active_incident_type") or "probe_failed"
        diagnostic = _diagnose(desired, container, logs, diagnostic_type)
        await _attempt_recovery(desired, runtime, container_name, diagnostic)

    _save_runtime(server_id, runtime)
    _write_observed(desired, runtime, container)


async def reconcile_all_servers() -> None:
    root = get_state_store().ensure_root()
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_dir() or not path.name.isdigit() or path.name.startswith("0"):
            continue
        try:
            await reconcile_server(int(path.name))
        except Exception:
            logger.exception("Guardian reconciliation failed for server_id=%s", path.name)


async def start_guardian_loop() -> None:
    global _running
    if _running:
        return
    _running = True
    logger.info("Guardian Verified Recovery loop starting")
    interval = max(0.25, float(getattr(settings, "guardian_loop_interval_seconds", 5.0)))
    while _running:
        try:
            await reconcile_all_servers()
        except Exception:
            logger.exception("Guardian reconciliation loop failed")
        await asyncio.sleep(interval)


async def stop_guardian_loop() -> None:
    global _running
    _running = False
    logger.info("Guardian Verified Recovery loop stopped")
