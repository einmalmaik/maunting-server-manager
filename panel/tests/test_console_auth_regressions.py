from __future__ import annotations

from types import SimpleNamespace

from app.api import console
from app.models import User


def test_console_token_round_trip_survives_without_in_memory_store(monkeypatch):
    monkeypatch.setattr(console, "get_settings", lambda: SimpleNamespace(secret_key="test-secret"))
    monkeypatch.setattr(console.time, "time", lambda: 1_000.0)

    token = console._issue_token(user_id=7, source="log", server_name="alpha")
    entry = console._consume_token(token)

    assert entry is not None
    assert entry.user_id == 7
    assert entry.source == "log"
    assert entry.server_name == "alpha"


def test_console_token_rejects_expired_payload(monkeypatch):
    monkeypatch.setattr(console, "get_settings", lambda: SimpleNamespace(secret_key="test-secret"))
    now = {"value": 1_000.0}
    monkeypatch.setattr(console.time, "time", lambda: now["value"])

    token = console._issue_token(user_id=7, source="tmux", server_name="alpha")
    now["value"] = 2_000.0

    assert console._consume_token(token) is None


def test_console_token_authorization_rejects_inactive_user(monkeypatch):
    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, user_id):
            return User(id=user_id, username="disabled", password_hash="x", role="user", permissions=None, is_active=False)

    monkeypatch.setattr(console, "SessionLocal", lambda: DummySession())

    entry = console._ConsoleToken(user_id=7, source="log", server_name="alpha")

    assert console._is_console_token_authorized(entry) is False


def test_console_token_authorization_requires_matching_permission(monkeypatch):
    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, user_id):
            return User(
                id=user_id,
                username="viewer",
                password_hash="x",
                role="user",
                permissions='["console.view.log"]',
                is_active=True,
            )

    monkeypatch.setattr(console, "SessionLocal", lambda: DummySession())

    log_entry = console._ConsoleToken(user_id=7, source="log", server_name="alpha")
    tmux_entry = console._ConsoleToken(user_id=7, source="tmux", server_name="alpha")

    assert console._is_console_token_authorized(log_entry) is True
    assert console._is_console_token_authorized(tmux_entry) is False
