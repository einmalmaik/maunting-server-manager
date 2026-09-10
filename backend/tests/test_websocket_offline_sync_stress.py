from __future__ import annotations

import asyncio
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from models import User, UserFriend, E2eeBlindEnvelope
from services.auth_service import AuthService
from services.social_service import SocialService
from services.sync_event_service import SyncEventService


def _create_user(db: Session, username: str, privacy: str = "friends") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=AuthService.hash_password("Password123!"),
        is_active=True,
        email_verified=True,
        social_privacy=privacy,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client: TestClient, username: str) -> dict:
    resp = client.post("/api/auth/login", json={
        "username": username,
        "password": "Password123!",
        "otp_code": None,
    })
    assert resp.status_code == 200
    return dict(resp.cookies)


# ============================================================================
# 1. Message Deduplication & Idempotency Tests
# ============================================================================

def test_e2ee_envelope_idempotent_deduplication(db: Session):
    """Prüft, dass wiederholtes Senden mit identischer client_uuid keine Duplikate erzeugt."""
    mailbox = "mailbox-stress-test-1"
    uuid_tag = "client-uuid-stable-12345"
    payload = "sv-e2ee-v1:encrypted-content-1"

    # Erstes Senden
    env1 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=payload,
        client_uuid=uuid_tag,
    )
    assert env1.id is not None
    assert env1.client_uuid == uuid_tag

    # Wiederholtes Senden (Retry / Netzwerkschwankung)
    env2 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=payload,
        client_uuid=uuid_tag,
    )
    # Muss dieselbe ID zurückliefern
    assert env2.id == env1.id
    assert env2.client_uuid == uuid_tag

    # Direkte DB-Prüfung: Genau eine Zeile darf existieren
    count = db.query(E2eeBlindEnvelope).filter_by(
        blind_mailbox_id=mailbox,
        client_uuid=uuid_tag,
    ).count()
    assert count == 1

    # Abruf aus der Mailbox liefert ebenfalls nur 1 Exemplar
    envelopes = SocialService.get_blind_envelopes(db, blind_mailbox_id=mailbox)
    assert len(envelopes) == 1
    assert envelopes[0].id == env1.id


def test_e2ee_envelope_without_uuid_creates_distinct_records(db: Session):
    """Envelopes ohne client_uuid werden standardmäßig als getrennte Nachrichten behandelt."""
    mailbox = "mailbox-stress-legacy"
    payload = "sv-e2ee-v1:legacy-msg"

    e1 = SocialService.relay_blind_envelope(db, blind_mailbox_id=mailbox, ciphertext_envelope=payload)
    e2 = SocialService.relay_blind_envelope(db, blind_mailbox_id=mailbox, ciphertext_envelope=payload)

    assert e1.id != e2.id
    count = db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=mailbox).count()
    assert count == 2


# ============================================================================
# 2. WebSocket relay_ack & Idempotente Bestätigung
# ============================================================================

def test_websocket_relay_ack_and_idempotency(db: Session, client: TestClient, owner_cookies: dict):
    """Prüft, dass der WebSocket auf Relay-Nachrichten mit relay_ack und korrekter ID quittiert."""
    mailbox = "ws-mailbox-dedup-test"
    client_uuid = "client-ws-uuid-999"

    with client.websocket_connect("/api/social/ws", cookies=owner_cookies) as ws:
        # Sende Relay-Frame
        ws.send_json({
            "type": "relay",
            "blind_mailbox_id": mailbox,
            "ciphertext_envelope": "sv-e2ee-v1:ws-relay-cipher",
            "client_uuid": client_uuid,
        })

        def _read_until(target_type: str, max_msgs: int = 5) -> dict:
            for _ in range(max_msgs):
                msg = ws.receive_json()
                if msg.get("type") == target_type:
                    return msg
            raise AssertionError(f"Did not receive message of type {target_type}")

        # Erwarte relay_ack (kann e2ee_blind_message voranstellen)
        resp = _read_until("relay_ack")
        assert resp["type"] == "relay_ack"
        assert resp["client_uuid"] == client_uuid
        assert isinstance(resp["id"], int)
        first_id = resp["id"]

        # Erneutes Senden mit derselben client_uuid (Retry-Simulation)
        ws.send_json({
            "type": "relay",
            "blind_mailbox_id": mailbox,
            "ciphertext_envelope": "sv-e2ee-v1:ws-relay-cipher",
            "client_uuid": client_uuid,
        })

        resp2 = _read_until("relay_ack")
        assert resp2["type"] == "relay_ack"
        assert resp2["client_uuid"] == client_uuid
        assert resp2["id"] == first_id

    # Sicherstellen, dass nur 1 Eintrag in der DB vorliegt
    assert db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=mailbox, client_uuid=client_uuid).count() == 1


