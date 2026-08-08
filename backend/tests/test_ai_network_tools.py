"""Netzwerkdiagnose und Node-Zustand — der Fall "laeuft, aber keiner kommt drauf".

Bisher konnte das Panel dazu nichts sagen: es kannte die vergebenen Ports und
den Serverstatus, aber nicht, ob dort tatsaechlich etwas lauscht. Diese Datei
haelt die drei Eigenschaften fest, auf die es dabei ankommt.

**Die Kernaussage entsteht aus einer Kombination.** Weder "Server laeuft" noch
"Port ist frei" ist fuer sich aussagekraeftig — zusammen sind sie die Diagnose.

**Nicht-Wissen wird als Nicht-Wissen gemeldet.** Fehlt UFW, ist die Antwort
"kann ich nicht sagen" und nicht "Port ist zu". Der Unterschied entscheidet
darueber, ob ein Betreiber an der richtigen Stelle sucht.

**Die Netzstruktur ist nicht Teil des Serverkontexts.** Welche Adressen der Host
hat und was die Firewall durchlaesst, gehoert dem Betreiber — nicht jedem, der
einen Server darauf sehen darf.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import Node, Role, RolePermission, Server, ServerPermission, ServerPort, User
from services import ai_action_service, server_network_diagnostics
from services.role_service import set_user_roles


def _server(db: Session, name: str, *, bind_ip: str | None, status: str = "running") -> Server:
    server = Server(
        name=name, game_type="dayz", install_dir=f"/tmp/{name}",
        status=status, container_name=f"msm-{name}", public_bind_ip=bind_ip,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    db.add(ServerPort(server_id=server.id, role="game", port=25565, protocol="tcp"))
    db.commit()
    return server


def _allow(db: Session, user: User, server: Server, *keys: str) -> None:
    for key in keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


def _global_role(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"net-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])


# ── Bind-IP-Einordnung ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("bind_ip", "kind", "reachable"),
    [
        ("172.17.0.5", "docker", False),
        ("127.0.0.1", "loopback", False),
        ("169.254.1.5", "link_local", False),
        ("192.168.1.50", "private", True),
        ("8.8.8.8", "public", True),
        ("0.0.0.0", "any", True),
        (None, "unset", True),
        ("kein-ip", "invalid", False),
    ],
)
def test_the_bind_ip_is_classified_and_the_problem_named(
    bind_ip: str | None, kind: str, reachable: bool
) -> None:
    """Genau hier entsteht die Aussage "das sieht nach einer Docker-IP aus"."""
    result = server_network_diagnostics._classify_bind_ip(bind_ip)

    assert result["kind"] == kind
    assert result["reachable_from_outside_possible"] is reachable
    assert result["note"]


# ── Die eigentliche Diagnose ──────────────────────────────────────────────

def test_a_running_server_with_a_free_port_is_the_diagnosis(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Kernfall: Container laeuft, aber im Container horcht nichts.

    `is_port_available` beantwortet "ist der Port frei?" — fuer einen laufenden
    Server ist genau das die Umkehrung der Diagnose.
    """
    server = _server(db, "stumm", bind_ip="192.168.1.50", status="running")
    monkeypatch.setattr(
        "services.port_check_service.is_port_available", lambda *_a, **_k: True
    )

    result = server_network_diagnostics.check_reachability(db, server)

    assert result["verdict"] == "running_but_not_listening"
    assert result["ports"][0]["listening"] is False


