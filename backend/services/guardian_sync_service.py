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


def _parse_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
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

        # Sync observed fields
        server.guardian_last_payload_hash = payload["payload_hash"]
        server.guardian_container_status = observed.get("container_status")
        server.guardian_active_incident_uuid = observed.get("active_incident_uuid")
        
        probe_ts = observed.get("probe_timestamp")
        server.guardian_probe_timestamp = _parse_datetime(probe_ts) if probe_ts else None
        
        trans_ts = observed.get("transition_timestamp")
        server.guardian_transition_timestamp = _parse_datetime(trans_ts) if trans_ts else None
        
        server.guardian_quarantine_status = observed.get("quarantine_status")
        server.guardian_sync_error_statistics = None
        
        if server.guardian_observed_state != observed_state:
            server.guardian_observed_state = observed_state

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
