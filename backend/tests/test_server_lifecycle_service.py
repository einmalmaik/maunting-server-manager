"""
Unit tests for the central server_lifecycle_service.

These tests focus on the new unified restart path and lock behavior.
They are isolated: heavy external dependencies (plugin, docker, firewall, iptables)
are mocked so we test the orchestration and locking logic itself.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from models import Server
from services.server_lifecycle_service import (
    get_server_lifecycle_lock,
    restart_server_with_updates,
)


def test_get_server_lifecycle_lock_returns_same_instance_for_same_id():
    """Basic contract: same server_id always yields the exact same lock instance."""
    lock1 = get_server_lifecycle_lock(42)
    lock2 = get_server_lifecycle_lock(42)
    assert lock1 is lock2
    assert isinstance(lock1, asyncio.Lock)


def test_get_server_lifecycle_lock_different_ids_are_different():
    """Different servers must not share a lock (would serialize unrelated operations)."""
    lock_a = get_server_lifecycle_lock(1)
    lock_b = get_server_lifecycle_lock(2)
    assert lock_a is not lock_b


def test_restart_server_with_updates_raises_on_unsupported_game_type():
    """Early validation: unknown game_type must fail fast with clear error."""
    from fastapi import HTTPException

    fake_server = Server(id=1, game_type="nonexistent_game_xyz")
    fake_db = MagicMock(spec=Session)

    with pytest.raises(HTTPException) as exc:
        # We can call it directly; it fails before any async work
        import asyncio
        asyncio.run(restart_server_with_updates(fake_db, fake_server))

    assert exc.value.status_code == 400
    assert "nicht unterstützt" in str(exc.value.detail)