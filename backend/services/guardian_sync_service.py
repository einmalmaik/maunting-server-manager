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
            raise GuardianContractError("guardian_invalid_observed_timestamp", "Naive datetime objects are not allowed")
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
        raise GuardianContractError("guardian_invalid_observed_timestamp", f"Naive timestamp strings are not allowed: {value}")
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
    try:
        client = node_client or NodeClient.from_node(node, timeout=5.0)
        
        # 1. Capabilities check, compilation and synchronization
        capabilities = client.get_guardian_capabilities()
        payload = compile_desired_state(db, server, capabilities=capabilities)
        server.guardian_last_payload_hash = payload.get("payload_hash")
        client.set_desired_state(f"msm-srv-{server.id}", payload)
        
        # 2. Retrieve observed state
        container_name = f"msm-srv-{server.id}"
        observed = client.get_guardian_state(container_name)
        
        required_fields = [
            "schema_version",
            "supported_schema_version",
            "server_id",
            "accepted_generation",
            "payload_hash",
            "guardian_observed_state",
            "observed_runtime_state",
            "container_state",
            "active_incident_uuid",
            "last_probe_at",
            "last_transition_at",
        ]
        for field in required_fields:
            if field not in observed:
                raise GuardianContractError("guardian_missing_observed_field", f"Missing {field}")
        
        if observed.get("schema_version") != 1:
            raise GuardianContractError("guardian_schema_version_mismatch", "Schema version mismatch")
        if observed.get("supported_schema_version") != 1:
            raise GuardianContractError("guardian_schema_version_mismatch", "Supported schema version mismatch")
        if observed.get("server_id") != server.id:
            raise GuardianContractError("guardian_server_id_mismatch", "Server ID mismatch")
            
        accepted_generation = observed.get("accepted_generation")
        if type(accepted_generation) is not int:
            raise GuardianContractError("guardian_invalid_observed_field", "accepted_generation must be an integer")
            
        accepted_payload_hash = observed.get("payload_hash")
        import re
        if not accepted_payload_hash or type(accepted_payload_hash) is not str or not re.match(r'^sha256:[a-f0-9]{64}$', accepted_payload_hash):
            raise GuardianContractError("guardian_invalid_observed_field", "Invalid payload_hash format")
            
        observed_state = observed.get("guardian_observed_state")
        observed_runtime_state = observed.get("observed_runtime_state")
        if observed_state != observed_runtime_state:
            raise GuardianContractError("guardian_observed_state_mismatch", "Observed states do not match")
            
        if observed_state not in _OBSERVED_STATES:
            raise GuardianContractError("guardian_invalid_observed_field", "Agent returned an invalid Guardian observed state")

        container_state = observed.get("container_state")
        if container_state not in _ALLOWED_CONTAINER_STATES:
            raise GuardianContractError("guardian_invalid_observed_field", f"Agent returned an invalid container state: {container_state}")

        q_data = observed.get("quarantine")
        if q_data is not None and not isinstance(q_data, dict):
            raise GuardianContractError("guardian_invalid_observed_field", "quarantine must be null or an object")
            
        rs_data = observed.get("recovery_suspension")
        if rs_data is not None and not isinstance(rs_data, dict):
            raise GuardianContractError("guardian_invalid_observed_field", "recovery_suspension must be null or an object")

        # Extract timestamps
        probe_ts = observed.get("last_probe_at")
        trans_ts = observed.get("last_transition_at")
        
        probe_dt = _parse_datetime(probe_ts) if probe_ts else None
        trans_dt = _parse_datetime(trans_ts) if trans_ts else None

        # Extract quarantine and recovery_suspension
        if q_data is not None:
            q_json = json.dumps(q_data, sort_keys=True, separators=(",", ":"))
        else:
            q_json = None

        if rs_data is not None:
            rs_json = json.dumps(rs_data, sort_keys=True, separators=(",", ":"))
        else:
            rs_json = None

        # Save observed fields to database
        server.guardian_observed_state = observed_state
        server.guardian_container_status = container_state
        server.guardian_active_incident_uuid = observed.get("active_incident_uuid")
        server.guardian_probe_timestamp = probe_dt
        server.guardian_transition_timestamp = trans_dt
        server.guardian_agent_quarantine_json = q_json
        server.guardian_agent_recovery_suspension_json = rs_json
        server.guardian_accepted_generation = accepted_generation
        server.guardian_accepted_payload_hash = accepted_payload_hash

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
        db.commit()

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
            
        import requests
        from services.node_client import NodeClientError
        if isinstance(exc, (NodeClientError, requests.exceptions.RequestException, ConnectionError)):
            logger.warning("Transient Guardian sync error for server %s: %s", server.id, exc)
            return {
                "payload_hash": None,
                "generation": None,
                "observed_state": "unknown",
                "acknowledged_incidents": [],
            }
            
        raise

    # 3. Handle incidents ingestion outside the main sync try/except
    # so that incident failures don't overwrite successful sync status.
    try:
        incidents = client.get_incidents(container_name)
        acknowledged = ingest_incidents_and_ack(
            db,
            server,
            client,
            container_name,
            incidents,
        )
    except Exception as exc:
        error_info = {
            "last_error": type(exc).__name__,
            "last_error_message": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context": "incident_sync"
        }
        try:
            server.guardian_sync_error_statistics = json.dumps(error_info)
            db.commit()
        except Exception:
            db.rollback()
        raise
    db.commit()
    db.refresh(server)
    
    from services.guardian_restart_service import _trigger_guardian_auto_restart
    _trigger_guardian_auto_restart(db, server.id)
    
    return {
        "payload_hash": payload["payload_hash"],
        "generation": payload["generation"],
        "observed_state": observed_state,
        "acknowledged_incidents": acknowledged,
    }
