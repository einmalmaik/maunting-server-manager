"""Serialize every mutating Agent operation per managed server.

The coordinator deliberately uses one ``threading.Lock`` per server.  The same
lock can therefore be used by synchronous FastAPI handlers, worker threads and
the asynchronous Guardian loop.  Nested calls in the same logical execution
context are allowed so a multi-step recovery can hold the lock while calling
the normal Docker service methods.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import AsyncIterator, Iterator


class InvalidServerOperation(ValueError):
    """Raised when an operation is not scoped to a valid server ID."""


_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_HELD_BY_CONTEXT: ContextVar[dict[int, tuple[str, int]]] = ContextVar(
    "msm_agent_operations_held",
    default={},
)


def _validated_server_id(server_id: int | str) -> int:
    try:
        value = int(server_id)
    except (TypeError, ValueError) as exc:
        raise InvalidServerOperation("server_id must be a positive integer") from exc
    if value <= 0 or str(server_id).strip() != str(value):
        raise InvalidServerOperation("server_id must be a positive integer")
    return value


def server_id_from_container_name(name: str, prefix: str) -> int:
    """Return the numeric ID from the one allowed managed-container format."""
    if not isinstance(name, str) or not name.startswith(prefix):
        raise InvalidServerOperation("invalid managed container name")
    suffix = name[len(prefix) :]
    if not suffix.isdigit() or not suffix or suffix.startswith("0"):
        raise InvalidServerOperation("invalid managed container name")
    return _validated_server_id(suffix)


def _lock_for(server_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(server_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[server_id] = lock
        return lock


def _owner_identity() -> tuple[str, int]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is not None:
        return ("task", id(task))
    return ("thread", threading.get_ident())


def _is_nested(server_id: int, owner: tuple[str, int]) -> bool:
    return _HELD_BY_CONTEXT.get().get(server_id) == owner


def _mark_held(server_id: int, owner: tuple[str, int]):
    held = dict(_HELD_BY_CONTEXT.get())
    held[server_id] = owner
    return _HELD_BY_CONTEXT.set(held)


@contextmanager
def operation(server_id: int | str) -> Iterator[None]:
    """Acquire the shared per-server mutation lock synchronously."""
    sid = _validated_server_id(server_id)
    owner = _owner_identity()
    if _is_nested(sid, owner):
        yield
        return

    lock = _lock_for(sid)
    lock.acquire()
    token = _mark_held(sid, owner)
    try:
        yield
    finally:
        _HELD_BY_CONTEXT.reset(token)
        lock.release()


@asynccontextmanager
async def operation_async(server_id: int | str) -> AsyncIterator[None]:
    """Acquire the shared per-server mutation lock without blocking the loop."""
    sid = _validated_server_id(server_id)
    owner = _owner_identity()
    if _is_nested(sid, owner):
        yield
        return

    lock = _lock_for(sid)
    await asyncio.to_thread(lock.acquire)
    token = _mark_held(sid, owner)
    try:
        yield
    finally:
        _HELD_BY_CONTEXT.reset(token)
        lock.release()


def is_operation_active(server_id: int | str) -> bool:
    """Return whether another logical context currently owns the server lock."""
    sid = _validated_server_id(server_id)
    owner = _owner_identity()
    if _is_nested(sid, owner):
        return False
    lock = _lock_for(sid)
    acquired = lock.acquire(blocking=False)
    if acquired:
        lock.release()
        return False
    return True


def reset_operation_coordinator_for_tests() -> None:
    """Clear idle locks.  Active locks are intentionally never discarded."""
    with _LOCKS_GUARD:
        active = [sid for sid, lock in _LOCKS.items() if lock.locked()]
        if active:
            raise RuntimeError("cannot reset operation coordinator while locks are active")
        _LOCKS.clear()

