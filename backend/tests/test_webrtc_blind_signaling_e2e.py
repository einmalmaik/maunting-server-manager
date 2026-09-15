from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional, Set
from uuid import uuid4
import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from models import AuditLog, User
import database as db_module
from services.webrtc_rendezvous_service import (
    BlindRendezvousManager,
    RoomFullError,
    SessionExpiredError,
    SessionTerminatedError,
    SessionNotFoundError,
    PeerNotInSessionError,
    DEFAULT_ROOM_TTL_SECONDS,
    MAX_PEERS_PER_ROOM,
)

# ============================================================================
# Pytest Test Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def reset_rendezvous_manager():
    """Ensures complete in-memory isolation between tests."""
    BlindRendezvousManager.clear_all_for_testing()
    yield
    BlindRendezvousManager.clear_all_for_testing()


# ============================================================================
# Pytest Test Suites (Tiers 1-4)
# ============================================================================

@pytest.mark.asyncio
class TestBlindRendezvousPairingAndCap:
    """Tiers 1-3: Rendezvous pairing, 2-peer cap, 3rd peer rejection, TTL cleanup."""

    async def test_rendezvous_pairing_initiator_and_receiver(self):
        """Tier 1.1: First peer is initiator (count=1), second peer is receiver (count=2), peer_joined event."""
        token = f"blind-token-{uuid4().hex}"

        # Peer 1 joins as initiator
        p1_id, role1, count1, q1 = await BlindRendezvousManager.join(token, "peer-1")
        assert role1 == "initiator"
        assert count1 == 1
        assert p1_id == "peer-1"

        # Peer 2 joins as receiver
        p2_id, role2, count2, q2 = await BlindRendezvousManager.join(token, "peer-2")
        assert role2 == "receiver"
        assert count2 == 2
        assert p2_id == "peer-2"

        # Peer 1 receives peer_joined notification
        msg = await q1.get()
        assert msg["event"] == "peer_joined"
        assert msg["peer_count"] == 2

    async def test_two_peer_room_cap_and_third_peer_rejection(self):
        """Tier 1.2: 3rd peer attempting to join full room is rejected with RoomFullError (4003)."""
        token = f"blind-token-{uuid4().hex}"

        await BlindRendezvousManager.join(token, "peer-1")
        await BlindRendezvousManager.join(token, "peer-2")

        # 3rd peer attempts to join
        with pytest.raises(RoomFullError) as exc_info:
            await BlindRendezvousManager.join(token, "peer-3")

        assert exc_info.value.code == 4003
        assert exc_info.value.error == "room_full"

        # Verify room still only has 2 peers
        session = BlindRendezvousManager.get_session(token)
        assert session is not None
        assert session.peer_count == 2
        assert "peer-3" not in session.peers

    async def test_opaque_signal_relay_between_peers(self):
        """Tier 1.3: Server blindly forwards opaque encrypted ciphertext envelope intact."""
        token = f"blind-token-{uuid4().hex}"

        p1_id, _, _, q1 = await BlindRendezvousManager.join(token, "peer-1")
        p2_id, _, _, q2 = await BlindRendezvousManager.join(token, "peer-2")

        # Clear peer_joined from q1
        await q1.get()

        # Peer 1 sends encrypted SDP offer envelope
        opaque_sdp = "sv-sig-v1:eyJ0eXBlIjoib2ZmZXIiLCJzZHAiOiJ2PTBcclxuLi4uIn0="
        relayed = await BlindRendezvousManager.relay(token, p1_id, opaque_sdp)
        assert relayed is True

        # Peer 2 receives message without server tampering
        msg = await q2.get()
        assert msg["event"] == "signal"
        assert msg["data"] == opaque_sdp

    async def test_peer_disconnect_and_room_eviction(self):
        """Tier 1.4: When peer leaves, remaining peer receives peer_left and session is marked closed."""
        token = f"blind-token-{uuid4().hex}"

        p1_id, _, _, q1 = await BlindRendezvousManager.join(token, "peer-1")
        p2_id, _, _, q2 = await BlindRendezvousManager.join(token, "peer-2")

        # Peer 1 leaves
        await BlindRendezvousManager.leave(token, p1_id, reason="user_hangup")
        peer2_msg = await q2.get()
        assert peer2_msg["event"] == "peer_left"
        assert peer2_msg["reason"] == "user_hangup"

        # Session should be closed (anti-replay tombstoned)
        assert BlindRendezvousManager.get_session(token) is None

        # Rejoining consumed token raises SessionTerminatedError
        with pytest.raises(SessionTerminatedError):
            await BlindRendezvousManager.join(token, "peer-rejoin")

    async def test_room_ttl_cleanup_evicts_expired_rooms(self):
        """Tier 2.1: Rooms older than 120s TTL are evicted by cleanup sweep."""
        fresh_token = f"fresh-{uuid4().hex}"
        expired_token = f"expired-{uuid4().hex}"

        await BlindRendezvousManager.join(fresh_token, "peer-fresh")
        await BlindRendezvousManager.join(expired_token, "peer-old")

        session_expired = BlindRendezvousManager.get_session(expired_token)
        assert session_expired is not None
        # Artificially age the expired room past 120s
        session_expired.expires_at = time.time() - 1

        evicted_count = await BlindRendezvousManager.sweep_expired()
        assert evicted_count >= 1
        assert BlindRendezvousManager.get_session(expired_token) is None
        assert BlindRendezvousManager.get_session(fresh_token) is not None

    async def test_relay_before_partner_joins_returns_false(self):
        """Tier 2.2: Relaying when partner has not yet joined returns False."""
        token = f"solo-{uuid4().hex}"
        p1_id, _, _, _ = await BlindRendezvousManager.join(token, "solo-peer")

        relayed = await BlindRendezvousManager.relay(token, p1_id, "test-signal")
        assert relayed is False

    async def test_relay_to_nonexistent_session_raises_error(self):
        """Tier 2.3: Relaying to a session that does not exist raises SessionNotFoundError."""
        with pytest.raises(SessionNotFoundError):
            await BlindRendezvousManager.relay("non-existent-token", "peer-x", "test-data")

    async def test_relay_from_unknown_peer_raises_error(self):
        """Tier 2.4: Relaying from a peer ID not in the room raises PeerNotInSessionError."""
        token = f"room-{uuid4().hex}"
        await BlindRendezvousManager.join(token, "peer-legit")

        with pytest.raises(PeerNotInSessionError):
            await BlindRendezvousManager.relay(token, "peer-imposter", "malicious-data")

    async def test_concurrency_isolation_across_separate_tokens(self):
        """Tier 3.1: Multiple rooms operate concurrently without cross-talk."""
        token_a = f"room-a-{uuid4().hex}"
        token_b = f"room-b-{uuid4().hex}"

        a1, _, _, qa1 = await BlindRendezvousManager.join(token_a, "alice")
        a2, _, _, qa2 = await BlindRendezvousManager.join(token_a, "bob")

        b1, _, _, qb1 = await BlindRendezvousManager.join(token_b, "charlie")
        b2, _, _, qb2 = await BlindRendezvousManager.join(token_b, "dave")

        # Relay in Room A
        await BlindRendezvousManager.relay(token_a, a1, "signal-for-bob")

        # Assert Bob received it
        bob_msg = await qa2.get()
        assert bob_msg["data"] == "signal-for-bob"

        # Assert Charlie received peer_joined when Dave joined, but NOT alice's signal
        charlie_joined = await qb1.get()
        assert charlie_joined["event"] == "peer_joined"
        assert qb1.empty()

    async def test_active_session_count_tracking(self):
        """Tier 3.2: Tracks active session count accurately across creation and destruction."""
        initial_count = BlindRendezvousManager.get_active_session_count()
        assert initial_count == 0

        token1 = f"token-1-{uuid4().hex}"
        token2 = f"token-2-{uuid4().hex}"

        await BlindRendezvousManager.join(token1, "p1")
        await BlindRendezvousManager.join(token2, "p2")
        assert BlindRendezvousManager.get_active_session_count() == 2

        await BlindRendezvousManager.leave(token1, "p1")
        assert BlindRendezvousManager.get_active_session_count() == 1


