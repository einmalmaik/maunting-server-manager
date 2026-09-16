from __future__ import annotations

import base64
import json
import os
import hashlib
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from main import app
from dependencies import get_current_user
from models import (
    User,
    DirectChat,
    ChatGroup,
    ChatGroupMember,
    E2eeBlindEnvelope,
)
from services.auth_service import AuthService
from services.social_service import SocialService
from services.panel_settings_service import PanelSettingsService
from schemas.social import validate_rsa_public_key_jwk


def _valid_test_envelope(payload_bytes: bytes = b"test-secret-payload-bytes-12345678") -> str:
    """Creates a format-compliant sv-e2ee-v1 envelope with random IV and tag."""
    raw = os.urandom(12) + payload_bytes + os.urandom(16)
    return "sv-e2ee-v1:" + base64.b64encode(raw).decode("ascii")


def _valid_rsa_jwk(marker: str = "A") -> str:
    """Creates a valid RSA-OAEP public key JWK passing validate_rsa_public_key_jwk."""
    return json.dumps({
        "kty": "RSA",
        "n": marker * 350,
        "e": "AQAB",
        "alg": "RSA-OAEP",
        "use": "enc",
    })


# ── Test 1: Empty State ──
def test_e2ee_sync_empty_state(client: TestClient, db: Session, owner_user: User):
    """GET /api/social/e2ee/sync returns empty list for user without messages."""
    app.dependency_overrides[get_current_user] = lambda: owner_user
    try:
        res = client.get("/api/social/e2ee/sync")
        assert res.status_code == 200
        data = res.json()
        assert "mailboxes" in data
        assert data["mailboxes"] == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Test 2: Unauthenticated and Operator Disabled Guard ──
def test_e2ee_sync_unauthenticated_and_disabled(client: TestClient, db: Session, owner_user: User):
    """Sync endpoint enforces authentication and respects operator toggle."""
    # 1. Unauthenticated request
    res_unauth = client.get("/api/social/e2ee/sync")
    assert res_unauth.status_code in (401, 403)

    # 2. Authenticated but social system disabled globally
    app.dependency_overrides[get_current_user] = lambda: owner_user
    try:
        PanelSettingsService.set("social_enabled", "false", db)
        res_disabled = client.get("/api/social/e2ee/sync")
        assert res_disabled.status_code == 403
        assert "deaktiviert" in res_disabled.text
    finally:
        PanelSettingsService.set("social_enabled", "true", db)
        app.dependency_overrides.pop(get_current_user, None)


# ── Test 3: Direct Chat Delta Filtering & since_id ──
def test_e2ee_sync_direct_chat_mailbox_filtering_and_delta(
    client: TestClient, db: Session, owner_user: User, regular_user: User
):
    """Sync filters by since_id and returns accurate max_envelope_id and unread_count."""
    # Establish DirectChat between owner and regular
    min_id, max_id = min(owner_user.id, regular_user.id), max(owner_user.id, regular_user.id)
    mailbox_id = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)
    chat = DirectChat(
        user_a_id=min_id,
        user_b_id=max_id,
        blind_mailbox_id=mailbox_id,
        initiated_by_user_id=owner_user.id,
    )
    db.add(chat)
    db.commit()

    # Seed 3 envelopes into this mailbox
    env1 = E2eeBlindEnvelope(blind_mailbox_id=mailbox_id, ciphertext_envelope=_valid_test_envelope(b"m1"), client_uuid=str(uuid4()))
    env2 = E2eeBlindEnvelope(blind_mailbox_id=mailbox_id, ciphertext_envelope=_valid_test_envelope(b"m2"), client_uuid=str(uuid4()))
    env3 = E2eeBlindEnvelope(blind_mailbox_id=mailbox_id, ciphertext_envelope=_valid_test_envelope(b"m3"), client_uuid=str(uuid4()))
    db.add_all([env1, env2, env3])
    db.commit()

    max_id_val = env3.id

    app.dependency_overrides[get_current_user] = lambda: owner_user
    try:
        # Full sync (since_id=0)
        res0 = client.get("/api/social/e2ee/sync?since_id=0")
        assert res0.status_code == 200
        boxes0 = res0.json()["mailboxes"]
        assert len(boxes0) == 1
        assert boxes0[0]["blind_mailbox_id"] == mailbox_id
        assert boxes0[0]["max_envelope_id"] == max_id_val
        assert boxes0[0]["unread_count"] == 3

        # Delta sync (since_id=env2.id) -> only env3 is unread
        res_delta = client.get(f"/api/social/e2ee/sync?since_id={env2.id}")
        assert res_delta.status_code == 200
        boxes_delta = res_delta.json()["mailboxes"]
        assert len(boxes_delta) == 1
        assert boxes_delta[0]["unread_count"] == 1
        assert boxes_delta[0]["max_envelope_id"] == max_id_val

        # Up-to-date sync (since_id=env3.id) -> 0 unread messages
        res_current = client.get(f"/api/social/e2ee/sync?since_id={env3.id}")
        assert res_current.status_code == 200
        assert res_current.json()["mailboxes"] == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Test 4: Participant Security Boundary & Zero-Knowledge Isolation ──
