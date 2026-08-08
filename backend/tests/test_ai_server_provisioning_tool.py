"""Die KI erstellt Server ausschliesslich ueber den gemeinsamen Pfad.

Zielpunkt 3.1 verlangt, dass die KI beim Erstellen und Einrichten eines Servers
hilft. Zielpunkt 10 verlangt, dass es dafuer **keinen zweiten Weg** gibt: Panel,
KI und Shop-Integration muessen dieselbe Fachlogik verwenden, sonst umgeht einer
von ihnen Blueprintpruefung, Kapazitaet, Portvergabe oder Rechte.

Diese Tests halten beides fest — die Faehigkeit und die Grenze.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiActionProposal, AiConversation, Role, RolePermission, User
from services import ai_action_service
from services.role_service import set_user_roles


def _conversation(db: Session, user: User) -> AiConversation:
    """Ein Panel-Chat ohne Serverbezug — genau der Ort fuer eine Erstellung."""
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Neuer Server"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _role(db: Session, user: User, keys: tuple[str, ...]) -> None:
    role = Role(name=f"provision-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])


def _arguments(**overrides) -> dict:
    values = {
        "name": "Mein Minecraft",
        "game_type": "dayz",
        "ram_limit_mb": 8192,
        "cpu_limit_percent": 200,
        "disk_limit_gb": 40,
        "reason": "Der Benutzer moechte einen neuen Server.",
        "expected_effect": "Ein installierter, gestoppter Server erscheint in der Liste.",
    }
    values.update(overrides)
    return values


def test_the_panel_chat_offers_the_creation_tool(db: Session) -> None:
    """Ohne Serverbezug muss es Werkzeuge geben — vorher gab es dort gar keine."""
    names = {
        item["function"]["name"]
        for item in ai_action_service.provider_tool_definitions(server_scoped=False)
    }

    assert "propose_server_create" in names
    assert "list_blueprints" in names
    assert "read_node_capacity" in names
    # Serverbezogene Werkzeuge haben im Panel-Chat nichts zu suchen.
    assert "read_server_logs" not in names


def test_creation_without_servers_create_is_rejected(
    db: Session, regular_user: User
) -> None:
    _role(db, regular_user, ("ai.chat.use",))
    conversation = _conversation(db, regular_user)

    with pytest.raises(ai_action_service.AiActionValidationError):
        ai_action_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_create",
            arguments=_arguments(),
            correlation_id=str(uuid4()),
        )
    assert db.query(AiActionProposal).count() == 0


def test_creation_proposal_needs_a_reason_and_an_expected_effect(
    db: Session, regular_user: User
) -> None:
    """Zielpunkt 3.6: ein Vorschlag ohne Begruendung ist keine Vorschau."""
    _role(db, regular_user, ("ai.chat.use", "servers.create"))
    conversation = _conversation(db, regular_user)
    arguments = _arguments()
    del arguments["reason"]

    with pytest.raises(ai_action_service.AiActionValidationError):
        ai_action_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_create",
            arguments=arguments,
            correlation_id=str(uuid4()),
        )


def test_unknown_game_type_is_rejected_before_a_proposal_exists(
    db: Session, regular_user: User
) -> None:
    _role(db, regular_user, ("ai.chat.use", "servers.create"))
    conversation = _conversation(db, regular_user)

    with pytest.raises(ai_action_service.AiActionValidationError):
        ai_action_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_create",
            arguments=_arguments(game_type="does-not-exist"),
            correlation_id=str(uuid4()),
        )
    assert db.query(AiActionProposal).count() == 0


def test_a_creation_proposal_carries_a_preview_and_stays_unconfirmed(
    db: Session, regular_user: User
) -> None:
    import json

    _role(db, regular_user, ("ai.chat.use", "servers.create"))
    conversation = _conversation(db, regular_user)

    proposal = ai_action_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_server_create",
        arguments=_arguments(),
        correlation_id=str(uuid4()),
    )
    db.commit()

    assert proposal.server_id is None, "Der Server existiert zum Vorschlagszeitpunkt nicht"
    assert proposal.requires_confirmation is True
    assert proposal.autonomous is False
    preview = json.loads(proposal.preview_json)
    assert preview["operation"] == "create_server"
    assert preview["ram_limit_mb"] == 8192
    # Ports vergibt MSM. Eine Vorschau, die konkrete Ports nennt, waere eine
    # Zusage, die erst die Portvergabe einloesen kann.
    assert preview["ports"] == "auto"
    assert preview["reason"]
    assert preview["expected_effect"]


def test_execution_goes_through_the_shared_provisioning_service(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Kern von Zielpunkt 10: kein zweiter Erstellungsweg.

    Der Test faengt `provision_server` ab und prueft, dass die Ausfuehrung
    genau dort landet — mit einem Idempotency-Key, der an den Vorschlag
    gebunden ist, damit ein zweiter Versuch keinen zweiten Server erzeugt.
    """
    from dataclasses import dataclass

    _role(db, regular_user, ("ai.chat.use", "servers.create"))
    conversation = _conversation(db, regular_user)
    proposal = ai_action_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_server_create",
        arguments=_arguments(),
        correlation_id=str(uuid4()),
    )
    db.commit()
    proposal_id = proposal.id

    captured: dict = {}

    @dataclass
    class _Server:
        id: int = 4711
        status: str = "installing"

    @dataclass
    class _Task:
        id: str = "task-4711"

    @dataclass
    class _Result:
        server: _Server
        task: _Task

    def fake_provision(db_arg, request, actor, *, idempotency_key=None, retry_of_id=None):
        captured["request"] = request
        captured["actor_origin"] = actor.origin
        captured["actor_user"] = actor.user.id
        captured["idempotency_key"] = idempotency_key
        return _Result(server=_Server(), task=_Task())

    monkeypatch.setattr(
        "services.server_provisioning_service.provision_server", fake_provision
    )

    _, token = ai_action_service.confirm_proposal(
        db, proposal_id=proposal_id, user=regular_user
    )
    executed, result = ai_action_service.execute_proposal(
        db, proposal_id=proposal_id, user=regular_user, confirmation_token=token
    )

    assert captured["request"].game_type == "dayz"
    assert captured["request"].ram_limit_mb == 8192
    assert captured["actor_origin"] == "ai"
    assert captured["actor_user"] == regular_user.id
    assert captured["idempotency_key"] == f"ai-{proposal_id}"
    assert result["server_id"] == 4711
    # Nach der Ausfuehrung traegt der Vorschlag seinen Server.
    assert executed.server_id == 4711
    assert executed.status == "succeeded"