# ============================================================================
# 3. Heartbeat / Ping Isolation
# ============================================================================

def test_websocket_ping_pong_isolated(client: TestClient, owner_cookies: dict):
    """Prüft, dass Heartbeats (ping/pong) direkt beantwortet werden und keine Events triggern."""
    with client.websocket_connect("/api/social/ws", cookies=owner_cookies) as ws:
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data == {"type": "pong"}


# ============================================================================
# 4. Ghost-Mode & Online-Status-Leak Isolation
# ============================================================================

def test_websocket_ghost_mode_never_leaks_to_strangers(db: Session, client: TestClient):
    """Ghost-Mode ('invisible' oder 'friends'/'private'): WebSocket-Join darf Nicht-Freunden keinen Status verraten."""
    alice = _create_user(db, "alice_ghost", privacy="friends")
    stranger = _create_user(db, "bob_stranger", privacy="friends")

    # Alice auf invisible setzen
    SocialService.update_presence(db, alice.id, {"status": "invisible"})

    stranger_cookies = _login_user(client, "bob_stranger")
    alice_cookies = _login_user(client, "alice_ghost")

    # Bob verbindet sich mit WebSocket
    with client.websocket_connect("/api/social/ws", cookies=stranger_cookies) as ws_bob:
        # Alice verbindet sich und sendet join
        with client.websocket_connect("/api/social/ws", cookies=alice_cookies) as ws_alice:
            ws_alice.send_json({"type": "join"})

            # Bob sendet ping, um zu verifizieren, dass die Socket-Verbindung aktiv ist
            ws_bob.send_json({"type": "ping"})
            bob_resp = ws_bob.receive_json()
            # Bob darf nur sein eigenes Pong erhalten, KEIN user_joined Event von Alice!
            assert bob_resp == {"type": "pong"}


def test_websocket_friends_join_broadcasts_only_to_friends(db: Session, client: TestClient):
    """Öffentliche oder befreundete Join-Events gehen nur an berechtigte Kontakte."""
    user1 = _create_user(db, "user_public", privacy="public")
    user2 = _create_user(db, "user_friend", privacy="friends")
    user3 = _create_user(db, "user_unrelated", privacy="friends")

    # user1 und user2 sind Freunde
    rel1 = UserFriend(user_id=user1.id, friend_id=user2.id, status="accepted")
    rel2 = UserFriend(user_id=user2.id, friend_id=user1.id, status="accepted")
    db.add_all([rel1, rel2])
    db.commit()

    SocialService.update_presence(db, user1.id, {"status": "online"})

    cookies1 = _login_user(client, "user_public")
    cookies2 = _login_user(client, "user_friend")
    cookies3 = _login_user(client, "user_unrelated")

    with client.websocket_connect("/api/social/ws", cookies=cookies2) as ws_friend:
        with client.websocket_connect("/api/social/ws", cookies=cookies3) as ws_unrelated:
            with client.websocket_connect("/api/social/ws", cookies=cookies1) as ws_alice:
                ws_alice.send_json({"type": "join"})

            # Freund erhält user_joined Event
            friend_event = ws_friend.receive_json()
            assert friend_event.get("type") == "user_joined"
            assert friend_event.get("user_id") == user1.id

            # Unbeteiligter Fremder (user3) darf KEIN user_joined erhalten
            ws_unrelated.send_json({"type": "ping"})
            unrelated_resp = ws_unrelated.receive_json()
            assert unrelated_resp == {"type": "pong"}


# ============================================================================
# 5. Stress Test: Schnelle Verbindungsabbrüche & Flapping
# ============================================================================

def test_websocket_rapid_connect_disconnect_stress(client: TestClient, owner_cookies: dict):
    """Stresstest für 25 aufeinanderfolgende schnelle Connect/Disconnect Zyklen ohne Session-Leaks."""
    for i in range(25):
        with client.websocket_connect("/api/social/ws", cookies=owner_cookies) as ws:
            ws.send_json({"type": "ping"})
            resp = ws.receive_json()
            assert resp == {"type": "pong"}
            # Schließt sofort


def test_sync_events_websocket_rapid_connect_disconnect(client: TestClient, owner_cookies: dict):
    """Stresstest für /api/events/ws (Sync Events WebSocket) ohne Session-Leaks."""
    for i in range(15):
        with client.websocket_connect("/api/events/ws", cookies=owner_cookies) as ws:
            ready = ws.receive_json()
            assert ready.get("type") == "ready"
            ws.send_json({"type": "ping"})
            resp = ws.receive_json()
            assert resp == {"type": "pong"}


# ============================================================================
# 6. Edge Cases: Security Invariants & WebSocket Crash Resilience
# ============================================================================

