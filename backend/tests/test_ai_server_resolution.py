"""Der Serverbezug haengt am Werkzeugaufruf — und wird dort geprueft.

Mit dem Einzelchat ist die serverbezogene Unterhaltung entfallen. Vorher war
sie die Huelle, die den Zugriff begrenzte: `execute_read_tool` las den Server
aus `conversation.server_id`, und diese Zeile war beim Anlegen einmal geprueft
worden. Jetzt nennt das **Modell** die `server_id` — und Modelle bekommen ihre
Eingaben unter anderem aus Serverlogs, Konfigurationsdateien und Anhaengen, also
aus Text, den ein Spieler oder Angreifer geschrieben haben kann.

Deshalb ist `_resolve_server` die Stelle, an der die Aussage "die KI hat exakt
die Rechte des Benutzers" steht oder faellt. Diese Datei testet genau sie.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, Role, RolePermission, Server, ServerPermission, User
from services import ai_action_errors, ai_action_service, ai_proposal_service
from services.role_service import set_user_roles


def _server(db: Session, name: str) -> Server:
    server = Server(
        name=name,
        game_type="dayz",
        install_dir=f"/tmp/{name}",
        status="stopped",
        container_name=f"msm-{name}",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _conversation(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Aufloesung"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _allow(db: Session, user: User, server: Server, *keys: str) -> None:
    for key in keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    db.commit()


def test_a_foreign_server_id_is_rejected_for_every_read_tool(
    db: Session, regular_user: User
) -> None:
    """Eine fremde ID darf kein einziges Lesewerkzeug oeffnen."""
    mine = _server(db, "meiner")
    foreign = _server(db, "fremder")
    _allow(db, regular_user, mine, "server.view", "server.console.read")

    for tool in ("read_server_status", "read_server_ports", "read_server_backups"):
        with pytest.raises(ai_action_errors.AiActionValidationError):
            ai_action_service.execute_read_tool(
                db, user=regular_user, tool_name=tool,
                arguments={"server_id": foreign.id},
            )


def test_a_foreign_server_id_is_rejected_for_a_write_proposal(
    db: Session, regular_user: User
) -> None:
    """Auch ein Vorschlag entsteht nicht fuer einen fremden Server."""
    mine = _server(db, "schreib-meiner")
    foreign = _server(db, "schreib-fremder")
    _allow(db, regular_user, mine, "server.view", "server.backups.create")
    _allow(db, regular_user, foreign, "server.backups.create")  # bewusst ohne server.view
    conversation = _conversation(db, regular_user)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_backup",
            arguments={
                "server_id": foreign.id,
                "reason": "Vorwand",
                "expected_effect": "Nichts",
            },
            correlation_id=str(uuid4()),
        )


def test_a_missing_or_bogus_server_id_never_falls_back_to_a_default(
    db: Session, regular_user: User
) -> None:
    """Fehlt die ID, wird nicht geraten — auch nicht bei genau einem Server.

    Ein stillschweigender Rueckfall auf "den einen sichtbaren Server" waere
    bequem und genau deshalb gefaehrlich: er wuerde in dem Moment falsch, in dem
    der Benutzer einen zweiten Server bekommt.
    """
    only = _server(db, "einziger")
    _allow(db, regular_user, only, "server.view")

    for arguments in ({}, {"server_id": None}, {"server_id": "abc"}, {"server_id": 0},
                      {"server_id": True}, {"server_id": -5}):
        with pytest.raises(ai_action_errors.AiActionValidationError):
            ai_action_service.execute_read_tool(
                db, user=regular_user, tool_name="read_server_status",
                arguments=dict(arguments),
            )


def test_list_my_servers_shows_only_visible_servers(
    db: Session, regular_user: User
) -> None:
    """Die Einstiegsliste ist selbst eine Rechtegrenze."""
    mine = _server(db, "sichtbar")
    _server(db, "unsichtbar")
    _allow(db, regular_user, mine, "server.view")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="list_my_servers", arguments={},
    )

    assert [row["server_id"] for row in result["servers"]] == [mine.id]
    assert result["count"] == 1


def test_list_my_servers_needs_no_extra_right_but_grants_none_either(
    db: Session, regular_user: User
) -> None:
    """Ohne sichtbare Server ist die Liste leer statt ein Fehler.

    Eine Fehlermeldung waere hier schlechter: sie zwingt das Modell zu raten,
    statt ihm die ehrliche Antwort "du hast keine" zu geben.
    """
    _server(db, "nicht-meiner")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="list_my_servers", arguments={},
    )

    assert result["servers"] == []
    assert result["count"] == 0


def test_planning_tools_still_require_the_create_permission(
    db: Session, regular_user: User
) -> None:
    """Blueprintliste und Hostkapazitaet bleiben an `servers.create` gebunden."""
    role = Role(name="planer", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, regular_user, [role.id])

    for tool in ("list_blueprints", "read_node_capacity"):
        with pytest.raises(ai_action_errors.AiActionValidationError):
            ai_action_service.execute_read_tool(
                db, user=regular_user, tool_name=tool, arguments={},
            )
