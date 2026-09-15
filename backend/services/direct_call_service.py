"""Ephemeral authorization for direct-call signaling invitations."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

DIRECT_CALL_TOKEN_TTL_SECONDS = 120.0


@dataclass(frozen=True)
class DirectCallInvite:
    caller_id: int
    recipient_id: int
    expires_at: float


class DirectCallInviteService:
    """Keeps direct-call authorization ephemeral and out of the database."""

    _invites: dict[str, DirectCallInvite] = {}
    _consumed: dict[str, float] = {}
    _lock = threading.Lock()

    @classmethod
    def issue(cls, caller_id: int, recipient_id: int) -> str:
        token = secrets.token_urlsafe(32)
        with cls._lock:
            cls._sweep_locked(time.time())
            cls._invites[token] = DirectCallInvite(
                caller_id=caller_id,
                recipient_id=recipient_id,
                expires_at=time.time() + DIRECT_CALL_TOKEN_TTL_SECONDS,
            )
        return token

    @classmethod
    def authorize(cls, token: str, user_id: int) -> str | None:
        """Return the user's call role, or ``None`` for an unknown/unauthorized token."""
        with cls._lock:
            invite = cls._invites.get(token)
            if not invite or invite.expires_at <= time.time():
                cls._invites.pop(token, None)
                return None
            if user_id == invite.caller_id:
                return "initiator"
            if user_id == invite.recipient_id:
                return "receiver"
            return None

    @classmethod
    def is_known(cls, token: str) -> bool:
        with cls._lock:
            invite = cls._invites.get(token)
            if not invite or invite.expires_at <= time.time():
                cls._invites.pop(token, None)
                return False
            return True

    @classmethod
    def reject(cls, token: str, user_id: int) -> int | None:
        """Consume an invitation when the recipient declines it."""
        with cls._lock:
            invite = cls._invites.get(token)
            if not invite or invite.expires_at <= time.time() or invite.recipient_id != user_id:
                if invite and invite.expires_at <= time.time():
                    cls._invites.pop(token, None)
                return None
            cls._invites.pop(token, None)
            cls._consumed[token] = time.time()
            return invite.caller_id

    @classmethod
    def cancel(cls, token: str, user_id: int) -> int | None:
        """Consume an invitation when the caller hangs up before accept."""
        with cls._lock:
            invite = cls._invites.get(token)
            if not invite or invite.expires_at <= time.time() or invite.caller_id != user_id:
                if invite and invite.expires_at <= time.time():
                    cls._invites.pop(token, None)
                return None
            cls._invites.pop(token, None)
            cls._consumed[token] = time.time()
            return invite.recipient_id

    @classmethod
    def is_consumed(cls, token: str) -> bool:
        """True once an invitation was explicitly rejected or cancelled."""
        with cls._lock:
            consumed_at = cls._consumed.get(token)
            if consumed_at is None:
                return False
            if time.time() - consumed_at > DIRECT_CALL_TOKEN_TTL_SECONDS * 2:
                cls._consumed.pop(token, None)
                return False
            return True

    @classmethod
    def clear_all_for_testing(cls) -> None:
        with cls._lock:
            cls._invites.clear()
            cls._consumed.clear()

    @classmethod
    def _sweep_locked(cls, now: float) -> None:
        for token, invite in list(cls._invites.items()):
            if invite.expires_at <= now:
                cls._invites.pop(token, None)
