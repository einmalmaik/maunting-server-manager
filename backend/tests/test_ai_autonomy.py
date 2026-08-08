"""Autonomer Modus: was er entfernt und was er ausdruecklich nicht entfernt.

Zielpunkt 3.7. Autonomie ersetzt genau einen Schritt — die Bestaetigung durch
einen Menschen. Die Rechtepruefung, die Aktivpruefung des Benutzers, der
Server-Mutex und das Audit bleiben unveraendert. Diese Tests halten beide
Richtungen fest: dass er wirkt, und dass er nichts anderes aufhebt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiAutonomyGrant,
    AiConversation,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_action_service, ai_autonomy_service
from services.role_service import set_user_roles


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _setup(
    db: Session, user: User, *, global_keys: tuple[str, ...], server_keys: tuple[str, ...]
) -> tuple[Server, AiConversation]:
    role = Role(name=f"autonomy-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in global_keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])

    server = Server(
        name="Autonomy Server",
        game_type="dayz",
        install_dir="/tmp/autonomy-server",
        container_name="msm-autonomy",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    for key in server_keys:
        db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key=key))
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Autonomie"
    )
    db.add(conversation)
    db.commit()
    return server, conversation


def _backup_arguments(server_id: int) -> dict:
    return {
        "server_id": server_id,
        "reason": "Vor der Aenderung absichern.",
        "expected_effect": "Ein wiederherstellbarer Stand liegt vor.",
    }


def _propose(
    db: Session, user: User, conversation: AiConversation, server: Server
) -> AiActionProposal:
    proposal = ai_action_service.create_proposal(
        db,
        user=user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments=_backup_arguments(server.id),
        correlation_id=str(uuid4()),
    )
    db.commit()
    return proposal


def test_without_a_grant_every_proposal_stays_confirmable(
    db: Session, regular_user: User
) -> None:
    """Der Standardmodus bleibt der unterstuetzte — auch mit der Berechtigung."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is False
    assert proposal.requires_confirmation is True


def test_without_the_permission_a_grant_alone_does_nothing(
    db: Session, regular_user: User
) -> None:
    """Eine Freigabe kann keine fehlende Berechtigung ersetzen."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use",),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is False
    assert proposal.requires_confirmation is True


def test_with_permission_and_grant_the_proposal_is_autonomous(
    db: Session, regular_user: User
) -> None:
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is True
    assert proposal.requires_confirmation is False


def test_a_disabled_grant_does_not_count(db: Session, regular_user: User) -> None:
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=False,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is False


def test_a_server_grant_wins_over_the_panel_wide_one(
    db: Session, regular_user: User
) -> None:
    """Die spezifischere Angabe entscheidet — hier gegen die Autonomie."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=None, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=False,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    proposal = _propose(db, regular_user, conversation, server)

    assert proposal.autonomous is False


