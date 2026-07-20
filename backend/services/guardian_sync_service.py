"""Panel-to-Agent Guardian reconciliation and commit-before-ACK delivery."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from games import get_plugin
from models import Server
from services.guardian_runtime_compiler import (
    GuardianCompileError,
    compile_desired_state as compile_state,
    validate_agent_capabilities,
)
from services.guardian_state_service import ensure_guardian_config_generation
from services.node_client import NodeClient
from services.guardian_incident_service import ingest_incidents_and_ack


logger = logging.getLogger(__name__)

_OBSERVED_STATES = frozenset(
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


class GuardianContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GuardianSyncMismatchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_ALLOWED_CONTAINER_STATES = frozenset(
    {
        "created",
        "restarting",
        "running",
        "removing",
        "paused",
        "exited",
        "dead",
        "missing",
        "unknown",
        "stopped",
        "installing",
        "updating",
        "error",
        "awaiting_files",
    }
)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not value:
        raise GuardianContractError("guardian_invalid_observed_timestamp", "Timestamp is empty")
    try:
        val_str = str(value)
        if val_str.endswith("Z"):
            val_str = val_str[:-1] + "+00:00"
        parsed = datetime.fromisoformat(val_str)
    except (TypeError, ValueError) as exc:
        raise GuardianContractError("guardian_invalid_observed_timestamp", f"Invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compile_desired_state(
    db: Session,
    server: Server,
    *,
    capabilities: dict | None = None,
) -> dict:
    plugin = get_plugin(server.game_type)
    blueprint = plugin.get_blueprint() if plugin else None
    if blueprint is None:
        raise GuardianCompileError("blueprint_unavailable", "Guardian Blueprint is unavailable")
    ensure_guardian_config_generation(db, server, blueprint)
    payload = compile_state(server, blueprint)
    if capabilities is not None:
        validate_agent_capabilities(payload, capabilities)
    return payload


def compile_and_sync_desired_state(
    db: Session,
    server: Server,
) -> dict:
    node = server.node
    if node is None or node.status != "online":
        raise RuntimeError("Guardian node is not online")
    client = NodeClient.from_node(node, timeout=5.0)
    capabilities = client.get_guardian_capabilities()
    payload = compile_desired_state(db, server, capabilities=capabilities)
    client.set_desired_state(f"msm-srv-{server.id}", payload)
    return payload

def reconcile_guardian_server(
    db: Session,
    server: Server,
    *,
    node_client: NodeClient | None = None,
) -> dict[str, Any]:
    node = server.node
    if node is None or node.status != "online":
        raise RuntimeError("Guardian node is not online")
    client = node_client or NodeClient.from_node(node, timeout=5.0)
    
    try:
        # 1. Capabilities check, compilation and synchronization
        capabilities = client.get_guardian_capabilities()
        payload = compile_desired_state(db, server, capabilities=capabilities)
        client.set_desired_state(f"msm-srv-{server.id}", payload)
        
        # 2. Retrieve observed state
        container_name = f"msm-srv-{server.id}"
        observed = client.get_guardian_state(container_name)
        observed_state = str(
            observed.get("guardian_observed_state")
            or observed.get("observed_runtime_state")
            or "unknown"
        )
        if observed_state not in _OBSERVED_STATES:
            raise ValueError("Agent returned an invalid Guardian observed state")

        container_state = observed.get("container_state") or "unknown"
        if container_state not in _ALLOWED_CONTAINER_STATES:
            raise ValueError(f"Agent returned an invalid container state: {container_state}")

        # Extract timestamps
        probe_ts = observed.get("last_probe_at")
        trans_ts = observed.get("last_transition_at")
        
        probe_dt = _parse_datetime(probe_ts) if probe_ts else None
        trans_dt = _parse_datetime(trans_ts) if trans_ts else None

        # Extract quarantine and recovery_suspension
        q_data = observed.get("quarantine")
        if q_data is not None:
            q_json = json.dumps(q_data, sort_keys=True, separators=(",", ":"))
        else:
            q_json = None

        rs_data = observed.get("recovery_suspension")
        if rs_data is not None:
            rs_json = json.dumps(rs_data, sort_keys=True, separators=(",", ":"))
        else:
            rs_json = None

        accepted_generation = observed.get("accepted_generation")
        if accepted_generation is not None:
            accepted_generation = int(accepted_generation)
        accepted_payload_hash = observed.get("payload_hash")

        # Save observed fields to database
        server.guardian_observed_state = observed_state
        server.guardian_container_status = container_state
        server.guardian_active_incident_uuid = observed.get("active_incident_uuid")
        server.guardian_probe_timestamp = probe_dt
        server.guardian_transition_timestamp = trans_dt
        server.guardian_agent_quarantine_json = q_json
        server.guardian_agent_recovery_suspension_json = rs_json
        server.guardian_accepted_generation = accepted_generation
        server.guardian_last_payload_hash = accepted_payload_hash

        # Sync verification rules
        expected_generation = payload.get("generation")
        expected_payload_hash = payload.get("payload_hash")

        # Check generation mismatch
        if accepted_generation != expected_generation:
            err_data = {
                "code": "guardian_generation_mismatch",
                "expected_generation": expected_generation,
                "accepted_generation": accepted_generation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            server.guardian_sync_error_statistics = json.dumps(err_data, sort_keys=True, separators=(",", ":"))
            db.commit()
            db.refresh(server)
            raise GuardianSyncMismatchError("guardian_generation_mismatch", f"Generation mismatch: expected {expected_generation}, accepted {accepted_generation}")

        # Check hash mismatch
        if accepted_payload_hash != expected_payload_hash:
            err_data = {
                "code": "guardian_payload_hash_mismatch",
                "expected_payload_hash": expected_payload_hash,
                "accepted_payload_hash": accepted_payload_hash,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            server.guardian_sync_error_statistics = json.dumps(err_data, sort_keys=True, separators=(",", ":"))
            db.commit()
            db.refresh(server)
            raise GuardianSyncMismatchError("guardian_payload_hash_mismatch", f"Payload hash mismatch: expected {expected_payload_hash}, accepted {accepted_payload_hash}")

        # Clear sync error and set success timestamp
        server.guardian_last_sync_at = datetime.now(timezone.utc)
        server.guardian_sync_error_statistics = None

        # 3. Handle incidents ingestion
        incidents = client.get_incidents(container_name)
        acknowledged = ingest_incidents_and_ack(
            db,
            server,
            client,
            container_name,
            incidents,
        )
        db.commit()
        db.refresh(server)
        return {
            "payload_hash": payload["payload_hash"],
            "generation": payload["generation"],
            "observed_state": observed_state,
            "acknowledged_incidents": acknowledged,
        }
    except GuardianSyncMismatchError:
        # Re-raise directly to bypass generic error storage
        raise
    except Exception as exc:
        db.rollback()
        # Save last known state on network/API failure
        error_info = {
            "last_error": type(exc).__name__,
            "last_error_message": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            server.guardian_sync_error_statistics = json.dumps(error_info)
            db.commit()
            db.refresh(server)
        except Exception:
            db.rollback()
        raise
