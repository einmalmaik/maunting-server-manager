"""Ephemeral In-Memory Blind WebRTC Rendezvous Manager.

Implements zero-metadata blinded WebRTC signaling (R4).
Rooms are strictly stored in-memory (RAM) and indexed solely by opaque blind tokens.
Contains NO database models, NO user IDs, NO usernames, NO IP logs, and NO audit logs.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Literal

_log = logging.getLogger("msm.webrtc_rendezvous")

DEFAULT_ROOM_TTL_SECONDS = 120.0
MAX_PEERS_PER_ROOM = 2
MAX_PEER_QUEUE_SIZE = 128


# ============================================================================
# Exception Hierarchy
# ============================================================================

class WebRtcRendezvousError(Exception):
    """Base exception for WebRTC rendezvous operations."""
    def __init__(self, error: str, code: int = 4000, message: str = "") -> None:
        super().__init__(message or error)
        self.error = error
        self.code = code
        self.message = message or error


class RoomFullError(WebRtcRendezvousError):
    """Raised when a 3rd peer attempts to join a full room."""
    def __init__(self, message: str = "Rendezvous room is full (max 2 participants)") -> None:
        super().__init__(error="room_full", code=4003, message=message)


class SessionExpiredError(WebRtcRendezvousError):
    """Raised when attempting to join or signal on an expired session."""
    def __init__(self, message: str = "Rendezvous session has expired") -> None:
        super().__init__(error="session_expired", code=4002, message=message)


class SessionTerminatedError(WebRtcRendezvousError):
    """Raised when attempting to join a session that has already concluded (anti-replay)."""
    def __init__(self, message: str = "Rendezvous token has already been consumed") -> None:
        super().__init__(error="session_terminated", code=4004, message=message)


class SessionNotFoundError(WebRtcRendezvousError):
    """Raised when token does not match an active session."""
    def __init__(self, message: str = "Rendezvous session not found") -> None:
        super().__init__(error="session_not_found", code=4004, message=message)


class PeerNotInSessionError(WebRtcRendezvousError):
    """Raised when peer ID is not part of the rendezvous session."""
    def __init__(self, message: str = "Peer is not a participant in this session") -> None:
        super().__init__(error="peer_not_in_session", code=4001, message=message)


# ============================================================================
# In-Memory Session & Peer Entities
# ============================================================================

class RendezvousPeer:
    """Represents an active anonymous peer connection in an ephemeral room."""
    __slots__ = ("peer_id", "role", "queue", "joined_at", "last_activity")

    def __init__(
        self,
        peer_id: str,
        role: Literal["initiator", "receiver"],
        queue: asyncio.Queue[dict[str, Any]],
        joined_at: float,
    ) -> None:
        self.peer_id = peer_id
        self.role = role
        self.queue = queue
        self.joined_at = joined_at
        self.last_activity = joined_at


class BlindRendezvousSession:
    """An ephemeral 2-party rendezvous room indexed solely by blind token."""
    __slots__ = ("token", "created_at", "expires_at", "peers", "is_closed", "closed_at")

    def __init__(self, token: str, ttl_seconds: float = DEFAULT_ROOM_TTL_SECONDS) -> None:
        self.token = token
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl_seconds
        self.peers: dict[str, RendezvousPeer] = {}
        self.is_closed = False
        self.closed_at: float | None = None

    @property
    def peer_count(self) -> int:
        return len(self.peers)

    def is_expired(self, now: float | None = None) -> bool:
        ts = now if now is not None else time.time()
        return ts >= self.expires_at or self.is_closed

    def touch(self, ttl_seconds: float = DEFAULT_ROOM_TTL_SECONDS, now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        self.expires_at = max(self.expires_at, ts + ttl_seconds)


# ============================================================================
# Manager Class
# ============================================================================

class BlindRendezvousManager:
    """100% ephemeral in-memory rendezvous manager for blinded WebRTC signaling.

    Keyed exclusively by blind rendezvous token.
    Enforces:
    - 2 peers maximum per room
    - 120s TTL eviction
    - Anti-replay tombstone cache for closed tokens
    - Thread-safe / asyncio-safe operations via asyncio.Lock
    - Decoupled peer queues with backpressure protection
    - Zero user identity or metadata storage
    """

    _sessions: dict[str, BlindRendezvousSession] = {}
    _closed_tokens: dict[str, float] = {}  # token -> closed_at timestamp
    _lock = asyncio.Lock()
    _ttl_seconds: float = DEFAULT_ROOM_TTL_SECONDS
    _max_peers: int = MAX_PEERS_PER_ROOM
    _max_queue_size: int = MAX_PEER_QUEUE_SIZE

    @classmethod
    async def join(
        cls,
        token: str,
        peer_id: str | None = None,
        ttl_seconds: float = DEFAULT_ROOM_TTL_SECONDS,
    ) -> tuple[str, Literal["initiator", "receiver"], int, asyncio.Queue[dict[str, Any]]]:
        """Atomically joins or creates a rendezvous room for the given blind token.

        Returns (peer_id, role, peer_count, rx_queue).
        Raises RoomFullError, SessionTerminatedError, or SessionExpiredError.
        """
        async with cls._lock:
            now = time.time()
            cls._sweep_expired_locked(now)

            # 1. Anti-replay check: has this token already been consumed/closed?
            if token in cls._closed_tokens:
                raise SessionTerminatedError()

            session = cls._sessions.get(token)

            # 2. If session exists, check expiration and capacity
            if session is not None:
                if session.is_expired(now):
                    cls._sessions.pop(token, None)
                    cls._closed_tokens[token] = now
                    raise SessionExpiredError()

                if session.peer_count >= cls._max_peers and (peer_id is None or peer_id not in session.peers):
                    raise RoomFullError()

                role: Literal["initiator", "receiver"] = "receiver"
            else:
                session = BlindRendezvousSession(token=token, ttl_seconds=ttl_seconds)
                cls._sessions[token] = session
                role = "initiator"

            # 3. Create decoupled peer queue and anonymous peer record
            actual_peer_id = peer_id if peer_id else f"peer-{uuid.uuid4().hex[:12]}"
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=cls._max_queue_size)
            peer = RendezvousPeer(
                peer_id=actual_peer_id,
                role=role,
                queue=queue,
                joined_at=now,
            )
            session.peers[actual_peer_id] = peer
            current_peer_count = session.peer_count

            # 4. If this is the second peer (receiver), notify the initiator
            if role == "receiver":
                for other_id, other_peer in session.peers.items():
                    if other_id != actual_peer_id:
                        cls._safe_enqueue(other_peer.queue, {
                            "event": "peer_joined",
                            "peer_count": current_peer_count,
                        })

            _log.debug("Rendezvous join: room=%s... role=%s count=%d", token[:8], role, current_peer_count)
            return actual_peer_id, role, current_peer_count, queue

    @classmethod
    async def relay(
        cls,
        token: str,
        sender_peer_id: str,
        data: str | dict[str, Any],
    ) -> bool:
        """Relays encrypted signaling data from sender peer to the partner peer.

        Returns True if delivered, False if partner has not joined yet.
        Raises SessionNotFoundError, SessionExpiredError, or PeerNotInSessionError.
        """
        async with cls._lock:
            now = time.time()
            session = cls._sessions.get(token)

            if session is None or session.is_expired(now):
                raise SessionNotFoundError()

            if sender_peer_id not in session.peers:
                raise PeerNotInSessionError()

            sender = session.peers[sender_peer_id]
            sender.last_activity = now
            session.touch(cls._ttl_seconds, now=now)

            # Find recipient peer
            recipient = None
            for pid, peer in session.peers.items():
                if pid != sender_peer_id:
                    recipient = peer
                    break

            if recipient is None:
                return False

            if isinstance(data, dict):
                msg = data
            else:
                msg = {
                    "event": "signal",
                    "data": str(data),
                }

            cls._safe_enqueue(recipient.queue, msg)
            return True

    @classmethod
    async def leave(
        cls,
        token: str,
        peer_id: str,
        reason: str = "disconnected",
    ) -> None:
        """Removes peer from room, notifies partner, and closes/cleans up room."""
        async with cls._lock:
            now = time.time()
            session = cls._sessions.get(token)
            if session is None:
                return

            if peer_id in session.peers:
                session.peers.pop(peer_id, None)

            # Notify remaining peer if one exists
            remaining_peers = list(session.peers.values())
            for other_peer in remaining_peers:
                cls._safe_enqueue(other_peer.queue, {
                    "event": "peer_left",
                    "reason": reason,
                })

            # If room is now empty, or an active session peer disconnected, close the session
            if len(remaining_peers) == 0:
                cls._sessions.pop(token, None)
                cls._closed_tokens[token] = now
            else:
                # If one peer left during a 2-party call, mark session closed so it can't be re-joined
                session.is_closed = True
                cls._sessions.pop(token, None)
                cls._closed_tokens[token] = now

            _log.debug("Rendezvous leave: room=%s... peer=%s reason=%s", token[:8], peer_id, reason)

    @classmethod
    async def sweep_expired(cls, now: float | None = None) -> int:
        """Public sweep method to evict expired sessions and clean closed token tombstone cache."""
        async with cls._lock:
            ts = now if now is not None else time.time()
            return cls._sweep_expired_locked(ts)

    @classmethod
    def _sweep_expired_locked(cls, now: float) -> int:
        """Internal helper for eviction; must be called with cls._lock held."""
        evicted = 0

        # Evict expired sessions
        for token, session in list(cls._sessions.items()):
            if session.is_expired(now):
                for peer in session.peers.values():
                    cls._safe_enqueue(peer.queue, {
                        "event": "error",
                        "error": "session_expired",
                        "code": 4002,
                        "message": "Rendezvous session expired",
                    })
                cls._sessions.pop(token, None)
                cls._closed_tokens[token] = now
                evicted += 1

        # Purge tombstone cache entries older than 2x TTL
        cutoff = now - (cls._ttl_seconds * 2.0)
        for token, closed_at in list(cls._closed_tokens.items()):
            if closed_at < cutoff:
                cls._closed_tokens.pop(token, None)

        return evicted

    @classmethod
    def _safe_enqueue(cls, queue: asyncio.Queue[dict[str, Any]], item: dict[str, Any]) -> bool:
        """Safely enqueues item into peer queue with backpressure protection."""
        if queue.full():
            try:
                queue.get_nowait()  # Drop oldest item to prevent hang
            except (asyncio.QueueEmpty, Exception):
                pass
        try:
            queue.put_nowait(item)
            return True
        except Exception:
            return False

    @classmethod
    def get_session(cls, token: str) -> BlindRendezvousSession | None:
        """Diagnostic lookup (read-only)."""
        return cls._sessions.get(token)

    @classmethod
    def get_active_session_count(cls) -> int:
        return len(cls._sessions)

    @classmethod
    def clear_all_for_testing(cls) -> None:
        """Resets all sessions and closed tokens (for test teardown)."""
        cls._sessions.clear()
        cls._closed_tokens.clear()

    @classmethod
    async def reset_all(cls) -> None:
        """Async variant for test teardown."""
        async with cls._lock:
            cls._sessions.clear()
            cls._closed_tokens.clear()