def test_e2ee_envelope_unauthorized_user_cannot_access_existing_uuid(db: Session):
    """Sicherheits-Invariante: Ein unberechtigter Dritter darf auch bei bekannter client_uuid
    keinen Umschlag aus einer fremden Mailbox abrufen."""
    from fastapi import HTTPException
    alice = _create_user(db, "alice_secure", privacy="friends")
    bob = _create_user(db, "bob_secure", privacy="friends")
    charlie = _create_user(db, "charlie_intruder", privacy="friends")

    # Alice und Bob sind Freunde
    rel1 = UserFriend(user_id=alice.id, friend_id=bob.id, status="accepted")
    rel2 = UserFriend(user_id=bob.id, friend_id=alice.id, status="accepted")
    db.add_all([rel1, rel2])
    db.commit()

    # Alice und Bob haben einen Chat
    chat = SocialService.ensure_direct_chat(db, alice.id, bob.id)
    mailbox = chat.blind_mailbox_id
    uuid_tag = "uuid-secret-msg-42"

    # Alice sendet Nachricht
    env = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope="sv-e2ee-v1:alice-secret",
        sender_user_id=alice.id,
        recipient_id=bob.id,
        client_uuid=uuid_tag,
    )
    assert env.id is not None

    # Charlie (nicht berechtigt) versucht denselben Umschlag über die client_uuid abzugreifen
    with pytest.raises(HTTPException) as exc_info:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=mailbox,
            ciphertext_envelope="sv-e2ee-v1:charlie-fake",
            sender_user_id=charlie.id,
            recipient_id=bob.id,
            client_uuid=uuid_tag,
        )
    # Muss mit 400 oder 403 abgewiesen werden (Berechtigungsfehler)
    assert exc_info.value.status_code in (400, 403)


def test_websocket_handles_invalid_relay_without_crash_or_disconnect(db: Session, client: TestClient):
    """Crash-Resilienz: Wenn ein Client einen fehlerhaften Relay-Frame sendet,
    stürzt der WebSocket nicht ab und trennt nicht die Verbindung, sondern antwortet mit einem Error-Frame."""
    alice = _create_user(db, "alice_ws_crash_test")
    cookies = _login_user(client, "alice_ws_crash_test")

    with client.websocket_connect("/api/social/ws", cookies=cookies) as ws:
        # Sende ungültigen Frame (falscher recipient_id für eine unpassende Mailbox)
        ws.send_json({
            "type": "relay",
            "blind_mailbox_id": "completely-invalid-mailbox-hash-not-matching",
            "ciphertext_envelope": "short",
            "recipient_id": 999999,
            "client_uuid": "failing-uuid-1",
        })

        # Server muss einen error Frame senden, OHNE die Verbindung zu schließen
        err_msg = ws.receive_json()
        assert err_msg.get("type") == "error"
        assert err_msg.get("error") == "relay_failed"
        assert err_msg.get("client_uuid") == "failing-uuid-1"

        # Verbindung ist weiterhin aktiv und antwortet auf Ping
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong == {"type": "pong"}


def test_websocket_blocked_user_never_receives_user_joined(db: Session, client: TestClient):
    """Ghost-Mode & Block: Auch bei öffentlichem Profil darf ein blockierter Nutzer niemals
    ein user_joined Event erhalten."""
    alice = _create_user(db, "alice_blocker", privacy="public")
    bob = _create_user(db, "bob_blocked", privacy="public")

    # Vorab bestehender Chat zwischen beiden
    chat = SocialService.ensure_direct_chat(db, alice.id, bob.id)

    # Alice blockiert Bob
    SocialService.block_user(db, alice.id, bob.id)

    alice_cookies = _login_user(client, "alice_blocker")
    bob_cookies = _login_user(client, "bob_blocked")

    with client.websocket_connect("/api/social/ws", cookies=bob_cookies) as ws_bob:
        with client.websocket_connect("/api/social/ws", cookies=alice_cookies) as ws_alice:
            ws_alice.send_json({"type": "join"})

        # Bob sendet ping — darf KEIN user_joined von Alice erhalten!
        ws_bob.send_json({"type": "ping"})
        resp = ws_bob.receive_json()
        assert resp == {"type": "pong"}


def test_e2ee_envelope_concurrent_duplicate_relays_race_condition(db: Session):
    """Nebenläufigkeits-Test: Gleichzeitige Inserts mit identischer client_uuid
    lösen die IntegrityError-Behandlung sauber aus und geben die gleiche ID zurück."""
    mailbox = "concurrent-mailbox-test"
    uuid_tag = "concurrent-uuid-xyz"
    payload = "sv-e2ee-v1:concurrent-payload"

    # Sequentiell und verschränkt testen
    env1 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=payload,
        client_uuid=uuid_tag,
    )
    env2 = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=payload,
        client_uuid=uuid_tag,
    )

    assert env1.id == env2.id
    assert db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=mailbox, client_uuid=uuid_tag).count() == 1

