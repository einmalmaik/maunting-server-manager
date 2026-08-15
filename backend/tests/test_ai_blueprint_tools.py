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
from unittest.mock import patch

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


def _server(
    db: Session, user: User, tmp_path: Path, game_type: str,
    *, server_keys: tuple[str, ...] = ("server.view",),
) -> Server:
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
    for key in server_keys:
        db.add(ServerPermission(user_id=user.id, server_id=row.id, permission_key=key))
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


def test_the_refusal_does_not_reveal_whether_a_blueprint_exists(
    db: Session, regular_user: User
) -> None:
    """Ohne `blueprints.manage` sagt die Ablehnung nichts über den Bestand.

    `_blueprint_change_payload` liest die Quelle vom Datenträger und reicht die
    Meldung des Blueprint-Dienstes wörtlich durch. Liefe der Bau vor der
    Rechteprüfung, unterschiede ein Benutzer ohne das Recht vorhandene von
    erfundenen Blueprint-Kennungen an der Fehlermeldung.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use",))
    conversation = _conversation(db, regular_user)

    meldungen = []
    for quelle in ("minecraft_forge", "gibt-es-nicht-4711"):
        with pytest.raises(ai_action_errors.AiActionValidationError) as fehler:
            ai_proposal_service.create_proposal(
                db,
                user=regular_user,
                conversation=conversation,
                tool_name="propose_blueprint_change",
                arguments={
                    "source_id": quelle,
                    "new_id": "abgeleitet_4711",
                    "changes": {"runtime.env": {"VERSION": "1.20.1"}},
                    "reason": "Test.",
                    "expected_effect": "Test.",
                },
                correlation_id=str(uuid4()),
            )
        meldungen.append(str(fehler.value))

    assert meldungen[0] == meldungen[1] == "AI-Aktion ist nicht erlaubt"


def test_a_running_server_cannot_be_switched(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Ein laufender Container haengt am alten Image.

    Ein Wechsel unter ihm weg fuehrt zu einem Zustand, den weder Panel noch
    Guardian einordnen koennen.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use",))
    server = _server(
        db, regular_user, tmp_path, "minecraft_forge",
        server_keys=("server.view", "server.config.write"),
    )
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


def test_switching_needs_the_same_permission_as_the_panel_button(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Dasselbe Recht wie am Knopf "Spiel / Blueprint wechseln".

    Das ist der eigentliche Punkt. Zwei Entwuerfe davor hatten hier ein anderes
    Recht — erst das globale `blueprints.manage`, dann ein eigens erfundenes
    `server.blueprint.switch`. Beide erzeugten dieselbe Krankheit in
    verschiedener Form: **eine Handlung mit zwei Rechten**. Jemand haette sie
    ueber die KI gedurft und ueber das Panel nicht, oder umgekehrt.

    Das Recht, nach dem der Betreiber gefragt hat, gab es bereits
    (`routers/servers.py::switch_server_blueprint_endpoint`); es fehlte nur die
    Verbindung dorthin.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use", "blueprints.manage"))
    server = _server(
        db, regular_user, tmp_path, "minecraft_forge",
        # Viele Serverrechte — aber nicht das eine, das der Panel-Knopf verlangt.
        server_keys=(
            "server.view", "server.start", "server.stop",
            "server.files.write", "server.backups.create",
        ),
    )
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


def test_both_blueprint_tools_run_under_an_autonomy_grant(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Vorgabe des Betreibers: im autonomen Modus laeuft der Wechsel durch.

    Beide Werkzeuge sind umkehrbar. Eine Ableitung legt eine neue Datei an und
    laesst die Vorlage stehen; ein Wechsel legt **zwingend** ein Backup an,
    bevor er die erste Datei anfasst, und bricht ab, wenn das Backup scheitert
    (`switch_server_blueprint`). Der Weg zurueck ist Teil des Vorgangs.

    Die Freigabe ist die Entscheidung — nicht die Bestaetigung jedes Falls.
    """
    from services import ai_autonomy_service, ai_tool_registry

    _rechte(
        db, regular_user,
        global_keys=("ai.chat.use", "ai.autonomous.use", "blueprints.manage"),
    )
    server = _server(
        db, regular_user, tmp_path, "minecraft_forge",
        server_keys=("server.view", "server.config.write"),
    )
    ai_autonomy_service.set_grant(
        db, user=regular_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=regular_user.id,
    )
    db.commit()

    for werkzeug in ("propose_blueprint_change", "propose_server_blueprint_switch"):
        assert werkzeug not in ai_tool_registry.ALWAYS_CONFIRM_TOOLS
        assert ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=server.id, tool_name=werkzeug,
        ), f"{werkzeug} soll unter einer Freigabe durchlaufen"

    # Die Gegenprobe im selben Atemzug: was Daten vernichtet, fragt trotzdem.
    for werkzeug in ("propose_server_delete", "propose_backup_restore"):
        assert not ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=server.id, tool_name=werkzeug,
        ), f"{werkzeug} darf niemals autonom laufen"


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
    server = _server(
        db, regular_user, tmp_path, "minecraft_forge",
        server_keys=("server.view", "server.config.write"),
    )
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

        # 4. Und der Server laesst sich darauf umstellen — ueber **denselben**
        #    Aufruf, den der Panel-Knopf "Spiel / Blueprint wechseln" nimmt.
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

        # Die Vorschau sagt, was wirklich passiert. Der erste Entwurf nannte nur
        # die geaenderten Umgebungsvariablen und "Server bleibt gestoppt" — und
        # verschwieg damit, dass ein Wechsel das Serververzeichnis loescht.
        vorschau2 = json.loads(umstellung.preview_json)
        assert vorschau2["creates_backup"] is True
        assert vorschau2["wipes_server_files"] is True
        assert vorschau2["reinstalls"] is True
        assert vorschau2["irreversible"] is True

        _, token2 = ai_proposal_service.confirm_proposal(
            db, proposal_id=umstellung.id, user=regular_user
        )
        gesehen: dict = {}

        def _fake_switch(db_, server_, blueprint_id, *, user_id=None):
            gesehen.update(
                server_id=server_.id, blueprint_id=blueprint_id, user_id=user_id
            )
            server_.game_type = blueprint_id
            return {"message": "gewechselt", "new_blueprint": blueprint_id}

        with patch(
            "services.server_lifecycle_service.switch_server_blueprint", _fake_switch
        ):
            ai_proposal_service.execute_proposal(
                db, proposal_id=umstellung.id, user=regular_user,
                confirmation_token=token2,
            )

        # Der eigentliche Beweis: die KI hat den Panel-Weg genommen. Ein eigener
        # Weg wuerde das Pflicht-Backup, den Datei-Wipe, die Portneuvergabe und
        # die Neuinstallation ueberspringen — und einen Server hinterlassen,
        # dessen Datenbankeintrag nicht mehr zu seinen Dateien passt.
        assert gesehen == {
            "server_id": server.id,
            "blueprint_id": "minecraft_forge_1_20_1",
            "user_id": regular_user.id,
        }
        db.refresh(server)
        assert server.game_type == "minecraft_forge_1_20_1"
    finally:
        # Blueprints sind Dateien auf der Platte, keine Datenbankzeilen: die
        # `clean_db`-Fixture raeumt sie nicht mit weg.
        try:
            server.game_type = "minecraft_forge"
            db.commit()
            blueprint_service.delete_community_blueprint("minecraft_forge_1_20_1", db=db)
        except Exception:
            pass


