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
_ACTIVE: InstallUpdateLockInfo | None = None


def try_acquire_install_update_lock(
    server_id: int,
    operation: str,
    *,
    ttl_seconds: int = DEFAULT_INSTALL_UPDATE_LOCK_TTL_SECONDS,
) -> bool:
    """Reserve the single install/update slot.

    Stale process-local locks are replaced after ``ttl_seconds``. This does not
    claim cross-process serialization; production should run one panel worker or
    back this with DB/Redis if multiple API workers are introduced.
    """
    global _ACTIVE
    now = time.monotonic()
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.expires_at > now:
            return False
        _ACTIVE = InstallUpdateLockInfo(
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
        if _ACTIVE is not None and _ACTIVE.server_id == server_id:
            _ACTIVE = None


def active_install_update_lock() -> InstallUpdateLockInfo | None:
    """Return the current non-stale lock for tests and diagnostics."""
    global _ACTIVE
    now = time.monotonic()
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.expires_at <= now:
            _ACTIVE = None
        return _ACTIVE


def reset_install_update_lock_for_tests() -> None:
    global _ACTIVE
    with _LOCK:
        _ACTIVE = None


def acquire_install_update_lock_blocking(
    server_id: int,
    operation: str,
    *,
    ttl_seconds: int = DEFAULT_INSTALL_UPDATE_LOCK_TTL_SECONDS,
) -> None:
    """Reserve the single install/update slot, blocking until it is free.

    Stale process-local locks are replaced after ``ttl_seconds``.
    """
    while not try_acquire_install_update_lock(server_id, operation, ttl_seconds=ttl_seconds):
        time.sleep(1)
