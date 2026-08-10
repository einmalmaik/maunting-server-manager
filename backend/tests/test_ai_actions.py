"""Security-Invarianten fuer bestaetigungspflichtige AI-Aktionen."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    AuditLog,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_action_errors, ai_action_service, ai_proposal_service
from services.dis_client import DisClient
from services.file_edit_service import content_revision
from services.role_service import set_user_roles


def _conversation(db: Session, user: User, server: Server) -> AiConversation:
    row = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=server.id, title="Actions"
    )
    db.add(row)
    db.flush()
    return row


def _server(db: Session, owner_user: User, tmp_path: Path) -> Server:
    install_dir = tmp_path / "ai-server"
    install_dir.mkdir()
    row = Server(
        name="AI Action Server",
        game_type="dayz",
        install_dir=str(install_dir),
        container_name=f"msm-ai-{uuid4().hex[:8]}",
        status="stopped",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_config_proposal_encrypts_payload_and_keeps_audit_content_free(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, owner_user, tmp_path)
    config = Path(server.install_dir) / "server.cfg"
    config.write_text("port=2302\nmaxPlayers=40\n", encoding="utf-8")
    conversation = _conversation(db, owner_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_config_update",
        arguments={
            "server_id": server.id,"path": "server.cfg",
            "content": "port=2402\nmaxPlayers=40\n",
            "expected_revision": content_revision(config.read_bytes()),
            "reason": "Testbegruendung",
            "expected_effect": "Testwirkung",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    decrypted = json.loads(DisClient.decrypt(
        proposal.payload_encrypted,
        aad=f"msm:ai:action-proposal:v1:{proposal.id}",
    ))
    assert decrypted["content"] == "port=2402\nmaxPlayers=40\n"
    audit = db.query(AuditLog).filter(AuditLog.action == "ai.action.proposed").one()
    assert "port=2402" not in (audit.details or "")


def test_config_with_credentials_is_never_overwritten_by_ai(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Eine Config mit erkennbaren Zugangsdaten bleibt fuer die KI schreibgeschuetzt.

    Die KI sieht solche Dateien nur redigiert. Ein Vorschlag auf Basis dieser
    Sicht wuerde den echten Wert durch den Platzhalter ersetzen oder die Zeile
    ganz entfernen. Beides muss vor dem Schreiben scheitern.
    """
    server = _server(db, owner_user, tmp_path)
    config = Path(server.install_dir) / "server.cfg"
    config.write_text("api_key=old-secret-value\nport=2302\n", encoding="utf-8")
    conversation = _conversation(db, owner_user, server)

    try:
        ai_proposal_service.create_proposal(
            db,
            user=owner_user,
            conversation=conversation,
            tool_name="propose_config_update",
            arguments={
                "server_id": server.id,"path": "server.cfg",
                "content": "port=2402\n",
                "expected_revision": content_revision(config.read_bytes()),
                "reason": "Testbegruendung",
                "expected_effect": "Testwirkung",
            },
            correlation_id=str(uuid4()),
        )
    except ai_action_errors.AiActionValidationError:
        pass
    else:
        raise AssertionError("Config mit Zugangsdaten wurde zum Ueberschreiben akzeptiert")

    db.rollback()
    assert db.query(AiActionProposal).count() == 0
    assert config.read_text(encoding="utf-8") == "api_key=old-secret-value\nport=2302\n"


def test_read_config_separates_full_replacement_from_partial_change(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Eine unvollstaendige Sicht sperrt die Vollersetzung — nicht die Datei.

    Frueher waren das eine Frage: keine vollstaendige Sicht, keine Revision,
    kein Schreiben. Damit war jede Datei ueber 24.000 Zeichen fuer die KI
    dauerhaft nur lesbar — und genau das war der Betriebsfall, aus dem diese
    Aenderung entstand.

    Es sind zwei Fragen. `editable` heisst "darf ganz ersetzt werden" und
    verlangt weiterhin die vollstaendige, unredigierte Sicht. `patchable` heisst
    "darf an einer Stelle geaendert werden" und verlangt nur, dass es Text ist:
    was nicht durchlaeuft, kann auch nicht kaputtgehen.

    Die Revision ist dadurch wieder das, was sie ist — die Kennung dieses
    Dateistands. Die Vollersetzung haengt nicht mehr an ihrem Fehlen, sondern an
    einer Messung der Datei selbst (siehe
    `test_oversized_file_is_rejected_for_full_replacement`).
    """
    server = _server(db, owner_user, tmp_path)

    secret_config = Path(server.install_dir) / "secret.cfg"
    secret_config.write_text("api_key=super-secret-value\nport=2302\n", encoding="utf-8")
    redacted_view = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="read_config",
        arguments={"server_id": server.id, "path": "secret.cfg"},
    )
    assert redacted_view["redacted"] is True
    assert redacted_view["editable"] is False
    assert redacted_view["patchable"] is True
    assert redacted_view["revision"] == content_revision(secret_config.read_bytes())
    assert "super-secret-value" not in redacted_view["content"]

    big_config = Path(server.install_dir) / "big.cfg"
    big_config.write_text("x" * (ai_action_service.MAX_READ_CONFIG_CHARS + 10), encoding="utf-8")
    truncated_view = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="read_config",
        arguments={"server_id": server.id, "path": "big.cfg"},
    )
    assert truncated_view["truncated"] is True
    assert truncated_view["editable"] is False
    assert truncated_view["patchable"] is True
    assert len(truncated_view["content"]) == ai_action_service.MAX_READ_CONFIG_CHARS

    binary = Path(server.install_dir) / "world.bin"
    binary.write_bytes(b"\x00\x01\x02" * 100)
    binary_view = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="read_config",
        arguments={"server_id": server.id, "path": "world.bin"},
    )
    # Eine Binaerdatei bleibt auf beiden Wegen gesperrt, und ihr Inhalt geht gar
    # nicht erst in den Kontext des Modells.
    assert binary_view["binary"] is True
    assert binary_view["editable"] is False
    assert binary_view["patchable"] is False
    assert binary_view["revision"] is None
    assert binary_view["content"] == ""

    plain_config = Path(server.install_dir) / "plain.cfg"
    plain_config.write_text("port=2302\n", encoding="utf-8")
    full_view = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="read_config",
        arguments={"server_id": server.id, "path": "plain.cfg"},
    )
    assert full_view["editable"] is True
    assert full_view["patchable"] is True
    assert full_view["revision"] == content_revision(plain_config.read_bytes())


def test_read_config_window_reaches_beyond_the_first_screen(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Ohne Fenster kaeme aus einer grossen Datei immer nur derselbe Anfang."""
    server = _server(db, owner_user, tmp_path)
    gross = Path(server.install_dir) / "buffs.xml"
    # Ausdruecklich ohne Zeilenendenuebersetzung: Serverdateien liegen auf
    # Linux, und der Test soll nicht davon abhaengen, worauf er laeuft.
    gross.write_text(
        "".join(f"<zeile nr=\"{i}\"/>\n" for i in range(1, 5001)),
        encoding="utf-8",
        newline="\n",
    )

    fenster = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="read_config",
        arguments={"server_id": server.id, "path": "buffs.xml", "offset": 4200, "limit": 3},
    )
    assert fenster["offset"] == 4200
    assert fenster["lines"] == 3
    assert fenster["total_lines"] == 5000
    assert fenster["content"] == (
        '<zeile nr="4200"/>\n<zeile nr="4201"/>\n<zeile nr="4202"/>\n'
    )
    # Ein Fenster ist per Definition nicht die ganze Datei — also keine
    # Vollersetzung, aber sehr wohl eine Teilaenderung.
    assert fenster["truncated"] is True
    assert fenster["editable"] is False
    assert fenster["patchable"] is True

    # Hinter dem Ende ist kein Fehler, sondern ein leeres Fenster: `total_lines`
    # sagt dem Modell, dass es zu weit gesprungen ist.
    dahinter = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="read_config",
        arguments={"server_id": server.id, "path": "buffs.xml", "offset": 9000},
    )
    assert dahinter["content"] == ""
    assert dahinter["total_lines"] == 5000