def test_the_hourly_budget_falls_back_to_confirmation(
    db: Session, regular_user: User
) -> None:
    """Ist das Budget erschoepft, scheitert nichts — es wird nur wieder gefragt."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=1, granted_by=regular_user.id,
    )
    db.commit()

    first = _propose(db, regular_user, conversation, server)
    second = _propose(db, regular_user, conversation, server)

    assert first.autonomous is True
    assert second.autonomous is False, "Das Stundenbudget muss greifen"
    assert second.requires_confirmation is True


def test_actions_older_than_an_hour_free_the_budget_again(
    db: Session, regular_user: User
) -> None:
    server, _ = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    del server
    now = datetime.now(timezone.utc)
    db.add(AiActionProposal(
        id=str(uuid4()),
        conversation_id=str(uuid4()),
        user_id=regular_user.id,
        server_id=None,
        tool_name="propose_backup",
        payload_encrypted="x",
        preview_json="{}",
        autonomous=True,
        correlation_id=str(uuid4()),
        created_at=now - timedelta(hours=2),
    ))
    db.commit()

    assert ai_autonomy_service.hourly_usage(db, user_id=regular_user.id) == 0


def test_always_confirm_tools_are_never_autonomous(
    db: Session, regular_user: User
) -> None:
    """Die Liste aus Zielbild 3.7 gilt unabhaengig von jeder Freigabe."""
    server, _ = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    for tool in sorted(ai_action_service.ALWAYS_CONFIRM_TOOLS):
        assert not ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=server.id, tool_name=tool
        ), f"{tool} darf niemals autonom laufen"


def test_autonomous_execution_still_rechecks_the_permission(
    db: Session, regular_user: User
) -> None:
    """Autonomie entfernt die Bestaetigung, nicht die Rechtepruefung."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()
    proposal = _propose(db, regular_user, conversation, server)

    db.query(ServerPermission).filter(
        ServerPermission.user_id == regular_user.id,
        ServerPermission.permission_key == "server.backups.create",
    ).delete()
    db.commit()

    with pytest.raises(ai_action_service.AiActionStateError) as excinfo:
        ai_action_service.execute_autonomously(
            db, proposal_id=proposal.id, user=regular_user
        )

    assert excinfo.value.code == "AI_ACTION_ACCESS_REVOKED"


def test_a_confirmable_proposal_can_not_be_executed_autonomously(
    db: Session, regular_user: User
) -> None:
    """Sonst waere der autonome Pfad eine Umgehung der Bestaetigungspflicht."""
    server, conversation = _setup(
        db,
        regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use"),
        server_keys=("server.view", "server.backups.create"),
    )
    proposal = _propose(db, regular_user, conversation, server)

    with pytest.raises(ai_action_service.AiActionStateError) as excinfo:
        ai_action_service.execute_autonomously(
            db, proposal_id=proposal.id, user=regular_user
        )

    assert excinfo.value.code == "AI_ACTION_NOT_AUTONOMOUS"


# ── Router ────────────────────────────────────────────────────────────────


def test_grant_endpoints_require_the_permission(client, user_cookies, user_csrf_token) -> None:
    listed = client.get("/api/ai/autonomy", cookies=user_cookies)
    written = client.put(
        "/api/ai/autonomy",
        json={"server_id": None, "enabled": True, "max_actions_per_hour": 5},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert listed.status_code == 403
    assert written.status_code == 403


def test_a_grant_for_a_foreign_server_is_not_possible(
    client, db: Session, regular_user: User, user_cookies: dict, user_csrf_token: str
) -> None:
    """Ein Grant auf einen unsichtbaren Server wuerde dessen Existenz verraten."""
    role = Role(name="autonomy-router", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.autonomous.use"))
    db.commit()
    set_user_roles(db, regular_user, [role.id])
    foreign = Server(
        name="Fremd", game_type="dayz", install_dir="/tmp/fremd", status="stopped"
    )
    db.add(foreign)
    db.commit()

    response = client.put(
        "/api/ai/autonomy",
        json={"server_id": foreign.id, "enabled": True, "max_actions_per_hour": 5},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 404
    assert db.query(AiAutonomyGrant).count() == 0


def test_grant_roundtrip_reports_the_used_budget(
    client, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    role = Role(name="autonomy-owner", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.autonomous.use"))
    db.commit()
    set_user_roles(db, owner_user, [role.id])

    created = client.put(
        "/api/ai/autonomy",
        json={"server_id": None, "enabled": True, "max_actions_per_hour": 7},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    listed = client.get("/api/ai/autonomy", cookies=owner_cookies)
    removed = client.delete(
        "/api/ai/autonomy", cookies=owner_cookies, headers=_csrf(owner_cookies)
    )
    empty = client.get("/api/ai/autonomy", cookies=owner_cookies)

    assert created.status_code == 200
    assert created.json()["max_actions_per_hour"] == 7
    assert created.json()["used_last_hour"] == 0
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert removed.status_code == 204
    assert empty.json() == []