def test_managing_blueprints_does_not_let_you_restructure_foreign_servers(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Der Fehler des ersten Entwurfs, als Testfall.

    Zuerst hing der Wechsel am **globalen** `blueprints.manage`. Das war in
    beide Richtungen falsch:

    - Wer Blueprints pflegen darf, haette jeden Server umbauen koennen, den er
      nur *sehen* darf — auch den eines Kunden.
    - Und der Besitzer seines eigenen Servers haette die Spielversion nicht
      aendern koennen, ohne panelweite Blueprint-Rechte zu bekommen.

    Vorlagen pflegen und einen Server zwischen ihnen wechseln sind zwei
    Aufgaben. Der Betreiber macht die eine, der Kunde die andere.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use", "blueprints.manage"))
    fremder = _server(
        db, regular_user, tmp_path, "minecraft_forge",
        server_keys=("server.view",),  # nur sehen
    )
    conversation = _conversation(db, regular_user)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_server_blueprint_switch",
            arguments={
                "server_id": fremder.id,
                "blueprint_id": "minecraft_vanilla",
                "reason": "Umbauen.",
                "expected_effect": "Anderer Blueprint.",
            },
            correlation_id=str(uuid4()),
        )
    db.refresh(fremder)
    assert fremder.game_type == "minecraft_forge"


