"""Die KI leitet Blueprints ab und stellt Server um.

Der Anlass steht wortwoertlich im Betriebsprotokoll. Ein Benutzer bat darum, die
Minecraft-Einstellungen anzupassen. Die KI las Status, Kapazitaet, Ports und
Mods — und antwortete:

    "Die konkrete Minecraft-/Forge-Version ist in den aktuell auslesbaren
     Servereinstellungen nicht ersichtlich."

Sie hatte recht. Die Version steht nicht am Server, sondern in
`runtime.env["VERSION"]` des Blueprints, und die KI konnte Blueprints nicht
lesen. Aendern haette sie sie ohnehin nicht koennen: native Blueprints sind
schreibgeschuetzt, ein Community-Blueprint mit nativer ID wird abgewiesen, und
`game_type` stand nicht in `ServerUpdate`. **Die Spielversion eines bestehenden
Servers war ueberhaupt nicht aenderbar** — auch nicht von Hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiConversation, Role, RolePermission, Server, ServerPermission, User
from services import (
    ai_action_errors,
    ai_action_service,
    ai_proposal_service,
    blueprint_service,
)
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.role_service import set_user_roles


def _rechte(db: Session, user: User, *, global_keys: tuple[str, ...]) -> None:
    role = Role(name=f"bp-{user.id}-{uuid4().hex[:6]}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in global_keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def _server(db: Session, user: User, tmp_path: Path, game_type: str) -> Server:
    row = Server(
        name="MaickCraft Public",
        game_type=game_type,
        install_dir=str(tmp_path / "mc"),
        container_name=f"msm-mc-{uuid4().hex[:8]}",
        status="stopped",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    db.add(ServerPermission(user_id=user.id, server_id=row.id, permission_key="server.view"))
    db.commit()
    return row


def _conversation(db: Session, user: User) -> AiConversation:
    row = AiConversation(id=str(uuid4()), user_id=user.id, server_id=None, title="BP")
    db.add(row)
    db.commit()
    return row


def test_the_ai_can_finally_read_the_game_version(
    db: Session, regular_user: User
) -> None:
    """Die Frage aus dem Betrieb, jetzt beantwortbar.

    `read_blueprint` liefert `runtime.env` — bei Forge stehen dort `VERSION` und
    `TYPE`. Ohne dieses Werkzeug blieb der KI nur die Feststellung, die Version
    sei nicht ersichtlich.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use", "servers.create"))

    ergebnis = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="read_blueprint",
        arguments={"blueprint_id": "minecraft_forge"},
    )

    assert ergebnis["origin"] == "native"
    # Mitgeliefert heisst schreibgeschuetzt — die KI muss das wissen, sonst
    # schlaegt sie eine Aenderung vor, die nie durchgehen kann.
    assert ergebnis["editable"] is False
    env = ergebnis["blueprint"]["runtime"]["env"]
    assert "VERSION" in env
    assert env.get("TYPE") == "FORGE"


def test_deriving_leaves_the_native_template_untouched(db: Session) -> None:
    """Der Kern der Loesung: ableiten statt aendern.

    Ein Blueprint gilt fuer **alle** Server seines Typs. Wuerde die KI
    `minecraft_forge` aendern, bekaemen alle Forge-Server eine andere Version —
    und mitgeliefert ist er ohnehin schreibgeschuetzt.
    """
    nutzlast = blueprint_service.derived_payload(
        "minecraft_forge",
        new_id="minecraft_forge_1_20",
        changes={"runtime.env": {"VERSION": "1.20.1"}},
    )

    assert nutzlast["meta"]["id"] == "minecraft_forge_1_20"
    assert nutzlast["runtime"]["env"]["VERSION"] == "1.20.1"
    # Gemischt, nicht ersetzt: ohne TYPE=FORGE waere aus dem Forge-Server ein
    # Vanilla-Server geworden.
    assert nutzlast["runtime"]["env"]["TYPE"] == "FORGE"
    # Und die Vorlage steht unveraendert da.
    original = blueprint_service.blueprint_view("minecraft_forge")["blueprint"]
    assert original["runtime"]["env"]["VERSION"] == "LATEST"


