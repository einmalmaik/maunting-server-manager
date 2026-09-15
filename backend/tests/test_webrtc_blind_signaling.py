"""Pytest test suite for WebRTC Blinded Signaling and Zero-Metadata Privacy Invariants.

Validates Milestone 1:
- Ephemeral in-memory rendezvous room management (BlindRendezvousManager).
- WebSocket handshake & bi-directional signaling relay (/api/social/webrtc/signal).
- Room capacity enforcement (max 2 peers, 3rd peer rejected with room_full / 1008).
- Ephemeral TTL expiration (120s) and automatic sweep cleanup.
- Graceful and abrupt peer disconnection handling (peer_left).
- Strict privacy invariants: ZERO database records, ZERO audit logs, ZERO caller-callee links.
- ICE server configuration endpoint (/api/social/webrtc/ice-servers).
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from database import Base
from models import AuditLog, E2eeBlindEnvelope, ChatMedia, User
from services.auth_service import AuthService
from services.webrtc_rendezvous_service import (
    BlindRendezvousManager,
    RoomFullError,
    SessionExpiredError,
    SessionTerminatedError,
)


# ============================================================================
# Test Fixtures & Helpers
# ============================================================================

@pytest.fixture(autouse=True)
def _reset_webrtc_rendezvous():
    """Isolates tests by clearing the static in-memory rendezvous manager state."""
    BlindRendezvousManager.clear_all_for_testing()
    yield
    BlindRendezvousManager.clear_all_for_testing()


def _create_user(db: Session, username: str) -> User:
    """Helper to create and persist an active, email-verified user."""
    user = User(
        username=username,
        email=f"{username}@test.local",
        password_hash=AuthService.hash_password("StrongTestPass123!"),
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_user(client: TestClient, username: str) -> dict[str, str]:
    """Helper to authenticate user via HTTP and retrieve session cookies."""
    resp = client.post("/api/auth/login", json={
        "username": username,
        "password": "StrongTestPass123!",
        "otp_code": None,
    })
    assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
    return dict(resp.cookies)


def _generate_blind_token() -> str:
    """Generates a cryptographically random 64-hex-character blind rendezvous token."""
    return secrets.token_hex(32)


def _mock_encrypted_payload(prefix: str = "mock") -> str:
    """Generates a realistic encrypted signaling payload matching sv-sig-v1 format."""
    return f"sv-sig-v1:{prefix}_" + secrets.token_urlsafe(48)


# ============================================================================
# 1. Happy Path: 2-Peer Handshake & Bi-directional Exchange
# ============================================================================

def test_webrtc_signal_two_peer_handshake_and_exchange(
    db: Session, client: TestClient
):
    """Verifies complete signaling lifecycle between 2 peers over /api/social/webrtc/signal."""
    alice = _create_user(db, "alice_call_1")
    bob = _create_user(db, "bob_call_1")
    alice_cookies = _login_user(client, "alice_call_1")
    bob_cookies = _login_user(client, "bob_call_1")

    token = _generate_blind_token()
    sdp_offer = _mock_encrypted_payload("offer")
    sdp_answer = _mock_encrypted_payload("answer")
    candidate_a = _mock_encrypted_payload("candidate_alice")
    candidate_b = _mock_encrypted_payload("candidate_bob")

    with client.websocket_connect("/api/social/webrtc/signal", cookies=alice_cookies) as ws_alice:
        # Step 1: Alice joins
        ws_alice.send_json({"action": "join", "token": token})
        resp_a = ws_alice.receive_json()
        assert resp_a["event"] == "joined"
        assert resp_a["role"] == "initiator"
        assert resp_a["peer_count"] == 1

        with client.websocket_connect("/api/social/webrtc/signal", cookies=bob_cookies) as ws_bob:
            # Step 2: Bob joins
            ws_bob.send_json({"action": "join", "token": token})
            resp_b = ws_bob.receive_json()
            assert resp_b["event"] == "joined"
            assert resp_b["role"] == "receiver"
            assert resp_b["peer_count"] == 2

            # Step 3: Alice notified of Bob's arrival
            peer_joined_msg = ws_alice.receive_json()
            assert peer_joined_msg["event"] == "peer_joined"
            assert peer_joined_msg["peer_count"] == 2

            # Step 4: Alice sends SDP offer -> Bob receives
            ws_alice.send_json({"action": "signal", "data": sdp_offer})
            relayed_offer = ws_bob.receive_json()
            assert relayed_offer["event"] == "signal"
            assert relayed_offer["data"] == sdp_offer

            # Step 5: Bob sends SDP answer -> Alice receives
            ws_bob.send_json({"action": "signal", "data": sdp_answer})
            relayed_answer = ws_alice.receive_json()
            assert relayed_answer["event"] == "signal"
            assert relayed_answer["data"] == sdp_answer

            # Step 6: ICE candidates exchange
            ws_alice.send_json({"action": "signal", "data": candidate_a})
            relayed_cand_a = ws_bob.receive_json()
            assert relayed_cand_a["event"] == "signal"
            assert relayed_cand_a["data"] == candidate_a

            ws_bob.send_json({"action": "signal", "data": candidate_b})
            relayed_cand_b = ws_alice.receive_json()
            assert relayed_cand_b["event"] == "signal"
            assert relayed_cand_b["data"] == candidate_b

            # Step 7: Ping/Pong isolation
            ws_alice.send_json({"action": "ping"})
            pong_a = ws_alice.receive_json()
            assert pong_a["event"] == "pong"

            ws_bob.send_json({"action": "ping"})
            pong_b = ws_bob.receive_json()
            assert pong_b["event"] == "pong"

            # Step 8: Alice sends explicit leave
            ws_alice.send_json({"action": "leave", "reason": "hangup"})
            peer_left_msg = ws_bob.receive_json()
            assert peer_left_msg["event"] == "peer_left"


# ============================================================================
# 2. Capacity Guard: 3rd Peer Rejection (room_full)
# ============================================================================

def test_webrtc_signal_third_peer_rejected_room_full(
    db: Session, client: TestClient
):
    """Verifies that an active room strictly caps at 2 peers and rejects a 3rd peer."""
    alice = _create_user(db, "alice_cap")
    bob = _create_user(db, "bob_cap")
    charlie = _create_user(db, "charlie_cap")
    alice_cookies = _login_user(client, "alice_cap")
    bob_cookies = _login_user(client, "bob_cap")
    charlie_cookies = _login_user(client, "charlie_cap")

    token = _generate_blind_token()

    with client.websocket_connect("/api/social/webrtc/signal", cookies=alice_cookies) as ws_alice:
        ws_alice.send_json({"action": "join", "token": token})
        assert ws_alice.receive_json()["event"] == "joined"

        with client.websocket_connect("/api/social/webrtc/signal", cookies=bob_cookies) as ws_bob:
            ws_bob.send_json({"action": "join", "token": token})
            assert ws_bob.receive_json()["event"] == "joined"
            assert ws_alice.receive_json()["event"] == "peer_joined"

            # Charlie attempts to join full room
            try:
                with client.websocket_connect("/api/social/webrtc/signal", cookies=charlie_cookies) as ws_charlie:
                    ws_charlie.send_json({"action": "join", "token": token})
                    err_frame = ws_charlie.receive_json()
                    assert err_frame["event"] == "error"
                    assert err_frame["error"] == "room_full"
                    ws_charlie.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 1008

            # Verify Alice and Bob remain unaffected and can still communicate
            test_signal = _mock_encrypted_payload("still_alive")
            ws_alice.send_json({"action": "signal", "data": test_signal})
            rx = ws_bob.receive_json()
            assert rx["event"] == "signal"
            assert rx["data"] == test_signal


# ============================================================================
# 3. Lifecycle: Ephemeral TTL Expiration & Cleanup
# ============================================================================

@pytest.mark.asyncio
async def test_webrtc_signal_token_ttl_expiration_and_cleanup():
    """Verifies that ephemeral rendezvous rooms expire after TTL and are cleanly evicted."""
    token = _generate_blind_token()

    # Step 1: Unit-level eviction verification via BlindRendezvousManager
    peer_id, role, count, queue = await BlindRendezvousManager.join(token)
    assert role == "initiator"
    session = BlindRendezvousManager.get_session(token)
    assert session is not None
    assert not session.is_expired()

    # Fast-forward simulated time beyond TTL (120s)
    future_time = session.created_at + 125.0
    evicted_count = await BlindRendezvousManager.sweep_expired(now=future_time)
    assert evicted_count == 1
    assert BlindRendezvousManager.get_session(token) is None

    # Verify session_expired error was placed in the peer's queue
    item = queue.get_nowait()
    assert item["event"] == "error"
    assert item["error"] == "session_expired"

    # Step 2: Attempting to join an expired token raises SessionExpiredError / SessionTerminatedError
    with pytest.raises((SessionExpiredError, SessionTerminatedError)):
        await BlindRendezvousManager.join(token)


# ============================================================================
# 4. Clean Hangup: Explicit Leave Notifies Peer
# ============================================================================

def test_webrtc_signal_explicit_leave_notifies_peer(
    db: Session, client: TestClient
):
    """Verifies that peer sending explicit leave action notifies partner with reason."""
    alice = _create_user(db, "alice_leave")
    bob = _create_user(db, "bob_leave")
    alice_cookies = _login_user(client, "alice_leave")
    bob_cookies = _login_user(client, "bob_leave")

    token = _generate_blind_token()

    with client.websocket_connect("/api/social/webrtc/signal", cookies=alice_cookies) as ws_alice:
        ws_alice.send_json({"action": "join", "token": token})
        assert ws_alice.receive_json()["event"] == "joined"

        with client.websocket_connect("/api/social/webrtc/signal", cookies=bob_cookies) as ws_bob:
            ws_bob.send_json({"action": "join", "token": token})
            assert ws_bob.receive_json()["event"] == "joined"
            assert ws_alice.receive_json()["event"] == "peer_joined"

            # Bob sends explicit leave
            ws_bob.send_json({"action": "leave", "reason": "user_hangup"})
            peer_left = ws_alice.receive_json()
            assert peer_left["event"] == "peer_left"
            assert peer_left.get("reason") == "user_hangup"

    # Verify room is closed and purged
    assert BlindRendezvousManager.get_session(token) is None


# ============================================================================
# 5. Resilience: Abrupt Disconnect Handling (peer_left)
# ============================================================================

def test_webrtc_signal_abrupt_disconnect_notifies_peer(
    db: Session, client: TestClient
):
    """Verifies that abrupt socket disconnection cleanly dispatches 'peer_left' to partner."""
    alice = _create_user(db, "alice_drop")
    bob = _create_user(db, "bob_drop")
    alice_cookies = _login_user(client, "alice_drop")
    bob_cookies = _login_user(client, "bob_drop")

    token = _generate_blind_token()

    with client.websocket_connect("/api/social/webrtc/signal", cookies=alice_cookies) as ws_alice:
        ws_alice.send_json({"action": "join", "token": token})
        assert ws_alice.receive_json()["event"] == "joined"

        # Bob connects and abruptly leaves (context manager exits)
        with client.websocket_connect("/api/social/webrtc/signal", cookies=bob_cookies) as ws_bob:
            ws_bob.send_json({"action": "join", "token": token})
            assert ws_bob.receive_json()["event"] == "joined"
            assert ws_alice.receive_json()["event"] == "peer_joined"
            # Bob abruptly drops here without sending {"action": "leave"}

        # Alice must receive 'peer_left' event
        peer_left_event = ws_alice.receive_json()
        assert peer_left_event["event"] == "peer_left"


# ============================================================================
# 6. Privacy Verification: ZERO Database or Audit Records
# ============================================================================

def test_webrtc_signal_strict_privacy_zero_db_or_audit_records(
    db: Session, client: TestClient
):
    """CRITICAL PRIVACY INVARIANT: Proves zero relational data or audit logs are stored."""
    alice = _create_user(db, "alice_priv")
    bob = _create_user(db, "bob_priv")
    alice_cookies = _login_user(client, "alice_priv")
    bob_cookies = _login_user(client, "bob_priv")

    # Baseline DB counts
    initial_audit_count = db.query(AuditLog).count()
    initial_envelope_count = db.query(E2eeBlindEnvelope).count()
    initial_media_count = db.query(ChatMedia).count()

    token = _generate_blind_token()

    # Perform active WebRTC call setup and signaling exchange
    with client.websocket_connect("/api/social/webrtc/signal", cookies=alice_cookies) as ws_alice:
        ws_alice.send_json({"action": "join", "token": token})
        ws_alice.receive_json()

        with client.websocket_connect("/api/social/webrtc/signal", cookies=bob_cookies) as ws_bob:
            ws_bob.send_json({"action": "join", "token": token})
            ws_bob.receive_json()
            ws_alice.receive_json()  # peer_joined

            # Exchange 3 rounds of SDP and ICE signals
            for i in range(3):
                payload_a = _mock_encrypted_payload(f"msg_{i}")
                ws_alice.send_json({"action": "signal", "data": payload_a})
                ws_bob.receive_json()

            ws_bob.send_json({"action": "leave"})
            ws_alice.receive_json()  # peer_left

    # Verification 1: AuditLog must have zero new entries
    final_audit_count = db.query(AuditLog).count()
    assert final_audit_count == initial_audit_count, (
        f"Privacy violation: AuditLog count increased from {initial_audit_count} to {final_audit_count}"
    )

    # Verification 2: Check for any leaked audit content mentioning call or webrtc
    suspicious_audit = db.query(AuditLog).filter(
        (AuditLog.action.ilike("%webrtc%"))
        | (AuditLog.action.ilike("%call%"))
        | (AuditLog.action.ilike("%signal%"))
        | (AuditLog.details.ilike(f"%{token}%"))
    ).all()
    assert len(suspicious_audit) == 0, f"Found leaked call records in AuditLog: {suspicious_audit}"

    # Verification 3: Zero envelopes or media persisted
    assert db.query(E2eeBlindEnvelope).count() == initial_envelope_count
    assert db.query(ChatMedia).count() == initial_media_count

    # Verification 4: Prohibit existence of persistent calling tables in Base metadata
    all_table_names = set(Base.metadata.tables.keys())
    prohibited_tables = {"calls", "call_logs", "call_history", "call_participants", "webrtc_sessions"}
    found_prohibited = all_table_names.intersection(prohibited_tables)
    assert len(found_prohibited) == 0, f"Prohibited calling tables found in schema: {found_prohibited}"


# ============================================================================
# 7. Security: Unauthenticated Connection Rejected
# ============================================================================

def test_webrtc_signal_unauthenticated_connection_rejected(client: TestClient):
    """Verifies that connection without authentication is rejected with code 1008."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/social/webrtc/signal"):
            pass
    assert exc.value.code == 1008