def test_oversized_file_is_rejected_for_full_replacement(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Die Vollersetzung misst die Datei — nicht das, was das Modell vorzeigt.

    Frueher lag der Schutz darin, dass `read_config` fuer eine gekuerzte Sicht
    keine Revision ausgab. Diese Absicherung glaubte dem Modell, dass es die
    Datei gesehen hat, sobald es eine gueltige Revision nennen konnte. Jetzt
    wird die Datei selbst gemessen: eine gueltige Revision hilft nicht.
    """
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    gross = Path(server.install_dir) / "big.cfg"
    gross.write_text("x" * (ai_action_service.MAX_READ_CONFIG_CHARS + 10), encoding="utf-8")

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=owner_user,
            conversation=conversation,
            tool_name="propose_config_update",
            arguments={
                "server_id": server.id,
                "path": "big.cfg",
                "content": "kurz\n",
                "expected_revision": content_revision(gross.read_bytes()),
                "reason": "Testbegruendung",
                "expected_effect": "Testwirkung",
            },
            correlation_id=str(uuid4()),
        )

    db.rollback()
    assert db.query(AiActionProposal).count() == 0
    assert len(gross.read_text(encoding="utf-8")) == ai_action_service.MAX_READ_CONFIG_CHARS + 10


# ── Teilaenderung ─────────────────────────────────────────────────────────
#
# Der Anlass in einem Satz: eine `buffs.xml` von rund einem Megabyte, ein
# Benutzer der zwei Werte darin geaendert haben will, und eine KI die ihm
# erklaert, er muesse das im Dateimanager tun. Die folgenden Tests halten fest,
# dass genau das jetzt geht — und dass es dabei nichts kaputt macht.


def _grosse_konfiguration(server: Server) -> Path:
    """Eine Datei jenseits jeder Vollersetzung, mit einer eindeutigen Stelle."""
    ziel = Path(server.install_dir) / "buffs.xml"
    fueller = "".join(f'  <buff name="fueller{i}" value="1"/>\n' for i in range(4000))
    ziel.write_text(
        "<buffs>\n"
        f"{fueller}"
        '  <buff name="staminaLoss" value="1.0"/>\n'
        '  <buff name="staminaRegen" value="1.0"/>\n'
        "</buffs>\n",
        encoding="utf-8",
    )
    assert len(ziel.read_text(encoding="utf-8")) > ai_action_service.MAX_READ_CONFIG_CHARS
    return ziel


def _patch(db: Session, user: User, conversation: AiConversation, server: Server, **rest):
    return ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=conversation,
        tool_name="propose_config_patch",
        arguments={
            "server_id": server.id,
            "reason": "Testbegruendung",
            "expected_effect": "Testwirkung",
            **rest,
        },
        correlation_id=str(uuid4()),
    )


def test_patch_changes_only_the_matched_passage_of_a_large_file(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Der Fall, an dem die Vollersetzung scheitert — und die Teilaenderung nicht.

    Entscheidend ist nicht, dass die Aenderung ankommt, sondern dass der Rest
    der Datei sie ueberlebt: das Modell hat den Fueller nie gesehen, und genau
    deshalb darf er danach unveraendert dastehen.
    """
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    datei = _grosse_konfiguration(server)
    vorher = datei.read_text(encoding="utf-8")

    proposal = _patch(
        db, owner_user, conversation, server,
        path="buffs.xml",
        expected_revision=content_revision(datei.read_bytes()),
        edits=[
            {
                "find": '<buff name="staminaLoss" value="1.0"/>',
                "replace": '<buff name="staminaLoss" value="0.35"/>',
            },
            {
                "find": '<buff name="staminaRegen" value="1.0"/>',
                "replace": '<buff name="staminaRegen" value="3.0"/>',
            },
        ],
    )
    db.commit()
    assert proposal.preview_json is not None
    vorschau = json.loads(proposal.preview_json)
    assert vorschau["edits"] == 2
    # Die Vorschau zeigt die Stelle, nicht die Datei — sonst waere sie so lang
    # wie die Datei und niemand liest sie.
    assert '+  <buff name="staminaLoss" value="0.35"/>' in vorschau["diff"]
    assert "fueller0" not in vorschau["diff"]

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=owner_user
    )
    ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=owner_user, confirmation_token=token
    )

    nachher = datei.read_text(encoding="utf-8")
    assert '<buff name="staminaLoss" value="0.35"/>' in nachher
    assert '<buff name="staminaRegen" value="3.0"/>' in nachher
    # Byte fuer Byte derselbe Rest: nur die zwei Zeilen unterscheiden sich.
    assert nachher == vorher.replace(
        '<buff name="staminaLoss" value="1.0"/>',
        '<buff name="staminaLoss" value="0.35"/>',
    ).replace(
        '<buff name="staminaRegen" value="1.0"/>',
        '<buff name="staminaRegen" value="3.0"/>',
    )


def test_patch_refuses_a_search_text_that_is_not_unique(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Mehrdeutig heisst abgewiesen, nicht "die erste Stelle".

    `value="1"` steht in einer Spielkonfiguration hundertfach. Wuerde die erste
    Fundstelle gewinnen, aendert der Vorschlag etwas anderes als das, was in
    der Vorschau steht — und der Mensch bestaetigt eine Zusage, die nicht gilt.
    """
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    datei = _grosse_konfiguration(server)
    revision = content_revision(datei.read_bytes())
    vorher = datei.read_text(encoding="utf-8")

    with pytest.raises(ai_action_errors.AiActionValidationError) as mehrfach:
        _patch(
            db, owner_user, conversation, server,
            path="buffs.xml",
            expected_revision=revision,
            edits=[{"find": 'value="1"', "replace": 'value="2"'}],
        )
    assert "eindeutig" in str(mehrfach.value)
    db.rollback()

    with pytest.raises(ai_action_errors.AiActionValidationError) as gar_nicht:
        _patch(
            db, owner_user, conversation, server,
            path="buffs.xml",
            expected_revision=revision,
            edits=[{"find": "gibtesnicht", "replace": "egal"}],
        )
    assert "nicht vor" in str(gar_nicht.value)
    db.rollback()

    assert db.query(AiActionProposal).count() == 0
    assert datei.read_text(encoding="utf-8") == vorher


def test_patch_touches_a_clean_passage_in_a_file_that_holds_credentials(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Die Lockerung gegenueber der Vollersetzung — und ihre Grenze.

    `propose_config_update` weist jede Datei ab, in der irgendwo Zugangsdaten
    stehen, und das bleibt richtig: sie schriebe die ganze Datei neu und
    ersetzte das echte Passwort durch den Platzhalter, den das Modell gesehen
    hat. Eine Teilaenderung fasst diese Zeile nicht an. Verlangt wird deshalb
    nur, dass die **beruehrte** Stelle sauber ist — sonst waere eine
    `serverconfig.xml` dauerhaft nur von Hand aenderbar.
    """
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    datei = Path(server.install_dir) / "serverconfig.xml"
    datei.write_text(
        'password="super-secret-value"\n<property name="StaminaLoss" value="1.0"/>\n',
        encoding="utf-8",
    )
    revision = content_revision(datei.read_bytes())

    proposal = _patch(
        db, owner_user, conversation, server,
        path="serverconfig.xml",
        expected_revision=revision,
        edits=[{
            "find": '<property name="StaminaLoss" value="1.0"/>',
            "replace": '<property name="StaminaLoss" value="0.35"/>',
        }],
    )
    db.commit()
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=owner_user
    )
    ai_proposal_service.execute_proposal(
        db, proposal_id=proposal.id, user=owner_user, confirmation_token=token
    )
    nachher = datei.read_text(encoding="utf-8")
    assert '<property name="StaminaLoss" value="0.35"/>' in nachher
    # Das Geheimnis steht unveraendert da — weder ersetzt noch durch den
    # Platzhalter ueberschrieben, den das Modell gesehen hat.
    assert 'password="super-secret-value"' in nachher

    # Die Grenze: ein Geheimnis *in* der Ersetzung wird nicht geschrieben.
    with pytest.raises(ai_action_errors.AiActionValidationError):
        _patch(
            db, owner_user, conversation, server,
            path="serverconfig.xml",
            expected_revision=content_revision(datei.read_bytes()),
            edits=[{
                "find": '<property name="StaminaLoss" value="0.35"/>',
                "replace": 'password="neues-geheimnis-hier"',
            }],
        )
    db.rollback()
    assert 'neues-geheimnis-hier' not in datei.read_text(encoding="utf-8")