def test_a_running_server_with_an_occupied_port_is_fine(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(db, "lauscht", bind_ip="192.168.1.50", status="running")
    monkeypatch.setattr(
        "services.port_check_service.is_port_available", lambda *_a, **_k: False
    )

    result = server_network_diagnostics.check_reachability(db, server)

    assert result["verdict"] == "listening"
    assert result["ports"][0]["listening"] is True


def test_a_stopped_server_with_a_free_port_is_not_reported_as_broken(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein gestoppter Server, dessen Port frei ist, ist kein Befund."""
    server = _server(db, "gestoppt", bind_ip="192.168.1.50", status="stopped")
    monkeypatch.setattr(
        "services.port_check_service.is_port_available", lambda *_a, **_k: True
    )

    result = server_network_diagnostics.check_reachability(db, server)

    assert result["verdict"] == "stopped_as_expected"


def test_external_reachability_is_never_claimed(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MSM steht hinter derselben NAT und darf nichts behaupten.

    Ein erfundenes "ist von aussen erreichbar" waere schlimmer als keine
    Aussage — der Betreiber wuerde an der falschen Stelle suchen.
    """
    server = _server(db, "extern", bind_ip="192.168.1.50")
    monkeypatch.setattr(
        "services.port_check_service.is_port_available", lambda *_a, **_k: False
    )

    result = server_network_diagnostics.check_reachability(db, server)

    assert result["external_check"] == "unavailable"
    assert "Router" in result["external_check_reason"]


def test_a_failed_probe_is_reported_as_unknown_not_as_closed(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine gescheiterte Messung ist kein Befund."""
    server = _server(db, "messfehler", bind_ip="192.168.1.50")

    def boom(*_args, **_kwargs):
        raise OSError("keine Berechtigung")

    monkeypatch.setattr("services.port_check_service.is_port_available", boom)

    result = server_network_diagnostics.check_reachability(db, server)

    assert result["verdict"] == "not_measurable"
    assert result["ports"][0]["listening"] is None


# ── Firewall: Nicht-Wissen sauber melden ──────────────────────────────────

def test_a_missing_firewall_is_unknown_not_blocked(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne UFW sagt MSM "kann ich nicht sagen", nicht "Port ist zu"."""
    server = _server(db, "ohne-ufw", bind_ip="192.168.1.50")
    monkeypatch.setattr("services.firewall_service.allowed_ports", lambda: None)

    result = server_network_diagnostics.describe_network(
        db, server, include_host_details=True
    )

    assert result["firewall"]["state"] == "unknown"
    assert "nicht" in result["firewall"]["note"]


def test_an_active_firewall_reports_each_port(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(db, "mit-ufw", bind_ip="192.168.1.50")
    monkeypatch.setattr("services.firewall_service.allowed_ports", lambda: {(25565, "tcp")})

    result = server_network_diagnostics.describe_network(
        db, server, include_host_details=True
    )

    assert result["firewall"]["state"] == "active"
    assert result["firewall"]["ports"][0]["allowed"] is True


# ── Rechtegrenzen ─────────────────────────────────────────────────────────

def test_host_details_need_the_network_permission(
    db: Session, regular_user: User
) -> None:
    """Die Netzstruktur des Betreibers ist nicht Teil des Serverkontexts."""
    server = _server(db, "rechte", bind_ip="192.168.1.50")
    _allow(db, regular_user, server, "server.view")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_server_network",
        arguments={"server_id": server.id},
    )

    assert result["host_details"] == "withheld"
    assert "host_interfaces" not in result
    assert "firewall" not in result
    # Die eigenen Ports darf er sehr wohl sehen.
    assert result["ports"][0]["port"] == 25565


def test_host_details_appear_with_the_network_permission(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(db, "rechte-ok", bind_ip="172.17.0.9")
    _allow(db, regular_user, server, "server.view", "server.network.manage")
    monkeypatch.setattr("services.firewall_service.allowed_ports", lambda: set())

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_server_network",
        arguments={"server_id": server.id},
    )

    assert "host_interfaces" in result
    assert result["bind_ip"]["kind"] == "docker"
    assert result["bind_ip"]["reachable_from_outside_possible"] is False


def test_network_tools_reject_a_server_the_user_may_not_see(
    db: Session, regular_user: User
) -> None:
    """Dieselbe Grenze wie bei jedem anderen serverbezogenen Werkzeug."""
    foreign = _server(db, "fremd", bind_ip="192.168.1.50")

    for tool in ("read_server_network", "check_server_reachability"):
        with pytest.raises(ai_action_service.AiActionValidationError):
            ai_action_service.execute_read_tool(
                db, user=regular_user, tool_name=tool,
                arguments={"server_id": foreign.id},
            )


# ── Bind-IP-Vorschlag ─────────────────────────────────────────────────────

def _conversation(db: Session, user: User):
    from uuid import uuid4

    from models import AiConversation

    row = AiConversation(id=str(uuid4()), user_id=user.id, server_id=None, title="Netz")
    db.add(row)
    db.commit()
    return row


def _propose_bind_ip(db: Session, user: User, server: Server, bind_ip: str):
    from uuid import uuid4

    return ai_action_service.create_proposal(
        db, user=user, conversation=_conversation(db, user),
        tool_name="propose_bind_ip_update",
        arguments={
            "server_id": server.id, "bind_ip": bind_ip,
            "reason": "Docker-Adresse ist von aussen nicht erreichbar.",
            "expected_effect": "Der Server ist im LAN erreichbar.",
        },
        correlation_id=str(uuid4()),
    )


def test_a_bind_ip_that_is_not_on_this_host_is_rejected(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Tippfehler darf keinen laufenden Server unstartbar machen.

    Bindet ein Container an eine Adresse, die es nicht gibt, schlaegt der Bind
    fehl und der Server kommt nicht mehr hoch. Deshalb wird die Adresse gegen
    die tatsaechlichen Schnittstellen geprueft, bevor irgendetwas passiert.
    """
    server = _server(db, "tippfehler", bind_ip="172.17.0.9")
    _allow(db, regular_user, server, "server.view", "server.network.manage")
    monkeypatch.setattr(
        "services.network_interfaces_service.list_host_interfaces", lambda: []
    )

    with pytest.raises(ai_action_service.AiActionValidationError):
        _propose_bind_ip(db, regular_user, server, "192.168.99.99")


def test_a_bind_ip_update_is_never_autonomous(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch mit erteilter Freigabe bleibt eine Netzwerkaenderung bestaetigungspflichtig.

    Sie startet den Container neu und kann bei falscher Adresse dazu fuehren,
    dass niemand mehr auf den Server kommt — das ist keine Aktion, die ohne
    einen Menschen laufen darf.
    """
    from services import ai_autonomy_service

    server = _server(db, "autonom", bind_ip="172.17.0.9")
    _allow(db, regular_user, server, "server.view", "server.network.manage")
    _global_role(db, regular_user, "ai.autonomous.use")
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=None, enabled=True,
        max_actions_per_hour=100, granted_by=regular_user.id,
    )
    db.commit()

    class _Interface:
        ip = "192.168.1.50"

    monkeypatch.setattr(
        "services.network_interfaces_service.list_host_interfaces", lambda: [_Interface()]
    )

    proposal = _propose_bind_ip(db, regular_user, server, "192.168.1.50")

    assert proposal.autonomous is False
    assert proposal.requires_confirmation is True


def test_a_bind_ip_update_needs_the_network_permission(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(db, "ohne-recht", bind_ip="172.17.0.9")
    _allow(db, regular_user, server, "server.view")

    class _Interface:
        ip = "192.168.1.50"

    monkeypatch.setattr(
        "services.network_interfaces_service.list_host_interfaces", lambda: [_Interface()]
    )

    with pytest.raises(ai_action_service.AiActionValidationError):
        _propose_bind_ip(db, regular_user, server, "192.168.1.50")


def test_the_preview_shows_both_addresses_and_the_restart(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Neustart muss in der Vorschau stehen, nicht in der Ueberraschung danach."""
    import json

    server = _server(db, "vorschau", bind_ip="172.17.0.9", status="running")
    _allow(db, regular_user, server, "server.view", "server.network.manage")

    class _Interface:
        ip = "192.168.1.50"

    monkeypatch.setattr(
        "services.network_interfaces_service.list_host_interfaces", lambda: [_Interface()]
    )

    proposal = _propose_bind_ip(db, regular_user, server, "192.168.1.50")
    preview = json.loads(proposal.preview_json)

    assert preview["current_bind_ip"] == "172.17.0.9"
    assert preview["new_bind_ip"] == "192.168.1.50"
    assert preview["current_kind"] == "docker"
    assert preview["new_kind"] == "private"
    assert preview["restart_required"] is True


# ── Node-Zustand ──────────────────────────────────────────────────────────

def test_node_health_needs_the_nodes_read_permission(
    db: Session, regular_user: User
) -> None:
    with pytest.raises(ai_action_service.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="read_node_health", arguments={},
        )


def test_node_health_reports_disk_and_contact_but_never_the_address(
    db: Session, regular_user: User
) -> None:
    """Auslastung ja, Netzstruktur nein.

    Ein Hostname oder eine IP waere weder noetig, um Auslastung zu vergleichen,
    noch harmlos: der Name ist frei befuellter Text und damit ein zusaetzlicher
    Einfallsweg fuer Prompt Injection.
    """
    _global_role(db, regular_user, "nodes.read")
    db.add(Node(
        name="Geheimer Hostname", host="10.9.9.9", auth_token_enc="x",
        is_local=False, status="online", cpu_total=8, cpu_percent=42.0,
        ram_total=16_000_000_000, ram_used=8_000_000_000,
        disk_total=500_000_000_000, disk_used=450_000_000_000,
        docker_connected=True, container_count=7, agent_version="1.2.3",
    ))
    db.commit()

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_node_health", arguments={},
    )

    node = result["nodes"][0]
    assert node["disk_used_percent"] == 90.0
    assert node["ram_used_percent"] == 50.0
    assert node["container_count"] == 7
    serialized = str(result)
    assert "Geheimer Hostname" not in serialized
    assert "10.9.9.9" not in serialized