# ============================================================================
# 8. Security: Invalid Token Format Rejected
# ============================================================================

def test_webrtc_signal_invalid_token_format_rejected(
    db: Session, client: TestClient
):
    """Verifies that invalid tokens (too short, path traversal, non-hex) are rejected."""
    alice = _create_user(db, "alice_bad_tok")
    cookies = _login_user(client, "alice_bad_tok")

    invalid_tokens = [
        "short",                       # Too short (<16 chars)
        "token with spaces inside!!",  # Illegal characters
        "../../etc/passwd",            # Path traversal attempt
        "",                            # Empty token
    ]

    for bad_tok in invalid_tokens:
        try:
            with client.websocket_connect("/api/social/webrtc/signal", cookies=cookies) as ws:
                ws.send_json({"action": "join", "token": bad_tok})
                resp = ws.receive_json()
                assert resp["event"] == "error"
        except WebSocketDisconnect as exc:
            assert exc.code in (1008, 1003)


# ============================================================================
# 9. Protocol Guard: First Message Must Be Join
# ============================================================================

def test_webrtc_signal_first_message_must_be_join(
    db: Session, client: TestClient
):
    """Verifies that sending 'signal' or 'ping' before 'join' is rejected."""
    alice = _create_user(db, "alice_no_join")
    cookies = _login_user(client, "alice_no_join")

    try:
        with client.websocket_connect("/api/social/webrtc/signal", cookies=cookies) as ws:
            ws.send_json({"action": "signal", "data": _mock_encrypted_payload("early")})
            resp = ws.receive_json()
            assert resp["event"] == "error"
            assert resp["error"] == "not_joined"
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