def test_patch_requires_the_current_revision(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Wer eine Stelle ersetzt, muss sagen, in welchem Stand er sie gesehen hat."""
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    datei = Path(server.install_dir) / "server.cfg"
    datei.write_text("port=2302\n", encoding="utf-8")
    veraltet = content_revision(datei.read_bytes())
    datei.write_text("port=2302\nmaxPlayers=40\n", encoding="utf-8")

    with pytest.raises(ai_action_errors.AiActionValidationError):
        _patch(
            db, owner_user, conversation, server,
            path="server.cfg",
            expected_revision=veraltet,
            edits=[{"find": "port=2302", "replace": "port=2402"}],
        )
    db.rollback()
    assert db.query(AiActionProposal).count() == 0
    assert datei.read_text(encoding="utf-8") == "port=2302\nmaxPlayers=40\n"


def test_patch_refuses_binary_files(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Eine Binaerdatei kommt als Ersatzzeichen-Salat an; zurueckgeschrieben ist sie hin."""
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    datei = Path(server.install_dir) / "world.bin"
    datei.write_bytes(b"\x00\x01\x02" * 100)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        _patch(
            db, owner_user, conversation, server,
            path="world.bin",
            expected_revision=content_revision(datei.read_bytes()),
            edits=[{"find": "�", "replace": "x"}],
        )
    db.rollback()
    assert datei.read_bytes() == b"\x00\x01\x02" * 100


def test_patch_runs_without_confirmation_when_autonomy_is_granted(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Die zweite Haelfte der Anforderung: im autonomen Modus wird nicht gefragt.

    Das Kriterium der Sperrliste ist Unumkehrbarkeit, nicht Risiko. Eine
    Dateiaenderung ist umkehrbar — `write_server_text` legt vorher einen
    Versionsschnappschuss an —, also gehoert sie nicht dorthin.
    """
    from services import ai_autonomy_service
    from services.ai_tool_registry import ALWAYS_CONFIRM_TOOLS

    assert "propose_config_patch" not in ALWAYS_CONFIRM_TOOLS

    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    datei = _grosse_konfiguration(server)
    ai_autonomy_service.set_grant(
        db, user=owner_user, server_id=server.id, enabled=True,
        max_actions_per_hour=10, granted_by=owner_user.id,
    )
    db.commit()

    proposal = _patch(
        db, owner_user, conversation, server,
        path="buffs.xml",
        expected_revision=content_revision(datei.read_bytes()),
        edits=[{
            "find": '<buff name="staminaLoss" value="1.0"/>',
            "replace": '<buff name="staminaLoss" value="0.35"/>',
        }],
    )
    db.commit()
    assert proposal.autonomous is True
    assert proposal.requires_confirmation is False

    ausgefuehrt, _ = ai_proposal_service.execute_autonomously(
        db, proposal_id=proposal.id, user=owner_user
    )
    assert ausgefuehrt.status == "succeeded"
    assert '<buff name="staminaLoss" value="0.35"/>' in datei.read_text(encoding="utf-8")


def test_a_rejected_patch_reveals_nothing_about_the_file(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Auch der *Versuch* darf nichts verraten.

    Der Payload-Bau liest den Zustand, ueber den er urteilt: um zu sagen, ob ein
    Suchtext eindeutig ist, muss er die Datei lesen und zaehlen. Lief die
    Rechtepruefung erst danach, war die Ablehnung selbst eine Auskunft — ein
    Benutzer mit `server.view` und ohne `server.files.read` bekam auf einen
    erfundenen Patch "kommt 3-mal vor" zurueck. Damit laesst sich der Inhalt
    einer Datei erraten, ohne sie je lesen zu duerfen.

    Gespeichert oder geschrieben wurde dabei nie etwas. Das Leck lag allein in
    der Reihenfolge — und genau deshalb faellt es ohne diesen Test nicht auf.
    """
    role = Role(name="nur-sehen", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, regular_user, [role.id])

    server = _server(db, regular_user, tmp_path)
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view"
    ))
    conversation = _conversation(db, regular_user, server)
    db.commit()

    datei = Path(server.install_dir) / "server.cfg"
    datei.write_text("maxPlayers=40\nmaxPlayers=40\nmaxPlayers=40\n", encoding="utf-8")

    with pytest.raises(ai_action_errors.AiActionValidationError) as exc:
        _patch(
            db, regular_user, conversation, server,
            path="server.cfg",
            expected_revision=content_revision(datei.read_bytes()),
            edits=[{"find": "maxPlayers=40", "replace": "maxPlayers=60"}],
        )

    meldung = str(exc.value)
    assert meldung == "AI-Aktion ist nicht erlaubt"
    # Keine Trefferzahl, kein Hinweis auf Groesse, Existenz oder Inhalt.
    assert "3" not in meldung
    db.rollback()
    assert db.query(AiActionProposal).count() == 0


def test_ai_file_tools_need_the_same_permissions_as_the_file_manager(
    db: Session, regular_user: User, tmp_path: Path
) -> None:
    """Die Zusage in einem Test: die KI kann nur, was der Benutzer selbst kann.

    Der Dateimanager verlangt `server.files.read` zum Lesen
    (`routers/files.py::read_file`) und `server.files.write` zum Schreiben
    (`routers/files.py::write_file`). Beide Wege muenden in dieselben Funktionen
    `read_server_text` / `write_server_text`. Wenn die KI mit weniger
    durchkaeme, waere sie ein zweiter Zugang mit eigenen Regeln — und die
    Rechtevergabe des Betreibers waere an ihr vorbei umgangen.
    """
    role = Role(name="nur-chat", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, regular_user, [role.id])

    server = _server(db, regular_user, tmp_path)
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view"
    ))
    conversation = _conversation(db, regular_user, server)
    db.commit()
    datei = Path(server.install_dir) / "server.cfg"
    datei.write_text("maxPlayers=40\n", encoding="utf-8", newline="\n")

    # Ohne `server.files.read`: kein Lesen, kein Auflisten, kein Suchen.
    for werkzeug, argumente in (
        ("read_config", {"path": "server.cfg"}),
        ("list_server_files", {}),
        ("search_server_files", {"query": "maxPlayers"}),
    ):
        with pytest.raises(ai_action_errors.AiActionValidationError):
            ai_action_service.execute_read_tool(
                db,
                user=regular_user,
                tool_name=werkzeug,
                arguments={"server_id": server.id, **argumente},
            )

    # Mit Leserecht geht das Lesen — aber noch immer kein Schreiben.
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.files.read"
    ))
    db.commit()
    gelesen = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="read_config",
        arguments={"server_id": server.id, "path": "server.cfg"},
    )
    assert gelesen["content"] == "maxPlayers=40\n"

    for werkzeug, argumente in (
        ("propose_config_patch", {
            "expected_revision": gelesen["revision"],
            "edits": [{"find": "maxPlayers=40", "replace": "maxPlayers=60"}],
        }),
        ("propose_config_update", {
            "content": "maxPlayers=60\n",
            "expected_revision": gelesen["revision"],
        }),
    ):
        with pytest.raises(ai_action_errors.AiActionValidationError):
            ai_proposal_service.create_proposal(
                db,
                user=regular_user,
                conversation=conversation,
                tool_name=werkzeug,
                arguments={
                    "server_id": server.id, "path": "server.cfg",
                    "reason": "r", "expected_effect": "e", **argumente,
                },
                correlation_id=str(uuid4()),
            )
        db.rollback()

    assert db.query(AiActionProposal).count() == 0
    assert datei.read_text(encoding="utf-8") == "maxPlayers=40\n"

    # Und mit Schreibrecht geht es. Sonst waere der Test oben auch dann gruen,
    # wenn das Werkzeug schlicht kaputt ist.
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.files.write"
    ))
    db.commit()
    vorschlag = _patch(
        db, regular_user, conversation, server,
        path="server.cfg",
        expected_revision=gelesen["revision"],
        edits=[{"find": "maxPlayers=40", "replace": "maxPlayers=60"}],
    )
    assert vorschlag.tool_name == "propose_config_patch"


