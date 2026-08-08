"""Mod-Werkzeuge der KI: lesen, Updates erkennen, Installation vorschlagen.

Zielpunkt 3.1 nennt Modverwaltung ausdruecklich als KI-Aufgabe. Zielpunkt 16
zieht die Grenze: externe Inhalte duerfen nicht ungeprueft in Serververzeichnisse
geschrieben werden. Beides zusammen bedeutet, dass die KI den vorhandenen
Installationspfad benutzt statt einen eigenen zu bekommen.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    Mod,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_action_service
from services.role_service import set_user_roles


def _setup(db: Session, user: User, *, server_keys: tuple[str, ...]) -> tuple[Server, AiConversation]:
    role = Role(name=f"mods-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, user, [role.id])

    server = Server(
        name="Mod Server",
        game_type="dayz",
        install_dir="/tmp/mod-server",
        container_name="msm-mod-server",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    for key in server_keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=server.id, title="Mods"
    )
    db.add(conversation)
    db.commit()
    return server, conversation


def _install_arguments(**overrides) -> dict:
    values = {
        "workshop_id": "1559212036",
        "action": "install",
        "reason": "Der Server startet ohne diese Abhaengigkeit nicht.",
        "expected_effect": "Nach dem Neustart laedt der Server die Mod.",
    }
    values.update(overrides)
    return values


def test_reading_mods_requires_the_mod_read_permission(
    db: Session, regular_user: User
) -> None:
    _, conversation = _setup(db, regular_user, server_keys=("server.view",))

    with pytest.raises(ai_action_service.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="read_server_mods",
            arguments={},
        )


def test_reading_mods_returns_status_without_secrets(
    db: Session, regular_user: User
) -> None:
    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read")
    )
    db.add(Mod(
        server_id=server.id,
        workshop_id="1559212036",
        name="CF",
        enabled=True,
        install_status="installed",
        update_status="outdated",
        update_reason="remote_newer",
        load_order=1,
    ))
    db.commit()

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="read_server_mods",
        arguments={},
    )

    assert result["mods"][0]["workshop_id"] == "1559212036"
    assert result["mods"][0]["update_status"] == "outdated"
    assert result["mods"][0]["update_reason"] == "remote_newer"


def test_mod_install_without_write_permission_is_rejected(
    db: Session, regular_user: User
) -> None:
    _, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read")
    )

    with pytest.raises(ai_action_service.AiActionValidationError):
        ai_action_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_mod_install",
            arguments=_install_arguments(),
            correlation_id=str(uuid4()),
        )
    assert db.query(AiActionProposal).count() == 0


def test_a_non_numeric_workshop_id_is_rejected(
    db: Session, regular_user: User
) -> None:
    """Die Kennung geht in einen Downloadpfad — sie muss rein numerisch sein."""
    _, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )

    for bad in ("../../etc/passwd", "12a", "", "1" * 21):
        with pytest.raises(ai_action_service.AiActionValidationError):
            ai_action_service.create_proposal(
                db,
                user=regular_user,
                conversation=conversation,
                tool_name="propose_mod_install",
                arguments=_install_arguments(workshop_id=bad),
                correlation_id=str(uuid4()),
            )


def test_mod_install_proposal_needs_confirmation_and_shows_a_preview(
    db: Session, regular_user: User
) -> None:
    import json

    _, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )

    proposal = ai_action_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(),
        correlation_id=str(uuid4()),
    )
    db.commit()

    assert proposal.requires_confirmation is True
    preview = json.loads(proposal.preview_json)
    assert preview["operation"] == "mod_install"
    assert preview["workshop_id"] == "1559212036"
    assert preview["already_installed"] is False
    # Eine Mod wirkt erst nach einem Neustart — das gehoert in die Vorschau.
    assert preview["restart_required"] is True


def test_execution_uses_the_existing_install_path(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zielpunkt 16: kein eigener Downloadbereich fuer die KI.

    Ausgefuehrt wird `install_mod_bg` — derselbe Code mit demselben
    Install-Lock, den auch der Mod-Tab des Panels ausloest.
    """
    started: list[tuple] = []

    class _Thread:
        def __init__(self, *, target, args, daemon, name):
            started.append((target, args))

        def start(self) -> None:
            return None

    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )
    proposal = ai_action_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(),
        correlation_id=str(uuid4()),
    )
    db.commit()
    monkeypatch.setattr("threading.Thread", _Thread)

    _, token = ai_action_service.confirm_proposal(
        db, proposal_id=proposal.id, user=regular_user
    )
    executed, result = ai_action_service.execute_proposal(
        db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
    )

    assert len(started) == 1
    target, args = started[0]
    from routers.mods import install_mod_bg

    assert target is install_mod_bg
    assert args == (server.id, "1559212036", "install")
    assert result["installation"] == "running"
    assert executed.status == "succeeded"
    # Die Mod-Zeile existiert und traegt den laufenden Vorgang.
    assert db.query(Mod).filter(Mod.server_id == server.id).count() == 1


def test_a_running_installation_blocks_a_second_one(
    db: Session, regular_user: User
) -> None:
    from services.mod_install_status_service import INSTALL_RUNNING

    server, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read", "server.mods.write")
    )
    db.add(Mod(
        server_id=server.id,
        workshop_id="1559212036",
        install_status=INSTALL_RUNNING,
    ))
    db.commit()
    proposal = ai_action_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_mod_install",
        arguments=_install_arguments(action="update"),
        correlation_id=str(uuid4()),
    )
    db.commit()

    _, token = ai_action_service.confirm_proposal(
        db, proposal_id=proposal.id, user=regular_user
    )
    with pytest.raises(ai_action_service.AiActionStateError) as excinfo:
        ai_action_service.execute_proposal(
            db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
        )

    assert excinfo.value.code == "AI_ACTION_SERVER_BUSY"


def test_workshop_search_reports_a_missing_api_key_honestly(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine leere Trefferliste waere hier eine falsche Aussage ueber den Workshop."""
    _, conversation = _setup(
        db, regular_user, server_keys=("server.view", "server.mods.read")
    )
    monkeypatch.setattr("services.steam_api_key_service.resolve_key", lambda: None)

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="search_workshop_mods",
        arguments={"query": "cf"},
    )

    assert result["available"] is False
    assert result["reason"] in {"steam_api_key_missing", "workshop_id_missing", "mods_not_supported"}
    assert "results" not in result
