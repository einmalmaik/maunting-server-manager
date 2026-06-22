"""Tests for the fix: mod install / reinstall-all must not hold a DB session while
blocked on the install/update lock. Otherwise parallel installs + UI polling
exhaust the QueuePool and yield 500 responses.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from sqlalchemy.exc import TimeoutError as SAQueueTimeout

from models import Mod, Server


def test_install_mod_bg_does_not_block_with_open_session(test_server: Server, db):
    """install_mod_bg must acquire the install lock *before* opening a DB session.
    The lock used to be acquired *inside* the session, causing the polling UI
    to time out (QueuePool limit) when many mods were pending.
    """
    from routers import mods

    call_order: list[str] = []

    fake_session = _FakeSession()

    def _lock(*_a, **_kw):
        call_order.append("lock")
        return None

    def _release(*_a, **_kw):
        call_order.append("release")
        return None

    def _session_local():
        call_order.append("session")
        return fake_session

    fake_plugin = type(
        "P",
        (),
        {
            "supports_mods": True,
            "install_mod": lambda self, server, wid: {"ok": True},
        },
    )()

    def _get_plugin(_gt):
        call_order.append("get_plugin")
        return fake_plugin

    def _mark_installing(_sid, _wid, _a):
        call_order.append("mark_installing")

    def _mark_installed(_sid, _wid):
        call_order.append("mark_installed")

    fake_session.query = lambda *_a, **_kw: type(  # type: ignore[attr-defined]
        "Q", (), {"filter": lambda *a, **kw: type("R", (), {"first": lambda *a, **kw: test_server})()}
    )()
    fake_session.close = lambda: call_order.append("close")  # type: ignore[assignment]

    with patch.object(mods, "acquire_install_update_lock_blocking", _lock), \
         patch.object(mods, "release_install_update_lock", _release), \
         patch.object(mods, "get_plugin", _get_plugin), \
         patch.object(mods, "mark_mod_installing", _mark_installing), \
         patch.object(mods, "mark_mod_installed", _mark_installed), \
         patch.object(mods, "updater") as mock_updater:
        mock_updater.update_mod_metadata_after_success.return_value = None
        with patch("routers.mods.SessionLocal", _session_local):
            mods.install_mod_bg(test_server.id, "12345", "install")

    # Lock must be acquired first; session must be closed even on success.
    assert call_order[0] == "lock"
    assert "close" in call_order


def test_list_mods_skips_refresh_during_active_jobs(client, test_server, owner_cookies, db):
    """list_mods should not trigger the heavy workshop update check while
    mods are pending/installing on that server (avoids QueuePool pressure)."""
    db.add(Mod(server_id=test_server.id, workshop_id="1", install_status="pending"))
    db.commit()

    with patch("routers.mods._refresh_mod_update_availability") as mock_refresh:
        resp = client.get(f"/api/mods/{test_server.id}", cookies=owner_cookies)
        assert resp.status_code == 200
        mock_refresh.assert_not_called()


def test_list_mods_runs_refresh_when_idle(client, test_server, owner_cookies, db):
    db.add(Mod(server_id=test_server.id, workshop_id="1", install_status="installed"))
    db.commit()

    with patch("routers.mods._refresh_mod_update_availability") as mock_refresh:
        resp = client.get(f"/api/mods/{test_server.id}", cookies=owner_cookies)
        assert resp.status_code == 200
        mock_refresh.assert_called_once()


class _FakeSession:
    def __init__(self):
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        self._closed = True
