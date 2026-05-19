from __future__ import annotations

from types import SimpleNamespace
import os

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


def test_find_active_log_prefers_conan_saved_logs(tmp_path, monkeypatch):
    original_expanduser = os.path.expanduser
    monkeypatch.setattr(console.os.path, "expanduser", lambda value: str(tmp_path) if value == "~" else original_expanduser(value))
    log_dir = tmp_path / "servers" / "alpha" / "serverfiles" / "ConanSandbox" / "Saved" / "Logs"
    legacy_dir = tmp_path / "servers" / "alpha" / "serverprofile"
    log_dir.mkdir(parents=True)
    legacy_dir.mkdir(parents=True)
    legacy_log = legacy_dir / "old.RPT"
    conan_log = log_dir / "ConanSandbox.log"
    legacy_log.write_text("legacy\n", encoding="utf-8")
    conan_log.write_text("conan\n", encoding="utf-8")
    os.utime(legacy_log, (1_000, 1_000))
    os.utime(conan_log, (2_000, 2_000))

    assert console._find_active_log("alpha") == conan_log
