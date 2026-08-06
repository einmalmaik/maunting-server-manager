"""Integration tests for Firewall Audit Logging, Crash Cleanup, and Reconciliation."""

from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session

from models import Server, AuditLog
from services import firewall_service
from services.guardian_sync_service import reconcile_guardian_server


def test_open_ports_creates_audit_log(db: Session) -> None:
    server = Server(name="Audit Test Server 1", game_type="seven_days_to_die", status="stopped", install_dir="/tmp/srv1")
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.firewall_service._ufw_available", return_value=True), \
         patch("services.firewall_service._run_ufw") as mock_ufw:
        mock_ufw.return_value = MagicMock(returncode=0, stdout="", stderr="")

        ports = [(26900, "udp", "game"), (26900, "tcp", "web")]
        success = firewall_service.open_ports(
            server.name,
            ports,
            db=db,
            server_id=server.id,
            reason="Server gestartet",
        )
        assert success is True

    audit = db.query(AuditLog).filter(
        AuditLog.action == "server.firewall_opened",
        AuditLog.target_id == server.id,
    ).first()

    assert audit is not None
    assert "Audit Test Server 1" in audit.details
    assert "26900/udp" in audit.details
    assert "Server gestartet" in audit.details


def test_close_ports_creates_audit_log(db: Session) -> None:
    server = Server(name="Audit Test Server 2", game_type="seven_days_to_die", status="running", install_dir="/tmp/srv2")
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.firewall_service._ufw_available", return_value=True), \
         patch("services.firewall_service._run_ufw") as mock_ufw:
        mock_ufw.return_value = MagicMock(returncode=0, stdout="Rule deleted\n", stderr="")

        ports = [(26900, "udp", "game")]
        success = firewall_service.close_ports(
            ports,
            db=db,
            server_id=server.id,
            name=server.name,
            reason="Server gestoppt",
        )
        assert success is True

    audit = db.query(AuditLog).filter(
        AuditLog.action == "server.firewall_closed",
        AuditLog.target_id == server.id,
    ).first()

    assert audit is not None
    assert "Audit Test Server 2" in audit.details
    assert "26900/udp" in audit.details
    assert "Server gestoppt" in audit.details


def test_guardian_sync_detects_crash_and_closes_ports(db: Session) -> None:
    from models import Node
    node = Node(name="Test Node", host="127.0.0.1", auth_token_enc="enc", status="online", is_local=True)
    db.add(node)
    db.commit()

    server = Server(
        name="Crashed Server",
        game_type="seven_days_to_die",
        status="running",
        game_port=26900,
        node_id=node.id,
        install_dir="/tmp/srv3",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    mock_client = MagicMock()
    mock_client.get_guardian_capabilities.return_value = {
        "guardian_schema_versions": [1],
        "parsers": ["unity"],
        "health_checks": ["process"],
        "policies": ["restart"],
    }
    mock_client.set_desired_state.return_value = {
        "generation": 1,
        "payload_hash": "sha256:" + "a" * 64,
    }
    mock_client.get_guardian_state.return_value = {
        "schema_version": 1,
        "supported_schema_version": 1,
        "server_id": server.id,
        "accepted_generation": 1,
        "payload_hash": "sha256:" + "a" * 64,
        "guardian_observed_state": "stopped",
        "observed_runtime_state": "stopped",
        "container_state": "exited",
        "active_incident_uuid": None,
        "last_probe_at": "2026-08-06T18:00:00Z",
        "last_transition_at": "2026-08-06T18:00:00Z",
        "quarantine": None,
        "recovery_suspension": None,
    }

    with patch("services.guardian_sync_service.NodeClient.from_node", return_value=mock_client), \
         patch("services.firewall_service._ufw_available", return_value=True), \
         patch("services.firewall_service._run_ufw") as mock_ufw:
        mock_ufw.return_value = MagicMock(returncode=0, stdout="Rule deleted\n", stderr="")

        reconcile_guardian_server(db, server, node_client=mock_client)

    db.refresh(server)
    assert server.status == "stopped"

    audit = db.query(AuditLog).filter(
        AuditLog.action == "server.firewall_closed",
        AuditLog.target_id == server.id,
    ).first()

    assert audit is not None
    assert "Crash-Cleanup (Guardian/Sync)" in audit.details


def test_reconcile_firewall_rules_cleans_stray_rules(db: Session) -> None:
    server = Server(name="Stray Port Server", game_type="seven_days_to_die", status="stopped", install_dir="/tmp/srv4")
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.firewall_service._ufw_available", return_value=True), \
         patch("services.firewall_service._run_ufw") as mock_ufw:
        mock_ufw.return_value = MagicMock(returncode=0, stdout="Rule deleted\n", stderr="")

        reconciled = firewall_service.reconcile_firewall_rules(db)
        assert reconciled >= 1

    audit = db.query(AuditLog).filter(
        AuditLog.action == "server.firewall_closed",
        AuditLog.target_id == server.id,
    ).first()

    assert audit is not None
    assert "Audit-Reconciliation" in audit.details