# ── Suche ─────────────────────────────────────────────────────────────────


def test_file_search_finds_the_line_in_a_file_and_below_a_directory(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Ohne Suche waere das Fenster von `read_config` nutzlos.

    Eine Konfiguration mit viertausend Zeilen durchblaettert kein Modell. Es
    braucht die Zeilennummer, um mit `offset` dorthin zu springen — das ist der
    Schritt, der den Betriebsfall ueberhaupt loesbar macht.
    """
    server = _server(db, owner_user, tmp_path)
    datei = _grosse_konfiguration(server)
    unterordner = Path(server.install_dir) / "Data" / "Config"
    unterordner.mkdir(parents=True)
    (unterordner / "gamestages.xml").write_text(
        "<gamestages>\n  <stage staminaLoss=\"1.0\"/>\n</gamestages>\n", encoding="utf-8"
    )

    in_datei = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="search_server_files",
        arguments={"server_id": server.id, "path": "buffs.xml", "query": "staminaLoss"},
    )
    assert [t["line"] for t in in_datei["matches"]] == [4002]
    assert in_datei["matches"][0]["path"] == "buffs.xml"

    # Gross- und Kleinschreibung egal: das Modell kennt die Schreibweise nicht,
    # bevor es gesucht hat.
    im_baum = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="search_server_files",
        arguments={"server_id": server.id, "query": "STAMINALOSS", "context": 1},
    )
    gefunden = {t["path"] for t in im_baum["matches"]}
    assert gefunden == {"buffs.xml", "Data/Config/gamestages.xml"}
    assert all("context" in t for t in im_baum["matches"])

    assert datei.exists()


def test_file_search_redacts_credentials_it_stumbles_over(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Trefferzeilen gehen in den Kontext des Modells — also durch dieselbe Redaktion."""
    server = _server(db, owner_user, tmp_path)
    (Path(server.install_dir) / "server.cfg").write_text(
        "port=2302\napi_key=super-secret-value\n", encoding="utf-8"
    )

    ergebnis = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="search_server_files",
        arguments={"server_id": server.id, "query": "api_key"},
    )
    assert ergebnis["matches"], "die Zeile selbst soll auffindbar bleiben"
    assert all("super-secret-value" not in t["text"] for t in ergebnis["matches"])


def test_unregistered_tool_is_rejected_without_persistence(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)

    try:
        ai_proposal_service.create_proposal(
            db,
            user=owner_user,
            conversation=conversation,
            tool_name="execute_shell",
            arguments={"command": "ignored"},
            correlation_id=str(uuid4()),
        )
    except ai_action_errors.AiActionValidationError:
        pass
    else:
        raise AssertionError("unregistered tool was accepted")
    assert db.query(AiActionProposal).count() == 0


def test_read_log_tool_bounds_and_redacts_output(
    db: Session,
    owner_user: User,
    tmp_path: Path,
    monkeypatch,
) -> None:
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    monkeypatch.setattr(
        "services.docker_service.logs",
        lambda *_args, **_kwargs: "ready\napi_key=provider-secret-value\n",
    )

    result = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="read_server_logs",
        arguments={"server_id": server.id, "lines": 50},
    )

    assert result["redacted"] is True
    assert "provider-secret-value" not in result["content"]
    assert "[REDACTED]" in result["content"]
    try:
        ai_action_service.execute_read_tool(
            db,
            user=owner_user,
            tool_name="read_server_logs",
            arguments={"server_id": server.id, "lines": 201},
        )
    except ai_action_errors.AiActionValidationError:
        pass
    else:
        raise AssertionError("oversized log request was accepted")