def test_the_owner_can_switch_without_panel_wide_blueprint_rights(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Die andere Haelfte: der Kunde kommt an seine eigene Version.

    Ohne panelweite Blueprint-Rechte, nur mit `server.config.write` an
    seinem Server. Genau das Hoster-Modell — der Betreiber stellt die Vorlagen
    bereit, der Kunde waehlt zwischen ihnen.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use",))
    server = _server(
        db, regular_user, tmp_path, "minecraft_forge",
        server_keys=("server.view", "server.config.write"),
    )
    conversation = _conversation(db, regular_user)

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_server_blueprint_switch",
        arguments={
            "server_id": server.id,
            "blueprint_id": "minecraft_vanilla",
            "reason": "Der Kunde will Vanilla statt Forge.",
            "expected_effect": "Naechster Start nutzt Vanilla.",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    assert vorschlag.requires_confirmation is True
    assert vorschlag.autonomous is False


def test_a_server_on_a_deleted_blueprint_can_still_be_switched_away(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Genau der Fall, in dem man umstellen **will**.

    Wird eine Community-Vorlage geloescht, laufen die Server darauf ins Leere.
    Wuerde der Vorschlag daran scheitern, dass der **alte** Blueprint nicht mehr
    lesbar ist, waere die Falle zugeschnappt: der Server liesse sich ueber die
    KI nie mehr retten, obwohl der Panel-Knopf es kann — der prueft nur das Ziel.
    """
    _rechte(db, regular_user, global_keys=("ai.chat.use",))
    server = _server(
        db, regular_user, tmp_path, "gibt_es_nicht_mehr",
        server_keys=("server.view", "server.config.write"),
    )
    conversation = _conversation(db, regular_user)

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_server_blueprint_switch",
        arguments={
            "server_id": server.id,
            "blueprint_id": "minecraft_vanilla",
            "reason": "Die alte Vorlage gibt es nicht mehr.",
            "expected_effect": "Der Server laeuft wieder auf einer gueltigen Vorlage.",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    vorschau = json.loads(vorschlag.preview_json)
    assert vorschau["from_blueprint"] == "gibt_es_nicht_mehr"
    assert vorschau["to_blueprint"] == "minecraft_vanilla"
    # Unbekannt statt leer: ein leeres Objekt haette behauptet, die alte Vorlage
    # habe keine Umgebungsvariablen gehabt.
    assert vorschau["env_before"] is None


def test_propose_blueprint_delete_blocks_active_servers(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Loeschen eines Blueprints, der noch von einem Server genutzt wird, muss geblockt werden."""
    _rechte(db, regular_user, global_keys=("ai.chat.use", "blueprints.manage"))
    conversation = _conversation(db, regular_user)

    from services import blueprint_service

    # 1. Community-Blueprint anlegen
    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_blueprint_change",
        arguments={
            "source_id": "minecraft_forge",
            "new_id": "custom_active_bp",
            "changes": {"runtime.env": {"VERSION": "1.20.1"}},
            "reason": "Test-Blueprint fuer aktiven Server",
            "expected_effect": "Blueprint wird angelegt",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=vorschlag.id, user=regular_user)
    ai_proposal_service.execute_proposal(db, proposal_id=vorschlag.id, user=regular_user, confirmation_token=token)

    server = _server(db, regular_user, tmp_path, "custom_active_bp", server_keys=("server.view",))

    try:
        with pytest.raises(ai_proposal_service.AiActionValidationError) as exc:
            ai_proposal_service.create_proposal(
                db,
                user=regular_user,
                conversation=conversation,
                tool_name="propose_blueprint_delete",
                arguments={
                    "blueprint_id": "custom_active_bp",
                    "reason": "Test-Loeschung",
                    "expected_effect": "Wird geblockt",
                },
                correlation_id=str(uuid4()),
            )
        assert "verwendet" in str(exc.value)

        # Auch direkter Service-Aufruf wirft HTTPException 409
        with pytest.raises(HTTPException) as http_exc:
            blueprint_service.delete_community_blueprint("custom_active_bp", db=db)
        assert http_exc.value.status_code == 409
    finally:
        try:
            server.game_type = "minecraft_forge"
            db.commit()
            blueprint_service.delete_community_blueprint("custom_active_bp", db=db)
        except Exception:
            pass


def test_propose_blueprint_delete_succeeds_for_unused_blueprint(
    db: Session, regular_user: User
) -> None:
    """Loeschen eines ungenutzten Community-Blueprints klappt nach Bestaetigung."""
    _rechte(db, regular_user, global_keys=("ai.chat.use", "blueprints.manage"))
    conversation = _conversation(db, regular_user)

    from services import blueprint_service

    # 1. Community-Blueprint anlegen
    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_blueprint_change",
        arguments={
            "source_id": "minecraft_forge",
            "new_id": "unused_bp",
            "changes": {"runtime.env": {"VERSION": "1.20.1"}},
            "reason": "Test-Blueprint ungenutzt",
            "expected_effect": "Blueprint wird angelegt",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=vorschlag.id, user=regular_user)
    ai_proposal_service.execute_proposal(db, proposal_id=vorschlag.id, user=regular_user, confirmation_token=token)

    try:
        vorschlag2 = ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conversation,
            tool_name="propose_blueprint_delete",
            arguments={
                "blueprint_id": "unused_bp",
                "reason": "Wird nicht mehr gebraucht",
                "expected_effect": "Entfernt den Blueprint",
            },
            correlation_id=str(uuid4()),
        )
        db.commit()

        assert vorschlag2.status == "proposed"
        assert vorschlag2.requires_confirmation is True

        # Bestaetigen und ausfuehren
        _, token2 = ai_proposal_service.confirm_proposal(db, proposal_id=vorschlag2.id, user=regular_user)
        _, ergebnis = ai_proposal_service.execute_proposal(
            db, proposal_id=vorschlag2.id, user=regular_user, confirmation_token=token2
        )
        assert ergebnis["deleted"] is True
        assert blueprint_service.get_registry().get("unused_bp") is None
    finally:
        try:
            blueprint_service.delete_community_blueprint("unused_bp", db=db)
        except Exception:
            pass

