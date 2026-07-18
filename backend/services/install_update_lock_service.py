"""Process-local guard for server install/update jobs.

The guarded work can be SteamCMD, HTTP source downloads, manual-upload setup,
or future blueprint-driven update work. Keep the naming generic so the router
does not encode a SteamCMD-only policy.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


INSTALL_UPDATE_ALREADY_RUNNING = "install_update_already_running"
DEFAULT_INSTALL_UPDATE_LOCK_TTL_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class InstallUpdateLockInfo:
    server_id: int
    operation: str
    acquired_at: float
    expires_at: float


_LOCK = threading.Lock()
_ACTIVE: dict[int | None, InstallUpdateLockInfo] = {}


def try_acquire_install_update_lock(
    server_id: int,
    operation: str,
    *,
    node_id: int | None = None,
    ttl_seconds: int = DEFAULT_INSTALL_UPDATE_LOCK_TTL_SECONDS,
) -> bool:
    """Reserve the install/update slot for a specific node and server.

    Stale process-local locks are replaced after ``ttl_seconds``. This does not
    claim cross-process serialization; production should run one panel worker or
    back this with DB/Redis if multiple API workers are introduced.
    """
    global _ACTIVE
    now = time.monotonic()
    with _LOCK:
        # Prevent concurrent operations on the exact same server
        for k, v in list(_ACTIVE.items()):
            if v.expires_at > now and v.server_id == server_id:
                return False
        
        # Prevent concurrent SteamCMD operations on the exact same node (to avoid SteamCMD locks)
        current = _ACTIVE.get(node_id)
        if current is not None and current.expires_at > now:
            return False

        _ACTIVE[node_id] = InstallUpdateLockInfo(
            server_id=server_id,
            operation=operation,
            acquired_at=now,
            expires_at=now + ttl_seconds,
        )
        return True


def release_install_update_lock(server_id: int) -> None:
    """Release the lock held by ``server_id``.

    A mismatched release is ignored so a late cleanup from an old job cannot
    clear a newer job that replaced a stale lock.
    """
    global _ACTIVE
    with _LOCK:
        for k, v in list(_ACTIVE.items()):
            if v.server_id == server_id:
                _ACTIVE.pop(k, None)


def active_install_update_lock() -> InstallUpdateLockInfo | None:
    """Return the first active non-stale lock (for backward-compatible diagnostic/test checks)."""
    global _ACTIVE
    now = time.monotonic()
    with _LOCK:
        for k, v in list(_ACTIVE.items()):
            if v.expires_at <= now:
                _ACTIVE.pop(k, None)
        if _ACTIVE:
            return list(_ACTIVE.values())[0]
        return None


def reset_install_update_lock_for_tests() -> None:
    global _ACTIVE
    with _LOCK:
        _ACTIVE.clear()


def force_release_install_update_lock(server_id: int) -> bool:
    """Admin/cancel: lock freigeben wenn es zu diesem Server gehoert."""
    global _ACTIVE
    with _LOCK:
        for k, v in list(_ACTIVE.items()):
            if v.server_id == server_id:
                _ACTIVE.pop(k, None)
                return True
        return False


def acquire_install_update_lock_blocking(
    server_id: int,
    operation: str,
    *,
    node_id: int | None = None,
    ttl_seconds: int = DEFAULT_INSTALL_UPDATE_LOCK_TTL_SECONDS,
) -> None:
    """Reserve the install/update slot, blocking until it is free.

    Stale process-local locks are replaced after ``ttl_seconds``.
    """
    while not try_acquire_install_update_lock(server_id, operation, node_id=node_id, ttl_seconds=ttl_seconds):
        time.sleep(1)