# ============================================================================
# 10. Security: Anti-Replay of Consumed Token
# ============================================================================

def test_webrtc_signal_anti_replay_consumed_token_rejected(
    db: Session, client: TestClient
):
    """Verifies that consumed tokens cannot be re-used to establish a new call (anti-replay)."""
    alice = _create_user(db, "alice_replay")
    bob = _create_user(db, "bob_replay")
    alice_cookies = _login_user(client, "alice_replay")
    bob_cookies = _login_user(client, "bob_replay")

    token = _generate_blind_token()

    # Run session 1 and hang up
    with client.websocket_connect("/api/social/webrtc/signal", cookies=alice_cookies) as ws_a:
        ws_a.send_json({"action": "join", "token": token})
        ws_a.receive_json()
        ws_a.send_json({"action": "leave"})

    # Attempt to re-join using the consumed token
    try:
        with client.websocket_connect("/api/social/webrtc/signal", cookies=bob_cookies) as ws_b:
            ws_b.send_json({"action": "join", "token": token})
            resp = ws_b.receive_json()
            assert resp["event"] == "error"
            assert resp["error"] in ("session_terminated", "session_expired")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


# ============================================================================
# 11. Concurrency: Multi-Room Strict Isolation
# ============================================================================

def test_webrtc_signal_multi_room_strict_isolation(
    db: Session, client: TestClient
):
    """Verifies that messages in Room A do not leak into concurrent Room B."""
    alice = _create_user(db, "alice_iso")
    bob = _create_user(db, "bob_iso")
    charlie = _create_user(db, "charlie_iso")
    dave = _create_user(db, "dave_iso")

    c_alice = _login_user(client, "alice_iso")
    c_bob = _login_user(client, "bob_iso")
    c_charlie = _login_user(client, "charlie_iso")
    c_dave = _login_user(client, "dave_iso")

    token_1 = _generate_blind_token()
    token_2 = _generate_blind_token()

    sig_1 = _mock_encrypted_payload("room_1_msg")
    sig_2 = _mock_encrypted_payload("room_2_msg")

    with client.websocket_connect("/api/social/webrtc/signal", cookies=c_alice) as ws_a, \
         client.websocket_connect("/api/social/webrtc/signal", cookies=c_bob) as ws_b, \
         client.websocket_connect("/api/social/webrtc/signal", cookies=c_charlie) as ws_c, \
         client.websocket_connect("/api/social/webrtc/signal", cookies=c_dave) as ws_d:

        # Join Room 1
        ws_a.send_json({"action": "join", "token": token_1})
        ws_a.receive_json()
        ws_b.send_json({"action": "join", "token": token_1})
        ws_b.receive_json()
        ws_a.receive_json()  # peer_joined

        # Join Room 2
        ws_c.send_json({"action": "join", "token": token_2})
        ws_c.receive_json()
        ws_d.send_json({"action": "join", "token": token_2})
        ws_d.receive_json()
        ws_c.receive_json()  # peer_joined

        # Alice sends in Room 1
        ws_a.send_json({"action": "signal", "data": sig_1})
        rx_b = ws_b.receive_json()
        assert rx_b["data"] == sig_1

        # Charlie sends in Room 2
        ws_c.send_json({"action": "signal", "data": sig_2})
        rx_d = ws_d.receive_json()
        assert rx_d["data"] == sig_2

        # Ping Room 1 to ensure no phantom message arrived
        ws_a.send_json({"action": "ping"})
        assert ws_a.receive_json()["event"] == "pong"


