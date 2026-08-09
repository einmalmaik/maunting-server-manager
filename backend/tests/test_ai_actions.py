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


def test_read_config_withholds_revision_for_redacted_or_truncated_view(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    """Ohne vollstaendige Sicht gibt es keine Revision — und damit keinen Write.

    Die Revision ist die Zusage "du hast den aktuellen Stand vollstaendig
    gesehen". Redigierte oder gekuerzte Ansichten sind das nicht.
    """
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)

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
    assert redacted_view["revision"] is None
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
    assert truncated_view["revision"] is None
    assert len(truncated_view["content"]) == ai_action_service.MAX_READ_CONFIG_CHARS

    plain_config = Path(server.install_dir) / "plain.cfg"
    plain_config.write_text("port=2302\n", encoding="utf-8")
    full_view = ai_action_service.execute_read_tool(
        db,
        user=owner_user,
        tool_name="read_config",
        arguments={"server_id": server.id, "path": "plain.cfg"},
    )
    assert full_view["editable"] is True
    assert full_view["revision"] == content_revision(plain_config.read_bytes())


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
