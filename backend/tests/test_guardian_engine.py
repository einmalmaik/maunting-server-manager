from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from blueprints.schema import load_blueprint_dict
from models import Incident, Node, Server
from models.server_port import ServerPort
from services.guardian_runtime_compiler import (
    GuardianCompileError,
    canonical_payload_hash,
    compile_desired_state,
    validate_agent_capabilities,
)
from services.guardian_state_service import (
    ensure_guardian_config_generation,
    set_desired_power_state,
)
from services.guardian_sync_service import (
    ingest_incidents_and_ack,
    reconcile_guardian_server,
)
from services.scheduler_service import _guardian_reconciliation_task


def _blueprint(*, unresolved: bool = False):
    port = "{{PORT:missing}}" if unresolved else "{{PORT:query}}"
    return load_blueprint_dict(
        {
            "version": 1,
            "meta": {
                "id": "guardian_runtime_test",
                "name": "Guardian Runtime Test",
                "category": "bot",
                "description": "",
            },
            "runtime": {
                "image": "synthetic.invalid/runtime:1",
                "startup": "./start",
                "env": {},
            },
            "ports": [
                {"name": "game", "protocol": "tcp"},
                {"name": "query", "protocol": "udp"},
            ],
            "source": {"type": "dockerOnly", "updateStrategy": "none"},
            "health": {
                "process": {"required": True, "id": "process"},
                "port": {
                    "id": "game-port",
                    "protocol": "tcp",
                    "port": "{{SERVER_PORT}}",
                    "timeout": "2s",
                },
                "application": {
                    "id": "query",
                    "type": "minecraft-query",
                    "port": port,
                    "interval": "30s",
                    "timeout": "3s",
                },
                "startup": {
                    "grace_period_seconds": 10,
                    "timeout_seconds": 120,
                },
            },
            "diagnostics": {"parsers": ["linux-oom", "port-conflict"]},
            "recovery": {
                "policies": [
                    {"match": "linux-oom", "action": "graceful_restart"},
                    {"match": "port-conflict", "action": "clear_declared_lock_files"},
                ],
                "safe_lock_files": [
                    {"path": "runtime/server.lock", "reason": "synthetic stale lock"}
                ],
            },
        }
    )


def _server(*, desired: str = "running", generation: int = 7) -> Server:
    server = Server(
        id=42,
        name="Synthetic",
        game_type="guardian_runtime_test",
        install_dir="/synthetic/not-real",
        status="stopped",
        desired_power_state=desired,
        desired_state_generation=generation,
        guardian_observed_state="unknown",
        public_bind_ip="127.0.0.1",
    )
    server.ports = [
        ServerPort(role="game", port=25565, protocol="tcp"),
        ServerPort(role="query", port=25566, protocol="udp"),
    ]
    return server


def _capabilities() -> dict:
    return {
        "guardian_schema_versions": [1],
        "probe_types": [
            "process",
            "tcp",
            "udp_port_mapping",
            "http-ping",
            "minecraft-status",
            "minecraft-query",
            "source-query",
        ],
        "diagnostic_parsers": [
            "linux-oom",
            "java-stacktrace",
            "nodejs-stacktrace",
            "port-conflict",
            "missing-runtime",
            "corrupted-config",
            "startup-pattern",
        ],
        "recovery_actions": [
            "restart",
            "graceful_restart",
            "clear_declared_lock_files",
            "quarantine",
        ],
    }


def test_runtime_compiler_resolves_tokens_and_hashes_canonically() -> None:
    payload = compile_desired_state(_server(), _blueprint())
    checks = {item["check_id"]: item for item in payload["guardian"]["health_checks"]}
    assert checks["game-port"]["target_port"] == 25565
    assert checks["query"]["target_port"] == 25566
    assert checks["query"]["target_host"] == "127.0.0.1"
    assert payload["desired_power_state"] == "running"
    assert payload["generation"] == 7
    assert payload["payload_hash"] == canonical_payload_hash(payload)
    assert payload == compile_desired_state(_server(), _blueprint())


def test_runtime_compiler_rejects_unresolved_tokens_and_missing_target() -> None:
    with pytest.raises(GuardianCompileError) as token_error:
        compile_desired_state(_server(), _blueprint(unresolved=True))
    assert token_error.value.code == "unresolved_placeholder"

    server = _server()
    server.public_bind_ip = None
    with pytest.raises(GuardianCompileError) as target_error:
        compile_desired_state(server, _blueprint())
    assert target_error.value.code == "probe_target_unavailable"


def test_capability_mismatch_lists_every_unsupported_requirement() -> None:
    payload = compile_desired_state(_server(), _blueprint())
    with pytest.raises(GuardianCompileError) as caught:
        validate_agent_capabilities(
            payload,
            {
                "guardian_schema_versions": [],
                "probe_types": ["process"],
                "diagnostic_parsers": [],
                "recovery_actions": [],
            },
        )
    unsupported = caught.value.details["unsupported"]
    assert unsupported["guardian_schema_versions"] == [1]
    assert unsupported["probe_types"] == ["minecraft-query", "tcp"]
    assert unsupported["diagnostic_parsers"] == ["linux-oom", "port-conflict"]
    assert unsupported["recovery_actions"] == [
        "clear_declared_lock_files",
        "graceful_restart",
    ]


def test_desired_state_is_independent_and_generation_changes_only_on_intent(
    db: Session,
) -> None:
    server = _server(desired="stopped", generation=1)
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)
    original_status = server.status

    assert set_desired_power_state(db, server, "running") is True
    assert server.desired_power_state == "running"
    assert server.desired_state_generation == 2
    assert server.status == original_status
    assert set_desired_power_state(db, server, "running") is False
    assert server.desired_state_generation == 2