# ============================================================================
# 12. Security & DoS: Oversized Payload Rejected
# ============================================================================

def test_webrtc_signal_oversized_payload_rejected(
    db: Session, client: TestClient
):
    """Verifies that signal payload exceeding 64KB is rejected without dropping connection."""
    alice = _create_user(db, "alice_oversize")
    bob = _create_user(db, "bob_oversize")
    alice_cookies = _login_user(client, "alice_oversize")
    bob_cookies = _login_user(client, "bob_oversize")

    token = _generate_blind_token()

    with client.websocket_connect("/api/social/webrtc/signal", cookies=alice_cookies) as ws_a, \
         client.websocket_connect("/api/social/webrtc/signal", cookies=bob_cookies) as ws_b:

        ws_a.send_json({"action": "join", "token": token})
        ws_a.receive_json()
        ws_b.send_json({"action": "join", "token": token})
        ws_b.receive_json()
        ws_a.receive_json()  # peer_joined

        # Send oversized payload (>64KB)
        huge_payload = "sv-sig-v1:" + ("A" * 70000)
        ws_a.send_json({"action": "signal", "data": huge_payload})
        err_msg = ws_a.receive_json()
        assert err_msg["event"] == "error"
        assert err_msg["error"] == "payload_too_large"

        # Verify communication is still active
        normal_payload = _mock_encrypted_payload("normal_after_error")
        ws_a.send_json({"action": "signal", "data": normal_payload})
        rx_b = ws_b.receive_json()
        assert rx_b["data"] == normal_payload


# ============================================================================
# 13. Discovery: ICE Servers Endpoint
# ============================================================================

def test_webrtc_ice_servers_endpoint(db: Session, client: TestClient):
    """Verifies GET /api/social/webrtc/ice-servers authentication and response schema."""
    # Unauthenticated -> 401
    resp_unauth = client.get("/api/social/webrtc/ice-servers")
    assert resp_unauth.status_code == 401

    # Authenticated -> 200 with list of STUN/TURN servers
    user = _create_user(db, "ice_user")
    cookies = _login_user(client, "ice_user")

    resp = client.get("/api/social/webrtc/ice-servers", cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert "ice_servers" in data
    assert isinstance(data["ice_servers"], list)
    assert len(data["ice_servers"]) > 0
    first_server = data["ice_servers"][0]
    assert "urls" in first_server
    urls = first_server["urls"]
    if isinstance(urls, list):
        assert any("stun:" in u for u in urls)
    else:
        assert "stun:" in urls
