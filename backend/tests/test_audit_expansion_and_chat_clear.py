"""Tests fuer erweitertes Audit-Logging und die Bereinigung von Aktionskarten beim Chat-Leeren."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    AiMessage,
    AuditLog,
    Role,
    User,
)
from services import ai_chat_service, role_service
from services.auth_service import AuthService
from tests._totp import totp_now


def _csrf(cookies: dict) -> dict:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_clear_history_deletes_action_proposals(db: Session, owner_user: User):
    """Prueft, dass clear_history sowohl Nachrichten als auch Aktionskarten (AiActionProposal) restlos abräumt."""
    conv = ai_chat_service.get_or_create_primary_conversation(db, owner_user)

    msg = AiMessage(
        id=str(uuid4()),
        conversation_id=conv.id,
        role="user",
        content="Server neu starten",
        intern=False,
    )
    db.add(msg)
    db.flush()

    proposal = AiActionProposal(
        id=str(uuid4()),
        conversation_id=conv.id,
        user_id=owner_user.id,
        server_id=None,
        tool_name="propose_server_restart",
        proposal_type="write",
        preview_json="{}",
        correlation_id=str(uuid4()),
        status="proposed",
        payload_encrypted=AuthService.encrypt_secret(
            json.dumps({"server_id": 1}), aad=f"msm:ai:action:{conv.id}"
        ),
    )
    db.add(proposal)
    db.commit()

    assert db.query(AiMessage).filter(AiMessage.conversation_id == conv.id).count() == 1
    assert db.query(AiActionProposal).filter(AiActionProposal.conversation_id == conv.id).count() == 1

    removed = ai_chat_service.clear_history(db, conv)
    db.commit()

    assert removed == 1
    assert db.query(AiMessage).filter(AiMessage.conversation_id == conv.id).count() == 0
    assert db.query(AiActionProposal).filter(AiActionProposal.conversation_id == conv.id).count() == 0


def test_roles_audit_logging(client: TestClient, db: Session, owner_cookies: dict):
    """Prueft, dass Rollen-CRUD im Audit-Log erfasst wird."""
    headers = _csrf(owner_cookies)

    # 1. Role create
    resp = client.post(
        "/api/roles",
        json={"name": "test_audit_role", "description": "Test Rolle", "permissions": ["servers.read"]},
        cookies=owner_cookies,
        headers=headers,
    )
    assert resp.status_code == 201
    role_id = resp.json()["id"]

    audit_create = db.query(AuditLog).filter(AuditLog.action == "roles.create", AuditLog.target_id == str(role_id)).first()
    assert audit_create is not None
    assert "test_audit_role" in audit_create.details

    # 2. Role update
    resp = client.patch(
        f"/api/roles/{role_id}",
        json={"description": "Aktualisierte Beschreibung"},
        cookies=owner_cookies,
        headers=headers,
    )
    assert resp.status_code == 200

    audit_update = db.query(AuditLog).filter(AuditLog.action == "roles.update", AuditLog.target_id == str(role_id)).first()
    assert audit_update is not None

    # 3. Role delete
    resp = client.delete(f"/api/roles/{role_id}", cookies=owner_cookies, headers=headers)
    assert resp.status_code == 204

    audit_delete = db.query(AuditLog).filter(AuditLog.action == "roles.delete", AuditLog.target_id == str(role_id)).first()
    assert audit_delete is not None


def test_panel_settings_audit_logging(client: TestClient, db: Session, owner_cookies: dict):
    """Prueft, dass Panel-Settings-Updates auditiert werden."""
    headers = _csrf(owner_cookies)

    resp = client.post(
        "/api/settings",
        json={"default_language": "en", "time_format": "24h"},
        cookies=owner_cookies,
        headers=headers,
    )
    assert resp.status_code == 200

    audit_entry = db.query(AuditLog).filter(AuditLog.action == "panel.settings.update").order_by(AuditLog.id.desc()).first()
    assert audit_entry is not None
    assert "default_language" in audit_entry.details


def test_auth_password_and_2fa_audit_logging(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    """Prueft, dass Passwortaenderung und 2FA-Statusänderungen auditiert werden."""
    headers = _csrf(owner_cookies)

    # 1. Password change
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "OwnerPass123!", "new_password": "NewOwnerPass123!"},
        cookies=owner_cookies,
        headers=headers,
    )
    assert resp.status_code == 200

    audit_pw = db.query(AuditLog).filter(AuditLog.action == "auth.password.change", AuditLog.target_id == str(owner_user.id)).first()
    assert audit_pw is not None

    # Reset password back
    AuthService.reset_password(db, owner_user, "OwnerPass123!")

    # 2. 2FA setup & enable
    resp_setup = client.post("/api/auth/2fa/setup", cookies=owner_cookies, headers=headers)
    assert resp_setup.status_code == 200
    secret = resp_setup.json()["secret"]
    valid_code = totp_now(secret)

    resp_enable = client.post(f"/api/auth/2fa/enable?otp_code={valid_code}", cookies=owner_cookies, headers=headers)
    assert resp_enable.status_code == 200

    audit_2fa_en = db.query(AuditLog).filter(AuditLog.action == "auth.2fa.enable", AuditLog.target_id == str(owner_user.id)).first()
    assert audit_2fa_en is not None

    # 3. 2FA disable
    valid_code2 = totp_now(secret)
    resp_disable = client.post(f"/api/auth/2fa/disable?otp_code={valid_code2}", cookies=owner_cookies, headers=headers)
    assert resp_disable.status_code == 200

    audit_2fa_dis = db.query(AuditLog).filter(AuditLog.action == "auth.2fa.disable", AuditLog.target_id == str(owner_user.id)).first()
    assert audit_2fa_dis is not None
