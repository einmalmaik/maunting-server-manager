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
    server.set_port("game", 26900, "udp")
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


def test_reconcile_firewall_rules_schweigt_ohne_ports(db: Session) -> None:
    """Ein Server ohne Ports erzeugt weder einen UFW-Aufruf noch eine Audit-Zeile.

    Der Abgleich läuft alle 30 Sekunden. Schreibt er für jeden gestoppten
    Server eine Zeile, besteht das Audit-Log nach einem Tag fast nur noch aus
    diesem Rauschen — und die echten privilegierten Aktionen sind darin nicht
    mehr zu finden.
    """
    server = Server(
        name="Portloser Server", game_type="seven_days_to_die",
        status="stopped", install_dir="/tmp/srv-portlos",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.firewall_service._ufw_available", return_value=True) as mock_available, \
         patch("services.firewall_service._run_ufw") as mock_ufw:
        mock_ufw.return_value = MagicMock(returncode=0, stdout="Rule deleted\n", stderr="")
        firewall_service.reconcile_firewall_rules(db)

    assert mock_available.call_count == 0
    assert mock_ufw.call_count == 0
    assert db.query(AuditLog).filter(
        AuditLog.action == "server.firewall_closed",
        AuditLog.target_id == server.id,
    ).count() == 0


def test_reconcile_firewall_rules_schweigt_wenn_regel_schon_weg_ist(db: Session) -> None:
    """Findet UFW nichts zu löschen, entsteht keine Audit-Zeile.

    UFW meldet für eine nicht existierende Regel Exit 0 mit "Could not
    delete". Früher galt das als Erfolg, und jeder Durchlauf protokollierte
    eine Schließung, die es nie gab.
    """
    server = Server(
        name="Schon Zu Server", game_type="seven_days_to_die",
        status="stopped", install_dir="/tmp/srv-schon-zu",
    )
    server.set_port("game", 26902, "udp")
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.firewall_service._ufw_available", return_value=True), \
         patch("services.firewall_service._run_ufw") as mock_ufw:
        mock_ufw.return_value = MagicMock(
            returncode=0, stdout="Could not delete non-existent rule\n", stderr="",
        )
        firewall_service.reconcile_firewall_rules(db)

    assert mock_ufw.call_count >= 1
    assert db.query(AuditLog).filter(
        AuditLog.action == "server.firewall_closed",
        AuditLog.target_id == server.id,
    ).count() == 0


def test_reconcile_firewall_rules_keeps_starting_and_restarting_open(db: Session) -> None:
    """Lifecycle öffnet UFW vor status=running — Reconcile darf starting/restarting nicht schließen.

    Der Fehler war im Betrieb schwer zu greifen: der Container lief, das Panel
    meldete "running", und trotzdem kam von aussen niemand drauf. Ursache war
    dieses Zeitfenster — `open_ports` laeuft vor dem Status-Flip, und der
    Reconcile-Job hielt den startenden Server fuer einen gestoppten.
    """
    starting = Server(
        name="Starting Keep Open",
        game_type="seven_days_to_die",
        status="starting",
        install_dir="/tmp/srv-starting",
    )
    restarting = Server(
        name="Restarting Keep Open",
        game_type="seven_days_to_die",
        status="restarting",
        install_dir="/tmp/srv-restarting",
    )
    running = Server(
        name="Running Keep Open",
        game_type="seven_days_to_die",
        status="running",
        install_dir="/tmp/srv-running",
    )
    stopped = Server(
        name="Stopped Close Ok",
        game_type="seven_days_to_die",
        status="stopped",
        install_dir="/tmp/srv-stopped",
    )
    # Alle vier bekommen einen Port, damit allein der Status entscheidet, wer
    # geschlossen wird — und nicht nebenbei die Portlosigkeit.
    for index, srv in enumerate((starting, restarting, running, stopped)):
        srv.set_port("game", 26910 + index, "udp")
    db.add_all([starting, restarting, running, stopped])
    db.commit()
    for s in (starting, restarting, running, stopped):
        db.refresh(s)

    with patch("services.firewall_service._ufw_available", return_value=True), \
         patch("services.firewall_service._run_ufw") as mock_ufw, \
         patch("services.firewall_service.close_ports", wraps=firewall_service.close_ports) as mock_close:
        mock_ufw.return_value = MagicMock(returncode=0, stdout="Rule deleted\n", stderr="")
        firewall_service.reconcile_firewall_rules(db)

    closed_ids = {
        call.kwargs.get("server_id")
        for call in mock_close.call_args_list
        if call.kwargs.get("server_id") is not None
    }
    assert starting.id not in closed_ids
    assert restarting.id not in closed_ids
    assert running.id not in closed_ids
    assert stopped.id in closed_ids


def test_reconcile_firewall_rules_still_closes_stopping_and_queued(db: Session) -> None:
    """Die Ausnahme gilt eng: `stopping` und `queued` bleiben Aufraeumfaelle.

    Bei `stopping` sollen die Ports gerade zufallen — das ist der Zweck. Bei
    `queued` sind sie noch gar nicht geoeffnet worden. Beide in die Ausnahme
    aufzunehmen haette aus einem Fix ein Leck gemacht.
    """
    stopping = Server(
        name="Stopping Close Ok", game_type="seven_days_to_die",
        status="stopping", install_dir="/tmp/srv-stopping",
    )
    queued = Server(
        name="Queued Close Ok", game_type="seven_days_to_die",
        status="queued", install_dir="/tmp/srv-queued",
    )
    stopping.set_port("game", 26920, "udp")
    queued.set_port("game", 26921, "udp")
    db.add_all([stopping, queued])
    db.commit()
    db.refresh(stopping)
    db.refresh(queued)

    with patch("services.firewall_service._ufw_available", return_value=True), \
         patch("services.firewall_service._run_ufw") as mock_ufw, \
         patch("services.firewall_service.close_ports", wraps=firewall_service.close_ports) as mock_close:
        mock_ufw.return_value = MagicMock(returncode=0, stdout="Rule deleted\n", stderr="")
        firewall_service.reconcile_firewall_rules(db)

    closed_ids = {
        call.kwargs.get("server_id")
        for call in mock_close.call_args_list
        if call.kwargs.get("server_id") is not None
    }
    assert stopping.id in closed_ids
    assert queued.id in closed_ids