def test_only_a_narrow_set_of_fields_can_be_changed(db: Session) -> None:
    """Eine Ableitung passt Werte an, sie baut den Blueprint nicht um.

    Portrollen oder die Installationsquelle zu aendern hiesse, einem Menschen
    eine Liste von Einzelaenderungen zur Bestaetigung vorzulegen, deren
    Zusammenwirken er nicht ueberblickt. Wer das braucht, laedt einen ganzen
    Blueprint hoch — dann sieht er das vollstaendige Ergebnis.
    """
    with pytest.raises(HTTPException) as fehler:
        blueprint_service.derived_payload(
            "minecraft_forge", new_id="boese", changes={"ports": []},
        )
    assert fehler.value.status_code == 400


def test_an_id_that_belongs_to_a_native_blueprint_is_refused(db: Session) -> None:
    """Native gewinnt immer — auch gegen eine Ableitung."""
    with pytest.raises(HTTPException) as fehler:
        blueprint_service.derived_payload(
            "minecraft_vanilla",
            new_id="minecraft_forge",
            changes={"runtime.env": {"VERSION": "1.20.1"}},
        )
    assert fehler.value.status_code == 409


def test_switching_a_server_needs_matching_port_roles(db: Session) -> None:
    """Die Ports haengen an ihren Rollennamen.

    Passt die Form nicht, bliebe ein belegter Port ohne Zuordnung oder ein
    verlangter ohne Vergabe. Der Vergleich ist bewusst auf die Form beschraenkt:
    ein Wechsel von Forge auf Vanilla ist eine legitime Absicht.
    """
    forge = blueprint_service.blueprint_view("minecraft_forge")["blueprint"]
    abgeleitet = blueprint_service.derived_payload(
        "minecraft_forge", new_id="mc_1_20",
        changes={"runtime.env": {"VERSION": "1.20.1"}},
    )
    assert blueprint_service.switch_incompatibility(forge, abgeleitet) is None

    fremd = dict(forge)
    fremd["ports"] = [{"name": "game", "protocol": "udp"}]
    grund = blueprint_service.switch_incompatibility(forge, fremd)
    assert grund is not None and "Ports" in grund


def test_a_running_server_cannot_be_switched(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Ein laufender Container haengt am alten Image.

    Ein Wechsel unter ihm weg fuehrt zu einem Zustand, den weder Panel noch
    Guardian einordnen koennen.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use", "blueprints.manage"))
    server = _server(db, regular_user, tmp_path, "minecraft_forge")
    server.status = "running"
    db.commit()
    conversation = _conversation(db, regular_user)

    with pytest.raises(ai_action_errors.AiActionValidationError, match="gestoppt"):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_blueprint_switch",
            arguments={
                "server_id": server.id,
                "blueprint_id": "minecraft_vanilla",
                "reason": "Andere Version.",
                "expected_effect": "Server laeuft auf 1.20.1.",
            },
            correlation_id=str(uuid4()),
        )


def test_switching_needs_the_blueprint_permission_not_just_server_access(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Wer Blueprints nicht pflegen darf, stellt auch keinen Server um."""
    _rechte(db, regular_user, global_keys=("ai.chat.use",))
    server = _server(db, regular_user, tmp_path, "minecraft_forge")
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.config.write",
    ))
    db.commit()
    conversation = _conversation(db, regular_user)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_blueprint_switch",
            arguments={
                "server_id": server.id,
                "blueprint_id": "minecraft_vanilla",
                "reason": "Andere Version.",
                "expected_effect": "Neue Version.",
            },
            correlation_id=str(uuid4()),
        )


def test_neither_blueprint_tool_ever_runs_autonomously(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Vorgabe des Betreibers fuer Blueprints: nur Vorschlaege, immer Bestaetigung.

    Ein Blueprint ist die Vorlage aller Server seines Typs; ein Fehler darin
    fuehrt zu Servern, die nicht starten. Das ist nichts, was im Hintergrund
    passieren darf, auch nicht bei erteilter Freigabe.
    """
    from services import ai_autonomy_service, ai_tool_registry

    _rechte(
        db, regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use", "blueprints.manage"),
    )
    server = _server(db, regular_user, tmp_path, "minecraft_forge")
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    for werkzeug in ("propose_blueprint_change", "propose_server_blueprint_switch"):
        assert werkzeug in ai_tool_registry.ALWAYS_CONFIRM_TOOLS
        assert not ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=server.id, tool_name=werkzeug,
        )