def test_a_revoked_permission_blocks_execution_even_after_confirmation(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Rechtepruefung laeuft erneut unmittelbar vor der Ausfuehrung.

    Gemeldet wird `AI_ACTION_NOT_FOUND` und nicht `AI_ACTION_ACCESS_REVOKED`:
    bei einem Erstellungsvorschlag ist `servers.create` gleichzeitig das
    Sichtbarkeits- und das Ausfuehrungsrecht, weshalb der Vorschlag ohne dieses
    Recht schon nicht mehr adressierbar ist. Entscheidend ist nicht der Code,
    sondern dass die Provisionierung nicht erreicht wird.
    """
    _role(db, regular_user, ("ai.chat.use", "servers.create"))
    conversation = _conversation(db, regular_user)
    proposal = ai_action_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_server_create",
        arguments=_arguments(),
        correlation_id=str(uuid4()),
    )
    db.commit()

    called = {"count": 0}

    def fake_provision(*_args, **_kwargs):
        called["count"] += 1
        raise AssertionError("Darf ohne Recht nicht erreicht werden")

    monkeypatch.setattr(
        "services.server_provisioning_service.provision_server", fake_provision
    )

    _, token = ai_action_service.confirm_proposal(
        db, proposal_id=proposal.id, user=regular_user
    )
    # Recht entziehen, nachdem bestaetigt wurde.
    db.query(RolePermission).filter(
        RolePermission.permission_key == "servers.create"
    ).delete()
    db.commit()

    with pytest.raises(ai_action_service.AiActionStateError) as excinfo:
        ai_action_service.execute_proposal(
            db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
        )

    assert excinfo.value.code in {"AI_ACTION_NOT_FOUND", "AI_ACTION_ACCESS_REVOKED"}
    assert called["count"] == 0, "Die Provisionierung darf ohne Recht nicht laufen"