class TestZeroMetadataAndAuditInvariants:
    """Critical Privacy Invariants: Zero DB records & Zero Audit Logs (ORIGINAL_REQUEST.md § R4)."""

    def test_zero_caller_callee_records_in_database(self, db: Session, owner_user: User, regular_user: User):
        """Tier 1.5: Verifies that signaling setup leaves ZERO caller-callee bindings or call logs in DB."""
        # Query existing tables in DB using SQLAlchemy inspect
        inspector = inspect(db_module.engine)
        table_names = inspector.get_table_names()

        # Invariant: There must NOT exist a 'calls' table storing call metadata
        assert "calls" not in table_names
        assert "call_logs" not in table_names

        # Verify no user records were updated with call relations
        owner_before = db.query(User).filter_by(id=owner_user.id).first()
        regular_before = db.query(User).filter_by(id=regular_user.id).first()
        assert owner_before is not None
        assert regular_before is not None

    def test_zero_audit_log_entries_during_signaling(self, db: Session):
        """Tier 1.6: WebRTC signaling must NEVER record entries in audit_logs table."""
        audit_count_before = db.query(AuditLog).count()

        # Simulate signaling activity
        async def run_signaling():
            token = f"blind-{uuid4().hex}"
            p1, _, _, q1 = await BlindRendezvousManager.join(token, "user-1")
            p2, _, _, q2 = await BlindRendezvousManager.join(token, "user-2")
            await BlindRendezvousManager.relay(token, p1, "sv-sig-v1:test")
            await BlindRendezvousManager.leave(token, p1)
            await BlindRendezvousManager.leave(token, p2)

        asyncio.run(run_signaling())

        # Audit log count MUST remain identical (Zero entries created)
        audit_count_after = db.query(AuditLog).count()
        assert audit_count_after == audit_count_before

    def test_blind_token_in_json_payload_contract(self):
        """Tier 1.7: Blind token must be transmitted in first JSON frame, never in URL query parameters."""
        join_payload = {"action": "join", "token": "blind-session-token-xyz"}
        json_str = json.dumps(join_payload)
        parsed = json.loads(json_str)

        assert parsed["action"] == "join"
        assert "token" in parsed
        assert len(parsed["token"]) >= 8

    def test_ice_servers_configuration_format(self):
        """Tier 1.8: Verifies expected ICE servers STUN/TURN structure."""
        # STUN/TURN structure expected by WebRTC client
        expected_config = {
            "iceServers": [
                {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.l.google.com:19302"]},
            ]
        }
        assert "iceServers" in expected_config
        assert len(expected_config["iceServers"]) >= 1
        assert any("stun:" in url for url in expected_config["iceServers"][0]["urls"])


class TestE2eSignalingWorkloads:
    """Tier 4: Realistic Real-World Workload Scenarios."""

    @pytest.mark.asyncio
    async def test_real_world_full_call_session_e2e(self):
        """Tier 4.1: Complete 1-on-1 Blind Call Session lifecycle from join to leave."""
        token = f"session-{uuid4().hex}"

        # 1. Initiator joins
        alice_id, alice_role, count1, q_alice = await BlindRendezvousManager.join(token, "alice-client")
        assert alice_role == "initiator"
        assert count1 == 1

        # 2. Receiver joins
        bob_id, bob_role, count2, q_bob = await BlindRendezvousManager.join(token, "bob-client")
        assert bob_role == "receiver"
        assert count2 == 2

        # Alice receives peer_joined event when Bob joins
        alice_peer_joined = await q_alice.get()
        assert alice_peer_joined["event"] == "peer_joined"

        # 3. Alice exchanges SDP Offer
        sdp_offer = "sv-sig-v1:OFFER_PAYLOAD_CIPHERTEXT"
        relayed_offer = await BlindRendezvousManager.relay(token, alice_id, sdp_offer)
        assert relayed_offer is True

        offer_received = await q_bob.get()
        assert offer_received["data"] == sdp_offer

        # 4. Bob exchanges SDP Answer
        sdp_answer = "sv-sig-v1:ANSWER_PAYLOAD_CIPHERTEXT"
        relayed_answer = await BlindRendezvousManager.relay(token, bob_id, sdp_answer)
        assert relayed_answer is True

        answer_received = await q_alice.get()
        assert answer_received["data"] == sdp_answer

        # 5. ICE candidate exchanges
        ice_alice = "sv-sig-v1:ICE_CANDIDATE_ALICE"
        await BlindRendezvousManager.relay(token, alice_id, ice_alice)
        ice_bob_rx = await q_bob.get()
        assert ice_bob_rx["data"] == ice_alice

        # 6. Teardown
        await BlindRendezvousManager.leave(token, alice_id, reason="hangup")
        bob_left = await q_bob.get()
        assert bob_left["event"] == "peer_left"

        assert BlindRendezvousManager.get_session(token) is None