def test_the_whole_minecraft_case_end_to_end(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Der Betriebsfall von vorne bis hinten.

    Lesen, ableiten, bestaetigen, umstellen. Vorher war jeder dieser Schritte
    unmoeglich.
    """
    _rechte(
        db, regular_user,
        global_keys=("ai.chat.use", "blueprints.manage", "servers.create"),
    )
    server = _server(db, regular_user, tmp_path, "minecraft_forge")
    conversation = _conversation(db, regular_user)
    try:
        # 1. Die KI liest die Version.
        gelesen = ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="read_blueprint",
            arguments={"blueprint_id": server.game_type},
        )
        assert gelesen["blueprint"]["runtime"]["env"]["VERSION"] == "LATEST"

        # 2. Sie schlaegt eine Ableitung vor — und der Mensch sieht das
        #    Ergebnis, nicht nur die Absicht.
        vorschlag = ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_blueprint_change",
            arguments={
                "source_id": "minecraft_forge",
                "new_id": "minecraft_forge_1_20_1",
                "changes": {"runtime.env": {"VERSION": "1.20.1"}},
                "reason": "Der Benutzer will Forge 1.20.1 statt LATEST.",
                "expected_effect": "Ein eigener Blueprint mit fester Version liegt vor.",
            },
            correlation_id=str(uuid4()),
        )
        db.commit()
        vorschau = json.loads(vorschlag.preview_json)
        assert vorschau["env_before"]["VERSION"] == "LATEST"
        assert vorschau["env_after"]["VERSION"] == "1.20.1"
        assert vorschlag.requires_confirmation is True

        # 3. Bestaetigt -> der Blueprint existiert.
        _, token = ai_proposal_service.confirm_proposal(
            db, proposal_id=vorschlag.id, user=regular_user
        )
        ai_proposal_service.execute_proposal(
            db, proposal_id=vorschlag.id, user=regular_user, confirmation_token=token
        )
        neuer = blueprint_service.blueprint_view("minecraft_forge_1_20_1")
        assert neuer["origin"] == "community"
        assert neuer["blueprint"]["runtime"]["env"]["VERSION"] == "1.20.1"

        # 4. Und der Server laesst sich darauf umstellen.
        umstellung = ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_blueprint_switch",
            arguments={
                "server_id": server.id,
                "blueprint_id": "minecraft_forge_1_20_1",
                "reason": "Der Server soll auf 1.20.1 laufen.",
                "expected_effect": "Naechster Start nutzt Forge 1.20.1.",
            },
            correlation_id=str(uuid4()),
        )
        db.commit()
        _, token2 = ai_proposal_service.confirm_proposal(
            db, proposal_id=umstellung.id, user=regular_user
        )
        ai_proposal_service.execute_proposal(
            db, proposal_id=umstellung.id, user=regular_user,
            confirmation_token=token2,
        )

        db.refresh(server)
        assert server.game_type == "minecraft_forge_1_20_1"
        # Der Server bleibt gestoppt — ihn zu starten waere ein zweiter Vorgang,
        # den niemand bestaetigt hat.
        assert server.status == "stopped"
    finally:
        # Blueprints sind Dateien auf der Platte, keine Datenbankzeilen: die
        # `clean_db`-Fixture raeumt sie nicht mit weg.
        try:
            blueprint_service.delete_community_blueprint("minecraft_forge_1_20_1")
        except HTTPException:
            pass
