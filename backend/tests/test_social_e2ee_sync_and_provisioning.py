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
from dependencies import get_current_user, verify_csrf
from models import (
    User,
    DirectChat,
    ChatGroup,
    ChatGroupMember,
    E2eeBlindEnvelope,
)
from services import e2ee_device_service
from services.auth_service import AuthService
from services.social_service import SocialService
from services.panel_settings_service import PanelSettingsService
from schemas.social import validate_rsa_public_key_jwk


def _valid_test_envelope(payload_bytes: bytes = b"test-secret-payload-bytes-12345678") -> str:
    """Creates a format-compliant sv-e2ee-v1 envelope with random IV and tag."""
    raw = os.urandom(12) + payload_bytes + os.urandom(16)
    return "sv-e2ee-group-v1:" + base64.b64encode(raw).decode("ascii")


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


# ── Test 6: Der Server erzeugt kein Schlüsselmaterial ──
def test_registrierung_legt_keinen_e2ee_schluessel_an(db: Session):
    """Gegenprobe zum Fehler, den `20260917_01_e2ee_geraete` beseitigt hat.

    Vorher erzeugte `AuthService.generate_e2ee_public_key_jwk` bei Registrierung
    und bei jedem Login ein RSA-Paar, warf den privaten Teil weg und
    veröffentlichte den öffentlichen. Jedes Konto hatte damit einen Schlüssel,
    zu dem es nirgends einen privaten gab: wer dagegen verschlüsselte, schrieb
    in ein schwarzes Loch — die Nachricht sah gesendet aus und war für immer
    unlesbar.

    Der Test prüft die Abwesenheit, weil genau die schwer zu bemerken ist: ein
    wieder eingeführter Kontoschlüssel fiele im Betrieb erst auf, wenn jemand
    eine unlesbare Nachricht bekommt.
    """
    neuer = AuthService.create_user(db, "ohne_schluessel", "ohne@test.de", "SecurePassword123!")
    db.refresh(neuer)

    assert not hasattr(neuer, "social_e2ee_public_key")
    assert not hasattr(neuer, "social_e2ee_wrapped_keyring")
    assert not hasattr(AuthService, "generate_e2ee_public_key_jwk")
    assert not hasattr(AuthService, "ensure_user_e2ee_key")
    # Kein Gerät hat sich gemeldet, also gibt es auch keine Zustelladresse.
    assert e2ee_device_service.geraete(db, neuer.id) == []


# ── Test 7: Ein Gerät veröffentlicht seinen eigenen Schlüssel ──
def test_geraet_veroeffentlicht_und_frischt_auf(client: TestClient, db: Session, owner_user: User):
    """Dasselbe Gerät meldet sich bei jedem Start — und bleibt eine Zeile."""
    app.dependency_overrides[get_current_user] = lambda: owner_user
    app.dependency_overrides[verify_csrf] = lambda: None
    try:
        erst = client.put("/api/social/e2ee/devices/self", json={
            "device_id": "a1b2c3d4e5f60718",
            "public_key": _valid_rsa_jwk("A"),
            "label": "Arbeitsrechner",
        })
        assert erst.status_code == 200, erst.text
        assert erst.json()["device_id"] == "a1b2c3d4e5f60718"

        # Neuer Schlüssel unter derselben Kennung: das Gerät hat seine lokale
        # Ablage verloren. Es muss sich neu melden können, sonst käme es nie
        # wieder in ein Gespräch hinein.
        zweit = client.put("/api/social/e2ee/devices/self", json={
            "device_id": "a1b2c3d4e5f60718",
            "public_key": _valid_rsa_jwk("B"),
            "label": "Arbeitsrechner",
        })
        assert zweit.status_code == 200, zweit.text

        liste = e2ee_device_service.geraete(db, owner_user.id)
        assert len(liste) == 1
        assert liste[0]["public_key"] == _valid_rsa_jwk("B")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(verify_csrf, None)


def test_geraetekennung_mit_punkt_wird_abgewiesen(client: TestClient, owner_user: User):
    """Der Punkt trennt die Felder in `sv-e2ee-dr-v1:<von>.<fuer>.<chiffre>`.

    Eine Kennung, die ihn enthielte, zerlegte den Umschlag beim Empfänger — und
    zwar so, dass die Nachricht nicht scheitert, sondern falsch geroutet wird.
    """
    app.dependency_overrides[get_current_user] = lambda: owner_user
    app.dependency_overrides[verify_csrf] = lambda: None
    try:
        res = client.put("/api/social/e2ee/devices/self", json={
            "device_id": "aaaa.bbbbcccc",
            "public_key": _valid_rsa_jwk("C"),
        })
        assert res.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(verify_csrf, None)


def test_privater_schluessel_im_geraeteeintrag_wird_abgewiesen(client: TestClient, owner_user: User):
    """Ein Client, der versehentlich seinen privaten Teil hochlädt, wird gestoppt."""
    app.dependency_overrides[get_current_user] = lambda: owner_user
    app.dependency_overrides[verify_csrf] = lambda: None
    try:
        mit_privatteil = json.dumps({
            "kty": "RSA", "n": "D" * 350, "e": "AQAB", "d": "geheim",
            "alg": "RSA-OAEP", "use": "enc",
        })
        res = client.put("/api/social/e2ee/devices/self", json={
            "device_id": "d1d2d3d4d5d6d7d8",
            "public_key": mit_privatteil,
        })
        assert res.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(verify_csrf, None)


def test_geraetedeckel_verdraengt_das_laengst_stille(db: Session, owner_user: User):
    """Jedes Gerät kostet alle Gegenstellen eine Kopie je Nachricht.

    Verdrängt wird nach `last_seen_at`, nicht nach Anlagedatum: das älteste
    Gerät ist oft das meistgenutzte, das längst stille dagegen ein Browser, den
    niemand mehr öffnet.
    """
    from datetime import datetime, timedelta, timezone
    from models import UserE2eeDevice

    for i in range(e2ee_device_service.MAX_GERAETE):
        e2ee_device_service.veroeffentlichen(
            db, owner_user, device_id=f"geraet{i:012d}", public_key_jwk=_valid_rsa_jwk("A")
        )
    # Das erste Gerät war zuletzt vor einem Jahr da.
    stilles = (
        db.query(UserE2eeDevice)
        .filter_by(user_id=owner_user.id, device_id="geraet000000000000")
        .first()
    )
    stilles.last_seen_at = datetime.now(timezone.utc) - timedelta(days=365)
    db.commit()

    e2ee_device_service.veroeffentlichen(
        db, owner_user, device_id="neuesgeraet00000", public_key_jwk=_valid_rsa_jwk("A")
    )

    kennungen = {g["device_id"] for g in e2ee_device_service.geraete(db, owner_user.id)}
    assert len(kennungen) == e2ee_device_service.MAX_GERAETE
    assert "neuesgeraet00000" in kennungen
    assert "geraet000000000000" not in kennungen


def test_fremdes_geraet_laesst_sich_nicht_vergessen(db: Session, owner_user: User, regular_user: User):
    """Ohne den `user_id`-Filter liesse sich mit einer geratenen Kennung ein
    fremdes Gerät aus dem Verkehr ziehen und damit ein Gespräch stilllegen."""
    e2ee_device_service.veroeffentlichen(
        db, regular_user, device_id="fremdgeraet00001", public_key_jwk=_valid_rsa_jwk("A")
    )

    assert e2ee_device_service.vergessen(db, owner_user, "fremdgeraet00001") is False
    assert len(e2ee_device_service.geraete(db, regular_user.id)) == 1