def test_e2ee_sync_participant_security_boundary_and_zero_knowledge(client: TestClient, db: Session):
    """Users only see mailboxes where they are authorized participants; third parties see nothing."""
    alice = AuthService.create_user(db, "alice_sec", "alice_sec@test.de", "Pass1234!")
    bob = AuthService.create_user(db, "bob_sec", "bob_sec@test.de", "Pass1234!")
    charlie = AuthService.create_user(db, "charlie_sec", "charlie_sec@test.de", "Pass1234!")
    dave = AuthService.create_user(db, "dave_sec", "dave_sec@test.de", "Pass1234!")

    # Chat AB
    box_ab = SocialService.derive_blind_mailbox_id(alice.id, bob.id)
    chat_ab = DirectChat(user_a_id=min(alice.id, bob.id), user_b_id=max(alice.id, bob.id), blind_mailbox_id=box_ab, initiated_by_user_id=alice.id)
    # Chat BC
    box_bc = SocialService.derive_blind_mailbox_id(bob.id, charlie.id)
    chat_bc = DirectChat(user_a_id=min(bob.id, charlie.id), user_b_id=max(bob.id, charlie.id), blind_mailbox_id=box_bc, initiated_by_user_id=bob.id)
    db.add_all([chat_ab, chat_bc])
    db.commit()

    # Envelopes in both
    db.add(E2eeBlindEnvelope(blind_mailbox_id=box_ab, ciphertext_envelope=_valid_test_envelope(), client_uuid=str(uuid4())))
    db.add(E2eeBlindEnvelope(blind_mailbox_id=box_bc, ciphertext_envelope=_valid_test_envelope(), client_uuid=str(uuid4())))
    db.commit()

    # 1. Alice must see box_ab, but NEVER box_bc
    app.dependency_overrides[get_current_user] = lambda: alice
    try:
        res_alice = client.get("/api/social/e2ee/sync")
        mids_alice = [m["blind_mailbox_id"] for m in res_alice.json()["mailboxes"]]
        assert box_ab in mids_alice
        assert box_bc not in mids_alice
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # 2. Charlie must see box_bc, but NEVER box_ab
    app.dependency_overrides[get_current_user] = lambda: charlie
    try:
        res_charlie = client.get("/api/social/e2ee/sync")
        mids_charlie = [m["blind_mailbox_id"] for m in res_charlie.json()["mailboxes"]]
        assert box_bc in mids_charlie
        assert box_ab not in mids_charlie
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # 3. Bob is in both, so he must see BOTH
    app.dependency_overrides[get_current_user] = lambda: bob
    try:
        res_bob = client.get("/api/social/e2ee/sync")
        mids_bob = [m["blind_mailbox_id"] for m in res_bob.json()["mailboxes"]]
        assert box_ab in mids_bob
        assert box_bc in mids_bob
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # 4. Dave is an outsider and must see NOTHING
    app.dependency_overrides[get_current_user] = lambda: dave
    try:
        res_dave = client.get("/api/social/e2ee/sync")
        assert res_dave.json()["mailboxes"] == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Test 5: Group Chat Mailbox Sync & Revocation ──