def test_effective_configuration_hash_increments_generation_once(db: Session) -> None:
    server = _server(generation=1)
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)
    assert ensure_guardian_config_generation(db, server, _blueprint()) is False
    assert server.desired_state_generation == 1

    query = next(port for port in server.ports if port.role == "query")
    query.port = 25567
    db.commit()
    assert ensure_guardian_config_generation(db, server, _blueprint()) is True
    assert server.desired_state_generation == 2
    assert ensure_guardian_config_generation(db, server, _blueprint()) is False
    assert server.desired_state_generation == 2


def _agent_incident(server_id: int, incident_uuid: str | None = None, status: str = "open") -> dict:
    value = incident_uuid or str(uuid.uuid4())
    return {
        "uuid": value,
        "server_id": server_id,
        "created_at": "2026-07-19T12:00:00Z",
        "updated_at": "2026-07-19T12:00:01Z",
        "type": "probe_failed",
        "status": status,
        "fingerprint": f"guardian:{server_id}:probe_failed",
        "payload": {
            "schema_version": 1,
            "message": "Synthetic redacted incident",
            "attempts": [{"attempt": 1, "result": "failed"}],
        },
    }


def test_incident_ingestion_is_uuid_idempotent_and_acks_after_commit(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)
    incident_uuid = str(uuid.uuid4())
    client = MagicMock()
    item = _agent_incident(server.id, incident_uuid)

    assert ingest_incidents_and_ack(db, server, client, "msm-srv-1", [item]) == [incident_uuid]
    client.acknowledge_incidents.assert_called_once_with("msm-srv-1", [incident_uuid])
    assert db.query(Incident).filter(Incident.uuid == incident_uuid).count() == 1

    client.reset_mock()
    item["status"] = "resolved"
    assert ingest_incidents_and_ack(db, server, client, "msm-srv-1", [item]) == [incident_uuid]
    assert db.query(Incident).filter(Incident.uuid == incident_uuid).count() == 1
    assert db.query(Incident).filter(Incident.uuid == incident_uuid).one().status == "resolved"


def test_database_failure_sends_no_incident_ack(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)
    client = MagicMock()
    with patch.object(db, "commit", side_effect=RuntimeError("synthetic commit failure")):
        with pytest.raises(RuntimeError):
            ingest_incidents_and_ack(
                db,
                server,
                client,
                "msm-srv-1",
                [_agent_incident(server.id)],
            )
    client.acknowledge_incidents.assert_not_called()


def test_ack_failure_after_commit_keeps_panel_incident(db: Session) -> None:
    server = _server()
    server.id = None
    db.add(server)
    db.commit()
    db.refresh(server)
    incident_uuid = str(uuid.uuid4())
    client = MagicMock()
    client.acknowledge_incidents.side_effect = RuntimeError("synthetic network failure")
    with pytest.raises(RuntimeError):
        ingest_incidents_and_ack(
            db,
            server,
            client,
            "msm-srv-1",
            [_agent_incident(server.id, incident_uuid)],
        )
    assert db.query(Incident).filter(Incident.uuid == incident_uuid).count() == 1


def test_stopped_desired_state_is_sent_and_observation_does_not_change_intent(
    db: Session,
) -> None:
    node = Node(
        name="Synthetic local node",
        host="http://127.0.0.1:9000",
        auth_token_enc="synthetic",
        is_local=True,
        status="online",
    )
    server = _server(desired="stopped")
    server.id = None
    server.node = node
    db.add_all([node, server])
    db.commit()
    db.refresh(server)
    client = MagicMock()
    client.get_guardian_capabilities.return_value = _capabilities()
    client.get_guardian_state.return_value = {
        "schema_version": 1,
        "server_id": server.id,
        "accepted_generation": 7,
        "payload_hash": None,  # will set dynamically
        "guardian_observed_state": "healthy",
        "observed_runtime_state": "healthy",
        "container_state": "running",
        "active_incident_uuid": None,
        "last_probe_at": "2026-07-20T12:00:00Z",
        "last_transition_at": "2026-07-20T11:59:00Z",
        "quarantine": None,
        "recovery_suspension": None,
        "supported_schema_version": 1,
    }
    client.get_incidents.return_value = []
    plugin = MagicMock()
    plugin.get_blueprint.return_value = _blueprint()

    with patch("services.guardian_sync_service.get_plugin", return_value=plugin):
        from services.guardian_sync_service import compile_desired_state
        payload = compile_desired_state(db, server)
        client.get_guardian_state.return_value["payload_hash"] = payload["payload_hash"]
        result = reconcile_guardian_server(db, server, node_client=client)

    sent = client.set_desired_state.call_args.args[1]
    assert sent["desired_power_state"] == "stopped"
    assert server.desired_power_state == "stopped"
    assert server.guardian_observed_state == "healthy"
    assert result["observed_state"] == "healthy"


def test_scheduler_processes_stopped_servers() -> None:
    node = Node(id=1, name="n", host="http://127.0.0.1", auth_token_enc="x", status="online")
    server = Server(id=42, name="s", game_type="x", install_dir="/x", status="stopped", node=node)
    server.node = node
    fake_db = MagicMock(spec=Session)
    fake_db.query.return_value.filter.return_value.all.return_value = [server]
    mock_client = MagicMock()
    with patch("services.guardian_reconciliation_service.SessionLocal", return_value=fake_db), patch(
        "services.guardian_reconciliation_service.NodeClient.from_node", return_value=mock_client
    ), patch(
        "services.guardian_reconciliation_service.reconcile_guardian_server"
    ) as reconcile:
        asyncio.run(_guardian_reconciliation_task())
    reconcile.assert_called_once()
    args, kwargs = reconcile.call_args
    assert args[0] is fake_db
    assert args[1] == server



