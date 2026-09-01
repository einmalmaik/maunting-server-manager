"""Gebuchter und tatsaechlich belegter Arbeitsspeicher sind zweierlei.

Der Fehler aus dem Betrieb: auf "richte einen Minecraft-Server ein" lehnte die
KI mit "kein freier RAM" ab — obwohl von sieben Servern nur drei liefen. Sie
sah ausschliesslich `ram_allocated_mb`, also die Summe **aller** zugewiesenen
Grenzen einschliesslich gestoppter Server.

`ram_limit_mb` ist eine Buchung, keine Messung. Vier gestoppte Server zu je
8 GB buchen 32 GB und belegen null.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models import Node, Role, RolePermission, Server, ServerPermission, User
from services import ai_action_service
from services.node_capacity import sum_allocated_ram_mb, sum_running_ram_mb
from services.role_service import set_user_roles


def _node(db: Session) -> Node:
    node = Node(
        name="kapazitaet", host="10.0.0.9", auth_token_enc="x", status="online",
        is_local=True,
        cpu_total=8, ram_total=32 * 1024 * 1024 * 1024,
        ram_used=6 * 1024 * 1024 * 1024,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def _server(db: Session, node: Node, name: str, status: str, ram_mb: int) -> Server:
    server = Server(
        name=name, game_type="dayz", install_dir=f"/tmp/{name}", status=status,
        container_name=f"msm-{name}", node_id=node.id, ram_limit_mb=ram_mb,
    )
    db.add(server)
    db.commit()
    return server


def _allow(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"kap-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def test_stopped_servers_book_ram_but_do_not_consume_it(db: Session) -> None:
    node = _node(db)
    _server(db, node, "laeuft", "running", 8_192)
    _server(db, node, "steht-1", "stopped", 8_192)
    _server(db, node, "steht-2", "stopped", 8_192)

    assert sum_allocated_ram_mb(db, node.id) == 24_576
    # Nur der laufende zaehlt.
    assert sum_running_ram_mb(db, node.id) == 8_192


def test_starting_and_restarting_count_as_consuming(db: Session) -> None:
    """Dort laeuft der Container bereits — der Speicher ist belegt."""
    node = _node(db)
    _server(db, node, "startet", "starting", 4_096)
    _server(db, node, "neustart", "restarting", 4_096)
    _server(db, node, "steht", "stopped", 4_096)

    assert sum_running_ram_mb(db, node.id) == 8_192


def test_the_tool_reports_both_numbers(db: Session, regular_user: User) -> None:
    """Ohne beide Zahlen kann das Modell den Unterschied gar nicht erkennen."""
    node = _node(db)
    _server(db, node, "laeuft", "running", 8_192)
    _server(db, node, "steht", "stopped", 16_384)
    _allow(db, regular_user, "servers.create")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_node_capacity", arguments={},
    )

    eintrag = next(item for item in result["nodes"] if item["node_id"] == node.id)
    assert eintrag["ram_allocated_mb"] == 24_576
    assert eintrag["ram_allocated_running_mb"] == 8_192
    # Und die Messung der Node selbst, als dritte unabhaengige Zahl.
    assert eintrag["ram_used_mb"] == 6_144
    assert eintrag["ram_total_mb"] == 32_768
    assert eintrag["ram_real_free_mb"] == 32_768 - 6_144
    assert eintrag["overcommit_allowed"] is True


def test_the_tool_handles_node_metrics_already_in_mb(db: Session, regular_user: User) -> None:
    """In Produktion speichert node_service ram_total / ram_used bereits in Megabyte."""
    node = Node(
        name="mb-node", host="10.0.0.12", auth_token_enc="x", status="online",
        is_local=True,
        cpu_total=8, ram_total=32_768,  # 32 GB in MB
        ram_used=16_384,  # 16 GB in MB
    )
    db.add(node)
    db.commit()
    _allow(db, regular_user, "servers.create")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_node_capacity", arguments={},
    )
    eintrag = next(item for item in result["nodes"] if item["node_id"] == node.id)
    assert eintrag["ram_total_mb"] == 32_768
    assert eintrag["ram_used_mb"] == 16_384
    assert eintrag["ram_real_free_mb"] == 16_384
    assert eintrag["overcommit_allowed"] is True


def test_the_tool_description_names_the_difference(db: Session) -> None:
    """Die Zahlen allein reichen nicht — das Modell muss wissen, was sie bedeuten."""
    definition = next(
        item for item in ai_action_service.provider_tool_definitions()
        if item["function"]["name"] == "read_node_capacity"
    )
    beschreibung = definition["function"]["description"]
    assert "gestoppter" in beschreibung.lower()
    assert "ram_allocated_running_mb" in beschreibung


def test_a_view_only_user_does_not_get_the_hosts_numbers(
    db: Session, regular_user: User
) -> None:
    """Die Auslastung des Hosts ist Sache des Betreibers, nicht des Kunden.

    `_resolve_server` prueft nur `server.view`. Damit gab `read_server_capacity`
    jedem Hosting-Kunden `ram_allocated_mb` heraus — die Summe der Buchungen
    **aller** Server auf dieser Node, auch der fremden — dazu Kernzahl, RAM-
    und Plattengroesse der Maschine. Ueber `read_node_capacity` bekaeme er
    dieselben Zahlen nicht: das verlangt `servers.create`. Zwei Wege zu
    denselben Daten mit verschiedenen Huerden sind keine Grenze.
    """
    node = _node(db)
    meiner = _server(db, node, "meiner", "running", 4_096)
    _server(db, node, "fremder", "running", 12_288)
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=meiner.id, permission_key="server.view"
    ))
    db.commit()

    zurueckgehalten = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_server_capacity",
        arguments={"server_id": meiner.id},
    )

    assert zurueckgehalten["node_details"] == "withheld"
    assert "ram_allocated_mb" not in zurueckgehalten
    assert "cpu_total" not in zurueckgehalten
    assert "disk_total_bytes" not in zurueckgehalten

    # Die Gegenprobe: wer die Grenzen dieses Servers aendern darf, braucht die
    # Zahlen — sonst kann die KI nicht sagen, ob mehr RAM ueberhaupt hineinpasst.
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=meiner.id,
        permission_key="server.resources.manage",
    ))
    db.commit()

    erlaubt = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_server_capacity",
        arguments={"server_id": meiner.id},
    )

    assert erlaubt["ram_allocated_mb"] == 16_384
    assert erlaubt["cpu_total"] == 8