def test_e2ee_sync_group_chat_mailbox(client: TestClient, db: Session, owner_user: User, regular_user: User):
    """Group chat mailboxes are returned to active members and excluded upon kick/leave."""
    group = SocialService.create_group(db, user=owner_user, name="Sync Test Guild")
    SocialService.join_group_by_invite_code(db, regular_user, group.invite_code)

    group_box = hashlib.sha256(f"msm:group:{group.id}".encode("utf-8")).hexdigest()
    db.add(E2eeBlindEnvelope(blind_mailbox_id=group_box, ciphertext_envelope=_valid_test_envelope(), client_uuid=str(uuid4())))
    db.commit()

    # Regular user is member -> sees group mailbox
    app.dependency_overrides[get_current_user] = lambda: regular_user
    try:
        res1 = client.get("/api/social/e2ee/sync")
        mids = [m["blind_mailbox_id"] for m in res1.json()["mailboxes"]]
        assert group_box in mids
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # Owner kicks regular user
    SocialService.kick_group_member(db, group_id=group.id, target_user_id=regular_user.id, caller=owner_user)

    # Regular user no longer member -> group mailbox MUST disappear from sync
    app.dependency_overrides[get_current_user] = lambda: regular_user
    try:
        res2 = client.get("/api/social/e2ee/sync")
        mids2 = [m["blind_mailbox_id"] for m in res2.json()["mailboxes"]]
        assert group_box not in mids2
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Test 6: Automated Key Provisioning on User Creation ──
def test_e2ee_automated_key_provisioning_on_user_creation(db: Session):
    """Newly created user automatically receives a valid, secure RSA-2048 E2EE public key."""
    new_user = AuthService.create_user(db, "auto_key_user", "auto_key@test.de", "SecurePassword123!")
    db.refresh(new_user)

    # Public key must be non-null and valid JWK
    assert new_user.social_e2ee_public_key is not None
    jwk_dict = validate_rsa_public_key_jwk(new_user.social_e2ee_public_key)
    assert jwk_dict["kty"] == "RSA"
    assert len(jwk_dict["n"]) >= 300
    assert jwk_dict["e"] == "AQAB"
    assert "d" not in jwk_dict  # Absolute Zero-Leak


# ── Test 7: Automated Key Provisioning on Login (Lazy Backfill) ──
def test_e2ee_automated_key_provisioning_on_login_lazy(client: TestClient, db: Session):
    """A legacy user with NULL public key gets auto-provisioned upon login."""
    legacy_user = User(
        username="legacy_user",
        email="legacy@test.de",
        password_hash=AuthService.hash_password("LegacyPass123!"),
        is_active=True,
        email_verified=True,
        social_e2ee_public_key=None,
    )
    db.add(legacy_user)
    db.commit()
    db.refresh(legacy_user)
    assert legacy_user.social_e2ee_public_key is None

    # Login
    res = client.post("/api/auth/login", json={
        "username": "legacy_user",
        "password": "LegacyPass123!",
        "otp_code": None,
    })
    assert res.status_code == 200

    db.refresh(legacy_user)
    assert legacy_user.social_e2ee_public_key is not None
    validate_rsa_public_key_jwk(legacy_user.social_e2ee_public_key)

    # Public key lookup endpoint returns 200 with the provisioned key
    app.dependency_overrides[get_current_user] = lambda: legacy_user
    try:
        key_res = client.get(f"/api/social/e2ee/public-key/{legacy_user.id}")
        assert key_res.status_code == 200
        assert key_res.json()["public_key"] == legacy_user.social_e2ee_public_key
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Test 8: Non-Regression Guarantee for Existing Keys ──
def test_e2ee_key_provisioning_non_regression_existing_keys_preserved(client: TestClient, db: Session):
    """Existing public keys and wrapped keyrings are NEVER overwritten by login or provisioning."""
    existing_pub = _valid_rsa_jwk("Z")
    existing_user = AuthService.create_user(db, "custom_key_user", "custom@test.de", "CustomPass123!")
    existing_user.social_e2ee_public_key = existing_pub
    existing_user.social_e2ee_wrapped_keyring = "sv-e2ee-keyring-v1:" + base64.b64encode(b"\x11" * 16 + b"\x22" * 12 + b"keyring" + b"\x33" * 16).decode("ascii")
    existing_user.social_e2ee_keyring_version = 2
    db.commit()

    # Login multiple times
    for _ in range(2):
        res = client.post("/api/auth/login", json={
            "username": "custom_key_user",
            "password": "CustomPass123!",
            "otp_code": None,
        })
        assert res.status_code == 200

    db.refresh(existing_user)
    # The existing public key and keyring must be untouched!
    assert existing_user.social_e2ee_public_key == existing_pub
    assert existing_user.social_e2ee_keyring_version == 2
    assert "keyring" in existing_user.social_e2ee_wrapped_keyring
