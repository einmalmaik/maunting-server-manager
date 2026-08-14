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

from models import AiActionProposal, AiConversation, Node, Role, RolePermission, Server, User
from services import ai_action_errors, ai_action_service, ai_proposal_service, ai_tool_registry
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


def test_the_single_tool_catalog_covers_panel_and_server_work(db: Session) -> None:
    """Es gibt genau einen Werkzeugsatz — mit und ohne Serverbezug.

    Frueher war der Katalog geteilt: der Panel-Chat sah nur globale Werkzeuge,
    der Server-Chat nur serverbezogene. Mit dem Einzelchat gibt es diese
    Trennung nicht mehr; stattdessen traegt jedes serverbezogene Werkzeug seine
    eigene `server_id`.
    """
    tools = ai_action_service.provider_tool_definitions()
    names = {item["function"]["name"] for item in tools}

    assert {"propose_server_create", "list_blueprints", "read_node_capacity"} <= names
    assert {"list_my_servers", "read_server_logs", "propose_backup"} <= names

    # Jedes serverbezogene Werkzeug verlangt die ID ausdruecklich. Ohne diese
    # Zusicherung koennte ein Modell sie weglassen und `_resolve_server`
    # muesste raten — genau das darf es nie.
    for item in tools:
        function = item["function"]
        if function["name"] in ai_tool_registry.GLOBAL_READ_TOOLS | ai_tool_registry.GLOBAL_WRITE_TOOLS:
            continue
        assert "server_id" in function["parameters"]["properties"], function["name"]
        assert "server_id" in function["parameters"]["required"], function["name"]


def test_creation_without_servers_create_is_rejected(
    db: Session, regular_user: User
) -> None:
    _role(db, regular_user, ("ai.chat.use",))
    conversation = _conversation(db, regular_user)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_create",
            arguments=_arguments(),
            correlation_id=str(uuid4()),
        )
    assert db.query(AiActionProposal).count() == 0


def test_the_refusal_does_not_reveal_whether_a_node_exists(
    db: Session, regular_user: User
) -> None:
    """Ohne `servers.create` sagt die Ablehnung nichts über den Bestand.

    Der Payload-Bau schlägt die `node_id` nach und wirft "Unbekannte Node",
    wenn es sie nicht gibt. Liefe er vor der Rechteprüfung, wäre die Ablehnung
    selbst eine Auskunft: ein Benutzer ohne `servers.create` könnte Node-
    Kennungen abzählen, indem er die beiden Meldungen unterscheidet. Über
    `read_node_capacity` bekäme er sie nicht — das Werkzeug hängt an genau
    diesem Recht.
    """
    node = Node(
        name="node-1", host="http://127.0.0.1", auth_token_enc="enc", status="online"
    )
    db.add(node)
    db.commit()
    db.refresh(node)

    _role(db, regular_user, ("ai.chat.use",))
    conversation = _conversation(db, regular_user)

    meldungen = []
    for node_id in (node.id, node.id + 10_000):
        with pytest.raises(ai_action_errors.AiActionValidationError) as fehler:
            ai_proposal_service.create_proposal(
                db,
                user=regular_user,
                conversation=conversation,
                tool_name="propose_server_create",
                arguments=_arguments(node_id=node_id),
                correlation_id=str(uuid4()),
            )
        meldungen.append(str(fehler.value))

    assert meldungen[0] == meldungen[1] == "AI-Aktion ist nicht erlaubt"
    assert db.query(AiActionProposal).count() == 0


def test_creation_proposal_needs_a_reason_and_an_expected_effect(
    db: Session, regular_user: User
) -> None:
    """Zielpunkt 3.6: ein Vorschlag ohne Begruendung ist keine Vorschau."""
    _role(db, regular_user, ("ai.chat.use", "servers.create"))
    conversation = _conversation(db, regular_user)
    arguments = _arguments()
    del arguments["reason"]

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
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

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
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

    proposal = ai_proposal_service.create_proposal(
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
    proposal = ai_proposal_service.create_proposal(
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

    # Der Ersatz legt eine **echte** Serverzeile an, statt eine Kennung zu
    # erfinden. Seit die Tests Fremdschluessel pruefen, wuerde das Nachtragen
    # einer erfundenen ID an `ai_action_proposals.server_id` sofort auffliegen —
    # und zwar zu Recht: im Betrieb gibt `provision_server` immer einen Server
    # zurueck, den es auch gibt.
    angelegt = Server(
        name="ai-erstellt", game_type="dayz", install_dir="/tmp/ai-erstellt",
        status="installing", container_name="msm-ai-erstellt",
    )
    db.add(angelegt)
    db.commit()
    db.refresh(angelegt)
    neue_server_id = angelegt.id

    @dataclass
    class _Task:
        id: str = "task-4711"

    @dataclass
    class _Result:
        server: Server
        task: _Task

    def fake_provision(db_arg, request, actor, *, idempotency_key=None, retry_of_id=None):
        captured["request"] = request
        captured["actor_origin"] = actor.origin
        captured["actor_user"] = actor.user.id
        captured["idempotency_key"] = idempotency_key
        return _Result(server=angelegt, task=_Task())

    monkeypatch.setattr(
        "services.server_provisioning_service.provision_server", fake_provision
    )

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal_id, user=regular_user
    )
    executed, result = ai_proposal_service.execute_proposal(
        db, proposal_id=proposal_id, user=regular_user, confirmation_token=token
    )

    assert captured["request"].game_type == "dayz"
    assert captured["request"].ram_limit_mb == 8192
    assert captured["actor_origin"] == "ai"
    assert captured["actor_user"] == regular_user.id
    assert captured["idempotency_key"] == f"ai-{proposal_id}"
    assert result["server_id"] == neue_server_id
    # Nach der Ausfuehrung traegt der Vorschlag seinen Server.
    assert executed.server_id == neue_server_id
    assert executed.status == "succeeded"


def test_a_revoked_permission_blocks_execution_even_after_confirmation(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Rechtepruefung laeuft erneut unmittelbar vor der Ausfuehrung.

    Gemeldet wird `AI_ACTION_ACCESS_REVOKED` — hier stand vorher, es sei
    `AI_ACTION_NOT_FOUND`, und der Test liess beides gelten. Das war ehrlich
    beschrieben, aber es war eine Auskunft, die in die Irre fuehrt: der
    Vorschlag ist da, dem Benutzer fehlt nur das Recht. Wer daraufhin sucht,
    sucht nach einer verschwundenen Zeile statt nach einem entzogenen Recht.

    Entscheidend bleibt daneben, dass die Provisionierung nicht erreicht wird.
    """
    _role(db, regular_user, ("ai.chat.use", "servers.create"))
    conversation = _conversation(db, regular_user)
    proposal = ai_proposal_service.create_proposal(
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

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=regular_user
    )
    # Recht entziehen, nachdem bestaetigt wurde.
    db.query(RolePermission).filter(
        RolePermission.permission_key == "servers.create"
    ).delete()
    db.commit()

    with pytest.raises(ai_action_errors.AiActionStateError) as excinfo:
        ai_proposal_service.execute_proposal(
            db, proposal_id=proposal.id, user=regular_user, confirmation_token=token
        )

    assert excinfo.value.code == "AI_ACTION_ACCESS_REVOKED"
    assert called["count"] == 0, "Die Provisionierung darf ohne Recht nicht laufen"
