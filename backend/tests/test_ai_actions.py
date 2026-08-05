"""Security-Invarianten fuer bestaetigungspflichtige AI-Aktionen."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

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
from services import ai_action_service
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


def test_config_proposal_encrypts_payload_and_redacts_old_secret(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, owner_user, tmp_path)
    config = Path(server.install_dir) / "server.cfg"
    config.write_text("api_key=old-secret-value\nport=2302\n", encoding="utf-8")
    conversation = _conversation(db, owner_user, server)
    proposal = ai_action_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_config_update",
        arguments={
            "path": "server.cfg",
            "content": "port=2402\n",
            "expected_revision": content_revision(config.read_bytes()),
        },
        correlation_id=str(uuid4()),
    )
    db.commit()

    preview = json.loads(proposal.preview_json)
    assert "old-secret-value" not in proposal.payload_encrypted
    assert "old-secret-value" not in json.dumps(preview)
    assert "[REDACTED]" in preview["diff"]
    decrypted = json.loads(DisClient.decrypt(
        proposal.payload_encrypted,
        aad=f"msm:ai:action-proposal:v1:{proposal.id}",
    ))
    assert decrypted["content"] == "port=2402\n"
    audit = db.query(AuditLog).filter(AuditLog.action == "ai.action.proposed").one()
    assert "port=2402" not in (audit.details or "")


def test_unregistered_tool_is_rejected_without_persistence(
    db: Session, owner_user: User, tmp_path: Path
) -> None:
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)

    try:
        ai_action_service.create_proposal(
            db,
            user=owner_user,
            conversation=conversation,
            tool_name="execute_shell",
            arguments={"command": "ignored"},
            correlation_id=str(uuid4()),
        )
    except ai_action_service.AiActionValidationError:
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
        conversation=conversation,
        tool_name="read_server_logs",
        arguments={"lines": 50},
    )

    assert result["redacted"] is True
    assert "provider-secret-value" not in result["content"]
    assert "[REDACTED]" in result["content"]
    try:
        ai_action_service.execute_read_tool(
            db,
            user=owner_user,
            conversation=conversation,
            tool_name="read_server_logs",
            arguments={"lines": 201},
        )
    except ai_action_service.AiActionValidationError:
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
    proposal = ai_action_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_config_update",
        arguments={
            "path": "server.cfg",
            "content": "port=2402\n",
            "expected_revision": content_revision(config.read_bytes()),
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
    proposal = ai_action_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_config_update",
        arguments={
            "path": "server.cfg",
            "content": "port=2402\n",
            "expected_revision": content_revision(config.read_bytes()),
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
    proposal = ai_action_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments={},
        correlation_id=str(uuid4()),
    )
    db.commit()
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    proposal, token = ai_action_service.confirm_proposal(
        db, proposal_id=proposal.id, user=owner_user, now=past
    )

    try:
        ai_action_service.execute_proposal(
            db,
            proposal_id=proposal.id,
            user=owner_user,
            confirmation_token=token,
        )
    except ai_action_service.AiActionStateError as exc:
        assert exc.code == "AI_ACTION_CONFIRMATION_EXPIRED"
    else:
        raise AssertionError("expired confirmation was accepted")
    db.refresh(proposal)
    assert proposal.status == "expired"


def test_startup_recovery_fails_closed(db: Session, owner_user: User, tmp_path: Path) -> None:
    server = _server(db, owner_user, tmp_path)
    conversation = _conversation(db, owner_user, server)
    proposal = ai_action_service.create_proposal(
        db,
        user=owner_user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments={},
        correlation_id=str(uuid4()),
    )
    proposal.status = "executing"
    db.commit()

    assert ai_action_service.reconcile_interrupted_actions(db) == 1
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
    proposal = ai_action_service.create_proposal(
        db,
        user=regular_user,
        conversation=conversation,
        tool_name="propose_backup",
        arguments={},
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