def test_confirmation_token_is_hashed_one_time_and_config_write_is_revision_bound(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    tmp_path: Path,
) -> None:
    server = _server(db, owner_user, tmp_path)
    config = Path(server.install_dir) / "server.cfg"
    config.write_text("port=2302\n", encoding="utf-8")
    conversation = _conversation(db, owner_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_config_update",
        arguments={
            "server_id": server.id,"path": "server.cfg",
            "content": "port=2402\n",
            "expected_revision": content_revision(config.read_bytes()),
            "reason": "Testbegruendung",
            "expected_effect": "Testwirkung",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    confirmed = client.post(
        f"/api/ai/actions/{proposal.id}/confirm",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert confirmed.status_code == 200
    token = confirmed.json()["confirmation_token"]
    db.expire_all()
    stored = db.get(AiActionProposal, proposal.id)
    assert stored is not None
    assert stored.confirmation_token_hash and stored.confirmation_token_hash != token
    assert token not in stored.confirmation_token_hash

    executed = client.post(
        f"/api/ai/actions/{proposal.id}/execute",
        json={"confirmation_token": token},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    replay = client.post(
        f"/api/ai/actions/{proposal.id}/execute",
        json={"confirmation_token": token},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    assert executed.status_code == 200
    assert executed.json()["proposal"]["status"] == "succeeded"
    assert replay.status_code == 409
    assert config.read_text(encoding="utf-8") == "port=2402\n"


def test_execute_blocks_changed_revision_and_marks_proposal_failed(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    tmp_path: Path,
) -> None:
    server = _server(db, owner_user, tmp_path)
    config = Path(server.install_dir) / "server.cfg"
    config.write_text("port=2302\n", encoding="utf-8")
    conversation = _conversation(db, owner_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_config_update",
        arguments={
            "server_id": server.id,"path": "server.cfg",
            "content": "port=2402\n",
            "expected_revision": content_revision(config.read_bytes()),
            "reason": "Testbegruendung",
            "expected_effect": "Testwirkung",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()
    confirmed = client.post(
        f"/api/ai/actions/{proposal.id}/confirm",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    ).json()
    config.write_text("port=2502\n", encoding="utf-8")

    response = client.post(
        f"/api/ai/actions/{proposal.id}/execute",
        json={"confirmation_token": confirmed["confirmation_token"]},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "AI_ACTION_REVISION_CONFLICT"
    db.expire_all()
    assert db.get(AiActionProposal, proposal.id).status == "failed"
    assert config.read_text(encoding="utf-8") == "port=2502\n"


def test_expired_confirmation_cannot_execute(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments={"server_id": server.id, "reason": "Testbegruendung", "expected_effect": "Testwirkung"},
        correlation_id=str(uuid4()),
    )
    db.commit()
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    proposal, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=owner_user, now=past
    )

    try:
        ai_proposal_service.execute_proposal(
            db,
            proposal_id=proposal.id,
            user=owner_user,
            confirmation_token=token,
        )
    except ai_action_errors.AiActionStateError as exc:
        assert exc.code == "AI_ACTION_CONFIRMATION_EXPIRED"
    else:
        raise AssertionError("expired confirmation was accepted")
    db.refresh(proposal)
    assert proposal.status == "expired"


def test_startup_recovery_fails_closed(db: Session, owner_user: User, tmp_path: Path) -> None:
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments={"server_id": server.id, "reason": "Testbegruendung", "expected_effect": "Testwirkung"},
        correlation_id=str(uuid4()),
    )
    proposal.status = "executing"
    db.commit()

    assert ai_proposal_service.reconcile_interrupted_actions(db) == 1
    db.refresh(proposal)
    assert proposal.status == "failed"
    assert proposal.error_code == "AI_ACTION_INTERRUPTED"


def test_confirmation_rechecks_revoked_rbac(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    tmp_path: Path,
) -> None:
    role = Role(name=f"ai-actions-{regular_user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    set_user_roles(db, regular_user, [role.id])
    server = _server(db, regular_user, tmp_path)
    grants = [
        ServerPermission(
            user_id=regular_user.id,
            server_id=server.id,
            permission_key=key,
        )
        for key in ("server.view", "server.backups.create")
    ]
    db.add_all(grants)
    db.commit()
    conversation = _conversation(db, regular_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments={"server_id": server.id, "reason": "Testbegruendung", "expected_effect": "Testwirkung"},
        correlation_id=str(uuid4()),
    )
    db.commit()
    db.delete(grants[1])
    db.commit()

    response = client.post(
        f"/api/ai/actions/{proposal.id}/confirm",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 403
    db.refresh(proposal)
    assert proposal.status == "proposed"


def test_lifecycle_proposal_stays_executing_until_the_task_finishes(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Ein nur eingereihter Start darf nicht als "ausgefuehrt" gelten.

    Start, Stop und Restart laufen in einem Hintergrund-Thread weiter. Bis der
    fertig ist, weiss niemand, ob der Server wirklich hochgekommen ist — der
    Vorschlag darf das also nicht behaupten.
    """
    from unittest.mock import patch

    from services.operation_task_service import finish_lifecycle_task
    from services.actor_context import ActorContext
    from services.operation_task_service import create_or_reuse_task, mark_running

    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_server_lifecycle",
        arguments={"server_id": server.id, "operation": "start", "reason": "Testbegruendung", "expected_effect": "Testwirkung"},
        correlation_id=str(uuid4()),
    )
    db.commit()
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=owner_user
    )

    task, _created = create_or_reuse_task(
        db,
        actor=ActorContext.for_user(owner_user),
        task_type="server.lifecycle.start",
        request_hash="f" * 64,
        idempotency_key=f"lifecycle-proposal-{proposal.id}",
    )
    mark_running(db, task, "queued")
    task.server_id = server.id
    db.commit()

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        return_value={"status": "queued", "task_id": task.id},
    ):
        executed, _result = ai_proposal_service.execute_proposal(
            db, proposal_id=proposal.id, user=owner_user, confirmation_token=token
        )

    assert executed.status == "executing"
    assert executed.task_id == task.id
    assert executed.executed_at is None

    # Der Hintergrund-Vorgang scheitert — der Vorschlag muss das uebernehmen.
    finish_lifecycle_task(db, task.id, succeeded=False, error_code="server_lifecycle_failed")

    db.expire_all()
    final = db.query(AiActionProposal).filter(AiActionProposal.id == proposal.id).one()
    assert final.status == "failed"
    assert final.error_code == "server_lifecycle_failed"


def test_lifecycle_proposal_becomes_succeeded_when_the_task_succeeds(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    from unittest.mock import patch

    from services.actor_context import ActorContext
    from services.operation_task_service import (
        create_or_reuse_task,
        finish_lifecycle_task,
        mark_running,
    )

    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_server_lifecycle",
        arguments={"server_id": server.id, "operation": "restart", "reason": "Testbegruendung", "expected_effect": "Testwirkung"},
        correlation_id=str(uuid4()),
    )
    db.commit()
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=owner_user
    )
    task, _created = create_or_reuse_task(
        db,
        actor=ActorContext.for_user(owner_user),
        task_type="server.lifecycle.restart",
        request_hash="a" * 64,
        idempotency_key=f"lifecycle-ok-{proposal.id}",
    )
    mark_running(db, task, "queued")
    task.server_id = server.id
    db.commit()

    with patch(
        "services.server_action_service.request_lifecycle_operation",
        return_value={"status": "queued", "task_id": task.id},
    ):
        ai_proposal_service.execute_proposal(
            db, proposal_id=proposal.id, user=owner_user, confirmation_token=token
        )

    finish_lifecycle_task(db, task.id, succeeded=True)

    db.expire_all()
    final = db.query(AiActionProposal).filter(AiActionProposal.id == proposal.id).one()
    assert final.status == "succeeded"
    assert final.executed_at is not None


def test_a_confirmed_delete_uses_the_one_deletion_path_and_is_marked_as_ai(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Die KI bekommt keinen eigenen Loeschweg.

    `delete_server_completely` ist laut eigenem Docstring "die eine
    Implementierung; der Router ist nur noch ihr HTTP-Rand". Panel,
    Hoster-Anbindung und KI muessen denselben Aufruf nehmen — sonst laufen
    Postgres-Ressourcen, S3-Objekte, Firewall-Regeln und das Audit auf einem
    Pfad anders als auf dem anderen.

    Der Test haelt zweierlei fest: dass genau diese Funktion gerufen wird, und
    dass sie erfaehrt, wer ausgeloest hat — `origin="ai"` landet im Audit und
    ist spaeter der einzige Unterschied zu einem Klick im Panel.
    """
    from unittest.mock import patch

    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_server_delete",
        arguments={
            "server_id": server.id,
            "reason": "Der Benutzer will den Server entfernen.",
            "expected_effect": "Server, Dateien und Backups sind weg.",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    # Die Vorschau nennt den Namen — daran erkennt ein Mensch beim Bestaetigen,
    # ob der richtige Server gemeint ist. Die ID allein sagt ihm nichts.
    vorschau = json.loads(proposal.preview_json)
    assert vorschau["server_name"] == server.name
    assert vorschau["irreversible"] is True

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=owner_user
    )
    gesehen: dict = {}

    def _fake_delete(db_, *, server_id, actor):
        gesehen["server_id"] = server_id
        gesehen["origin"] = actor.origin
        gesehen["user_id"] = actor.user.id
        return {"message": "Server gelöscht"}

    with patch(
        "services.server_deletion_service.delete_server_completely",
        _fake_delete,
    ):
        updated, _result = ai_proposal_service.execute_proposal(
            db, proposal_id=proposal.id, user=owner_user, confirmation_token=token
        )

    assert updated.status == "succeeded"
    assert gesehen == {
        "server_id": server.id, "origin": "ai", "user_id": owner_user.id
    }


def test_a_confirmed_restore_uses_the_one_restore_path(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Auch der Restore bekommt keinen eigenen Weg.

    Die Vorgabe des Betreibers war ausdruecklich: die KI nutzt dieselbe Logik
    wie der Benutzer, **wegen S3**. Ein Nachbau wuerde nicht nur den
    S3-Download anders machen, sondern vor allem die Reihenfolge verlieren, auf
    die es ankommt — herunterladen und entschluesseln, bevor der Container
    faellt. Wer zuerst stoppt, hat bei falschem Passwort einen gestoppten Server
    und kein Backup.
    """
    from unittest.mock import patch

    from models import Backup

    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    backup = Backup(server_id=server.id, filename=str(tmp_path / "b.tar.gz"), size_mb=3)
    db.add(backup)
    db.commit()
    db.refresh(backup)

    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_backup_restore",
        arguments={
            "server_id": server.id,
            "backup_id": backup.id,
            "reason": "Der Benutzer will den Stand von vorhin zurueck.",
            "expected_effect": "Die Serverdaten entsprechen wieder dem Backup.",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    # Die Vorschau nennt, *welchen* Stand man zurueckholt. "Backup einspielen"
    # ohne Datum ist keine Grundlage fuer eine Zustimmung.
    vorschau = json.loads(proposal.preview_json)
    assert vorschau["backup_id"] == backup.id
    assert vorschau["backup_created_at"] is not None
    assert vorschau["irreversible"] is True

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal.id, user=owner_user
    )
    gesehen: dict = {}

    def _fake_restore(db_, *, server_id, backup_id, actor):
        gesehen.update(
            server_id=server_id, backup_id=backup_id, origin=actor.origin
        )
        return {"message": "Backup wiederhergestellt"}

    with patch(
        "services.backup_restore_service.restore_server_backup", _fake_restore
    ):
        updated, _result = ai_proposal_service.execute_proposal(
            db, proposal_id=proposal.id, user=owner_user, confirmation_token=token
        )

    assert updated.status == "succeeded"
    assert gesehen == {
        "server_id": server.id, "backup_id": backup.id, "origin": "ai"
    }


def test_a_restore_cannot_reach_a_backup_of_another_server(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Die Backup-ID wird gegen den Server aufgeloest, nicht blind uebernommen.

    Ohne diese Bindung waere ein Vorschlag ein Weg, fremde Backup-IDs
    abzuzaehlen — und im schlimmsten Fall den Stand eines fremden Servers ueber
    den eigenen zu legen.
    """
    from models import Backup

    server = _server(db, owner_user, tmp_path)
    fremder = Server(
        name="Fremd", game_type="dayz",
        install_dir=str(tmp_path / "fremd"), container_name="msm-fremd",
        status="stopped",
    )
    db.add(fremder)
    db.commit()
    db.refresh(fremder)
    fremdes_backup = Backup(
        server_id=fremder.id, filename=str(tmp_path / "f.tar.gz"), size_mb=1
    )
    db.add(fremdes_backup)
    db.commit()
    db.refresh(fremdes_backup)

    conversation = _conversation(db, owner_user, server)
    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_proposal_service.create_proposal(
            db,
            user=owner_user,
            conversation=conversation,
            tool_name="propose_backup_restore",
            arguments={
                "server_id": server.id,
                "backup_id": fremdes_backup.id,
                "reason": "Zurueckholen.",
                "expected_effect": "Alter Stand.",
            },
            correlation_id=str(uuid4()),
        )


def test_the_backup_name_from_the_model_is_redacted_and_capped(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Der Name ist Modelltext und landet in einer Liste, die Menschen lesen."""
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)

    proposal = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments={
            "server_id": server.id,
            "name": "vor Mod-Update " + "x" * 200,
            "reason": "Absichern.",
            "expected_effect": "Ein Stand liegt vor.",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    vorschau = json.loads(proposal.preview_json)
    assert vorschau["name"].startswith("vor Mod-Update")
    assert len(vorschau["name"]) <= 64


def test_the_ai_sees_the_same_files_a_human_sees_in_the_file_manager(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Die Endungsliste ist weg — das war die eigentliche Bitte des Betreibers.

    Frueher liess `_config_path` nur neun Erweiterungen durch. Damit war fuer
    die KI unsichtbar, was ein Mensch im Dateimanager selbstverstaendlich
    bearbeitet: Dateien **ohne** Endung (`Dockerfile`, `.env`, `whitelist`),
    `.xml` (Ark, Unreal), `.lua` (Garry's Mod, DayZ), `.sh`. Genau daraus
    entstand die Sorge, die KI koenne "an einer anderen Stelle etwas anderes
    einstellen".
    """
    server = _server(db, owner_user, tmp_path)
    wurzel = Path(server.install_dir)
    (wurzel / "server.properties").write_text("max-players=20\n", encoding="utf-8")
    (wurzel / "whitelist").write_text("maik\n", encoding="utf-8")
    (wurzel / "Game.ini").write_text("[/Script]\n", encoding="utf-8")
    (wurzel / "start.sh").write_text("#!/bin/sh\necho los\n", encoding="utf-8")
    (wurzel / "settings.xml").write_text("<config/>\n", encoding="utf-8")
    (wurzel / "config").mkdir()

    for pfad in ("whitelist", "start.sh", "settings.xml", "server.properties"):
        gelesen = ai_action_service.execute_read_tool(
            db, user=owner_user, tool_name="read_config",
            arguments={"server_id": server.id, "path": pfad},
        )
        assert gelesen["editable"] is True, f"{pfad} muesste bearbeitbar sein"
        assert gelesen["binary"] is False


def test_listing_a_directory_replaces_guessing_file_names(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Ohne Verzeichnisbaum muesste die KI Namen raten.

    Ein geratener Name ist entweder ein Treffer oder eine Fehlermeldung, aus der
    man Namen abzaehlen kann — beides schlechter als eine Liste.
    """
    server = _server(db, owner_user, tmp_path)
    wurzel = Path(server.install_dir)
    (wurzel / "server.properties").write_text("x\n", encoding="utf-8")
    (wurzel / "mods").mkdir()
    # Das Chunk-Upload-Verzeichnis gehoert in keine Liste.
    (wurzel / ".msm-uploads").mkdir()

    ergebnis = ai_action_service.execute_read_tool(
        db, user=owner_user, tool_name="list_server_files",
        arguments={"server_id": server.id},
    )

    namen = {eintrag["name"] for eintrag in ergebnis["entries"]}
    assert namen == {"server.properties", "mods"}
    assert ergebnis["exists"] is True
    assert any(e["is_dir"] for e in ergebnis["entries"])


def test_a_binary_file_is_reported_as_such_instead_of_as_garbled_text(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Seit die Endungsliste weg ist, kann hier auch ein Mod-Jar landen.

    Der Dateizugriff dekodiert mit `errors="replace"` — eine Binaerdatei kommt
    als Ersatzzeichen-Salat zurueck. Wuerde das Modell ihn zurueckschreiben,
    waere die Datei zerstoert. Der Salat kostet ausserdem Tokens und sagt nichts.
    """
    server = _server(db, owner_user, tmp_path)
    (Path(server.install_dir) / "mod.jar").write_bytes(
        b"PK\x03\x04" + bytes(range(256)) * 4
    )

    gelesen = ai_action_service.execute_read_tool(
        db, user=owner_user, tool_name="read_config",
        arguments={"server_id": server.id, "path": "mod.jar"},
    )

    assert gelesen["binary"] is True
    assert gelesen["editable"] is False
    assert gelesen["content"] == ""
    assert gelesen["revision"] is None
    assert "zerstoeren" in gelesen["edit_blocked_reason"]


def test_a_write_proposal_with_binary_content_is_refused(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Zweite Schranke dort, wo der Schaden entstuende.

    `read_config` kennzeichnet eine Binaerdatei bereits als nicht bearbeitbar.
    Die Pruefung beim Vorschlagen greift auch dann, wenn der Inhalt auf einem
    anderen Weg entstanden ist.
    """
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)

    with pytest.raises(ai_action_errors.AiActionValidationError, match="kein Text"):
        ai_proposal_service.create_proposal(
            db,
            user=owner_user,
            conversation=conversation,
            tool_name="propose_config_update",
            arguments={
                "server_id": server.id,
                "path": "irgendwas.dat",
                "content": "\x00\x00binaer",
                "expected_revision": None,
                "reason": "Anpassen.",
                "expected_effect": "Geaendert.",
            },
            correlation_id=str(uuid4()),
        )


def test_a_path_still_cannot_escape_the_server_directory(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Die Endung war nie die Sicherheitsgrenze — die ist unveraendert da.

    Ihr Wegfall darf nicht mit einer Lockerung verwechselt werden: relativ,
    kein `..`, keine Backslashes, begrenzte Laenge. Der Rest liegt in
    `safe_path`, das ueber `resolve()` auch Symlinks nach aussen abfaengt.
    """
    server = _server(db, owner_user, tmp_path)

    for boesartig in (
        "../../etc/passwd",
        "/etc/passwd",
        "..\windows\system32\config",
        "config/../../../etc/shadow",
        "x" * 300 + ".cfg",
    ):
        with pytest.raises(ai_action_errors.AiActionValidationError):
            ai_action_service.execute_read_tool(
                db, user=owner_user, tool_name="read_config",
                arguments={"server_id": server.id, "path": boesartig},
            )


# ── Der Vorschlag ueberlebt den Server, den er loescht ────────────────────
#
# Der Betriebsanlass: "sag der KI, sie soll den Server loeschen" — die Loeschung
# gelingt, und das Panel meldet "Aktionsvorschlag nicht gefunden".
#
# `ai_action_proposals.server_id` kaskadierte auf `servers.id`. Loeschte
# `execute_proposal` den Server, vernichtete die Datenbank im selben Zug die
# Vorschlagszeile — und vier Zeilen weiter stolperte der Aufruf ueber sie:
# `db.get(...)` gab `None`, `AI_ACTION_NOT_FOUND` flog, der Router machte 404
# daraus. Weder `status='succeeded'` noch der Audit-Eintrag wurden geschrieben,
# und `_vorschlag_ergebnisse` meldete dem Modell einen Fehlschlag, den es nie
# gab.
#
# Die folgenden Tests nehmen deshalb die **echte** `delete_server_completely`.
# Der Test darueber patcht sie vollstaendig weg — das ist fuer seine Frage
# richtig (wird der eine Weg genommen?), macht ihn fuer diese aber blind: eine
# Funktion, die nie eine Zeile loescht, loest auch keine Kaskade aus.


def _loeschbar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nur die Wirkungen nach aussen stillegen, nicht die Fachlogik.

    Vier Dinge greifen ueber den Prozess hinaus: PostgreSQL-Ressourcen, der
    Container, die Firewall und der Neustart-Zeitplan. Alles andere — Audit,
    Reihenfolge, Rechtepruefung, `db.delete(server)` und der Commit — laeuft
    echt, denn genau dort sass der Fehler.
    """
    monkeypatch.setattr(
        "services.postgres_service.drop_server_resources", lambda db, server_id: None
    )
    monkeypatch.setattr(
        "services.docker_service.remove", lambda name, **kwargs: {"ok": True}
    )
    monkeypatch.setattr(
        "services.docker_service.repair_bind_mount_permissions", lambda path: {"ok": True}
    )
    monkeypatch.setattr("services.server_deletion_service.close_ports", lambda *a, **k: None)
    monkeypatch.setattr(
        "services.server_deletion_service.iptables_revoke_server", lambda *a, **k: None
    )
    monkeypatch.setattr("services.scheduler_service.remove_restart_jobs", lambda server_id: None)


def _panel_unterhaltung(db: Session, user: User) -> AiConversation:
    """Die eine Unterhaltung des Benutzers — ohne Serverbezug, wie im Betrieb.

    Der Helfer `_conversation` weiter oben setzt `server_id` und bildet damit
    eine Form ab, die es seit dem Einzelchat nicht mehr gibt:
    `get_or_create_primary_conversation` legt sie ausdruecklich mit
    `server_id=None` an. Der Unterschied ist hier nicht kosmetisch — eine
    serverbezogene Unterhaltung faellt beim Loeschen des Servers selbst der
    Kaskade zum Opfer und nimmt den Vorschlag ueber `conversation_id` mit. Der
    Test wuerde dann etwas messen, das im Panel gar nicht vorkommt.
    """
    row = AiConversation(id=str(uuid4()), user_id=user.id, server_id=None, title="Panel")
    db.add(row)
    db.commit()
    return row


def _loeschvorschlag(
    db: Session, user: User, conversation: AiConversation, server: Server
) -> AiActionProposal:
    return ai_proposal_service.create_proposal(
        db,
        user=user,
        conversation=conversation,
        tool_name="propose_server_delete",
        arguments={
            "server_id": server.id,
            "reason": "Der Benutzer will den Server entfernen.",
            "expected_effect": "Server, Dateien und Backups sind weg.",
        },
        correlation_id=str(uuid4()),
    )


def test_a_geloeschter_server_laesst_seinen_vorschlag_stehen(
    db: Session, owner_user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Beleg ueberlebt die Tat.

    Ein Aktionsvorschlag ist ein Beleg der Unterhaltung eines Benutzers, kein
    Kind eines Servers. Er haengt zu Recht an `conversation_id` und `user_id`;
    `server_id` ist nur ein Bezug und darf ihn nicht mitreissen.
    """
    _loeschbar(monkeypatch)
    server = _server(db, owner_user, tmp_path)
    server_id = server.id
    conversation = _panel_unterhaltung(db, owner_user)
    proposal = _loeschvorschlag(db, owner_user, conversation, server)
    db.commit()
    proposal_id = proposal.id

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal_id, user=owner_user
    )
    ausgefuehrt, _result = ai_proposal_service.execute_proposal(
        db, proposal_id=proposal_id, user=owner_user, confirmation_token=token
    )

    assert db.get(Server, server_id) is None, "Der Server muss wirklich weg sein"
    uebrig = db.get(AiActionProposal, proposal_id)
    assert uebrig is not None, "Der Vorschlag darf nicht mitgeloescht werden"
    assert uebrig.status == "succeeded"
    assert uebrig.executed_at is not None
    # Der Bezug faellt, der Beleg bleibt. Welcher Server gemeint war, steht
    # weiterhin lesbar in der Vorschau — und dort steht es fuer einen Menschen,
    # nicht als Zahl.
    assert uebrig.server_id is None
    assert json.loads(uebrig.preview_json)["server_name"] == "AI Action Server"
    assert ausgefuehrt.status == "succeeded"


def test_a_der_lauf_erfaehrt_den_erfolg(
    db: Session, owner_user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die KI darf nicht das Gegenteil der Wahrheit erzaehlen.

    Beim Aufwecken fragt `_vorschlag_ergebnisse` die Vorschlagszeilen ab und
    meldet dem Modell, was aus ihnen geworden ist. Fehlte die Zeile, meldete es
    `status: "failed"` mit `AI_ACTION_NOT_FOUND` — und das Modell teilte dem
    Betreiber mit, das Loeschen sei gescheitert, waehrend der Server weg war.
    Das ist die schlimmere Haelfte des Fehlers: eine falsche Fehlermeldung
    kostet Zeit, eine falsche Erfolgsmeldung kostet Vertrauen.
    """
    from services.ai_stream_service import _vorschlag_ergebnisse

    _loeschbar(monkeypatch)
    server = _server(db, owner_user, tmp_path)
    conversation = _panel_unterhaltung(db, owner_user)
    proposal = _loeschvorschlag(db, owner_user, conversation, server)
    db.commit()
    proposal_id = proposal.id

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal_id, user=owner_user
    )
    ai_proposal_service.execute_proposal(
        db, proposal_id=proposal_id, user=owner_user, confirmation_token=token
    )

    ergebnisse = _vorschlag_ergebnisse(db, [proposal_id])

    assert len(ergebnisse) == 1
    assert ergebnisse[0]["tool_name"] == "propose_server_delete"
    assert ergebnisse[0]["status"] == "succeeded"
    assert "error_code" not in ergebnisse[0]


def test_a_alte_vorschlaege_bleiben_im_verlauf(
    db: Session, owner_user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Umfang war groesser als das Loeschen.

    **Jeder** je fuer einen Server erzeugte Vorschlag verschwand aus dem
    Chatverlauf, sobald dieser Server geloescht wurde — auch eine
    Konfigaenderung von vor Wochen. Der Verlauf schrieb sich damit rueckwirkend
    um, ohne dass jemand etwas zurueckgenommen haette.
    """
    _loeschbar(monkeypatch)
    server = _server(db, owner_user, tmp_path)
    config = Path(server.install_dir) / "server.cfg"
    config.write_text("port=2302\n", encoding="utf-8")
    conversation = _panel_unterhaltung(db, owner_user)
    frueher = ai_proposal_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_config_update",
        arguments={
            "server_id": server.id, "path": "server.cfg",
            "content": "port=2402\n",
            "expected_revision": content_revision(config.read_bytes()),
            "reason": "Port anpassen.",
            "expected_effect": "Der Server lauscht auf 2402.",
        },
        correlation_id=str(uuid4()),
    )
    loeschen = _loeschvorschlag(db, owner_user, conversation, server)
    db.commit()
    frueher_id, loeschen_id = frueher.id, loeschen.id

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=loeschen_id, user=owner_user
    )
    ai_proposal_service.execute_proposal(
        db, proposal_id=loeschen_id, user=owner_user, confirmation_token=token
    )

    verbleibend = {
        row.id for row in db.query(AiActionProposal).filter(
            AiActionProposal.conversation_id == conversation.id
        ).all()
    }
    assert verbleibend == {frueher_id, loeschen_id}


def test_a_execute_meldet_erfolg_statt_404(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dieselbe Zusage am HTTP-Rand — dort hat der Betreiber sie gebrochen gesehen.

    Sein Log zeigte `GET /api/ai/actions/<uuid> 404`. Diese Anfrage stellt die
    Oberflaeche nur im Fehlerfall: `execute` war fehlgeschlagen, die Karte holte
    den Stand nach, und auch das lief ins Leere. Beide Antworten muessen 200
    sein.
    """
    _loeschbar(monkeypatch)
    server = _server(db, owner_user, tmp_path)
    conversation = _panel_unterhaltung(db, owner_user)
    proposal = _loeschvorschlag(db, owner_user, conversation, server)
    db.commit()
    proposal_id = proposal.id

    bestaetigt = client.post(
        f"/api/ai/actions/{proposal_id}/confirm",
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    assert bestaetigt.status_code == 200

    ausgefuehrt = client.post(
        f"/api/ai/actions/{proposal_id}/execute",
        json={"confirmation_token": bestaetigt.json()["confirmation_token"]},
        cookies=owner_cookies, headers=_csrf(owner_cookies),
    )
    assert ausgefuehrt.status_code == 200, ausgefuehrt.text
    assert ausgefuehrt.json()["proposal"]["status"] == "succeeded"

    nachgeholt = client.get(
        f"/api/ai/actions/{proposal_id}", cookies=owner_cookies,
    )
    assert nachgeholt.status_code == 200, nachgeholt.text
    assert nachgeholt.json()["status"] == "succeeded"


def test_a_geloeschter_server_bleibt_bestaetigungspflichtig(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Waechter: an der Sperre aendert diese Korrektur nichts.

    Die Vorgabe des Betreibers lautet, dass im autonomen Modus alles durchlaeuft
    ausser Loeschvorgaengen. Wer den Vorschlag ueberlebensfaehig macht, koennte
    versucht sein, ihn auch gleich autonom zu machen — er bleibt unumkehrbar.
    """
    from services.ai_tool_registry import ALWAYS_CONFIRM_TOOLS

    server = _server(db, owner_user, tmp_path)
    conversation = _panel_unterhaltung(db, owner_user)
    proposal = _loeschvorschlag(db, owner_user, conversation, server)
    db.commit()

    assert "propose_server_delete" in ALWAYS_CONFIRM_TOOLS
    assert proposal.requires_confirmation is True
    assert proposal.autonomous is False


def test_a_entzogenes_recht_ist_kein_fehlender_vorschlag(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    regular_user: User,
    tmp_path: Path,
) -> None:
    """404 und 403 sagen zwei verschiedene Dinge — und beide muessen stimmen.

    Vier Sachverhalte liefen frueher in dasselbe "Aktionsvorschlag nicht
    gefunden": kaputte Kennung, fremde Zeile, fehlendes globales Recht,
    fehlendes `server.view`. Fuer den Betreiber sah ein entzogenes Recht damit
    aus wie ein verschwundener Vorschlag, und die Fehlersuche begann an der
    falschen Stelle. Genau darauf ist er bei diesem Fehler hereingefallen.

    Was **nicht** aufweicht: die Existenz fremder Zeilen. Die `user_id` steht
    schon in der Abfrage, geworfen wird nur fuer Vorschlaege, die dem Anrufer
    ohnehin gehoeren — ein fremder bleibt "gibt es nicht".
    """
    server = _server(db, owner_user, tmp_path)
    conversation = _panel_unterhaltung(db, owner_user)
    proposal = _loeschvorschlag(db, owner_user, conversation, server)
    db.commit()
    proposal_id = proposal.id

    # Solange alles steht: sichtbar.
    assert client.get(
        f"/api/ai/actions/{proposal_id}", cookies=owner_cookies
    ).status_code == 200

    # Recht entziehen, ohne den Vorschlag anzufassen.
    db.query(AiActionProposal).filter(AiActionProposal.id == proposal_id).update(
        {AiActionProposal.user_id: regular_user.id}
    )
    db.commit()
    # Jetzt gehoert er einem anderen — das bleibt "gibt es nicht", sonst waere
    # die blosse Antwort schon eine Auskunft ueber fremde Vorgaenge.
    fremd = client.get(f"/api/ai/actions/{proposal_id}", cookies=owner_cookies)
    assert fremd.status_code == 404

    # Und eine unbrauchbare Kennung ebenfalls.
    assert client.get(
        "/api/ai/actions/kein-uuid", cookies=owner_cookies
    ).status_code == 404


def test_a_entzogene_sicht_meldet_403_und_nicht_404(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    tmp_path: Path,
) -> None:
    """Die andere Haelfte: der Vorschlag ist da, die Sicht auf den Server ist weg.

    `test_confirmation_rechecks_revoked_rbac` haelt dasselbe fuer das
    Bestaetigen fest — dort kam schon immer 403, weil `confirm_proposal` den
    Fall ausdruecklich kennt. Das blosse Nachschlagen antwortete dagegen 404 und
    behauptete damit etwas Falsches ueber eine Zeile, die es sehr wohl gibt.
    """
    role = Role(name=f"ai-sicht-{regular_user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    set_user_roles(db, regular_user, [role.id])
    server = _server(db, regular_user, tmp_path)
    sicht = ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view",
    )
    db.add_all([
        sicht,
        ServerPermission(
            user_id=regular_user.id, server_id=server.id,
            permission_key="server.backups.create",
        ),
    ])
    db.commit()
    conversation = _panel_unterhaltung(db, regular_user)
    proposal = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments={
            "server_id": server.id,
            "reason": "Vor der Umstellung sichern.",
            "expected_effect": "Ein Backup liegt vor.",
        },
        correlation_id=str(uuid4()),
    )
    db.commit()
    proposal_id = proposal.id

    assert client.get(
        f"/api/ai/actions/{proposal_id}", cookies=user_cookies
    ).status_code == 200

    db.delete(sicht)
    db.commit()

    ohne_sicht = client.get(f"/api/ai/actions/{proposal_id}", cookies=user_cookies)

    assert ohne_sicht.status_code == 403, ohne_sicht.text
    assert ohne_sicht.json()["detail"] == "Berechtigung wurde entzogen"
    # Und die Zeile ist wirklich noch da — der 403 ist keine hoefliche Umschrift
    # fuer "geloescht".
    assert db.get(AiActionProposal, proposal_id) is not None


def test_a_wer_loeschen_darf_sieht_seinen_loeschvorschlag_auch_danach(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Welches Recht gilt, steht in der Werkzeugtabelle — nicht in einer Konstante.

    Nach dem Loeschen traegt der Vorschlag kein `server_id` mehr, es gibt also
    keinen Server, gegen den sich `server.view` pruefen liesse. Frueher verlangte
    dieser Zweig fest `servers.create`: wer loeschen darf, aber nicht erstellen,
    haette seinen eigenen, gerade erledigten Loeschvorschlag nicht mehr sehen
    duerfen. Dieselbe Grenze zieht `_require_tool_permission` beim Vorschlagen
    laengst richtig — zwei Orte mit zwei Antworten sind genau die Abweichung,
    die niemand bemerkt.

    Deshalb hier ausdruecklich ein Benutzer mit `servers.delete` und **ohne**
    `servers.create`.
    """
    _loeschbar(monkeypatch)
    role = Role(name=f"ai-loescher-{regular_user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add_all([
        RolePermission(role_id=role.id, permission_key="ai.chat.use"),
        RolePermission(role_id=role.id, permission_key="servers.delete"),
    ])
    set_user_roles(db, regular_user, [role.id])
    server = _server(db, regular_user, tmp_path)
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view",
    ))
    db.commit()
    conversation = _panel_unterhaltung(db, regular_user)
    proposal = _loeschvorschlag(db, regular_user, conversation, server)
    db.commit()
    proposal_id = proposal.id

    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=proposal_id, user=regular_user
    )
    ai_proposal_service.execute_proposal(
        db, proposal_id=proposal_id, user=regular_user, confirmation_token=token
    )

    danach = client.get(f"/api/ai/actions/{proposal_id}", cookies=user_cookies)

    assert danach.status_code == 200, danach.text
    assert danach.json()["status"] == "succeeded"
