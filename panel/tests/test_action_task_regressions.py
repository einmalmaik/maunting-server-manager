from __future__ import annotations

import builtins
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.api import actions
from app import shell


def test_invoke_core_action_async_rejects_second_running_task(monkeypatch, tmp_path):
    server_dir = tmp_path / "servers" / "alpha"
    server_dir.mkdir(parents=True)

    monkeypatch.setattr(shell, "get_server_dir", lambda server_name=None: server_dir)

    class _FakeThread:
        def __init__(self, target, daemon=False):
            self._target = target
            self.daemon = daemon

        def start(self):
            return None

    monkeypatch.setattr(shell.threading, "Thread", _FakeThread)

    shell.invoke_core_action_async("backup", server_name="alpha")

    with pytest.raises(RuntimeError) as exc:
        shell.invoke_core_action_async("update", server_name="alpha")

    assert "already running" in str(exc.value)
    assert (server_dir / ".panel_task.lock").exists()
    assert json.loads((server_dir / ".panel_task.json").read_text(encoding="utf-8"))["status"] == "running"


def test_invoke_core_action_async_reclaims_stale_task_lock(monkeypatch, tmp_path):
    server_dir = tmp_path / "servers" / "alpha"
    server_dir.mkdir(parents=True)
    task_file = server_dir / ".panel_task.json"
    task_lock = server_dir / ".panel_task.lock"
    task_file.write_text(json.dumps({"status": "finished"}), encoding="utf-8")
    task_lock.write_text("stale", encoding="utf-8")

    monkeypatch.setattr(shell, "get_server_dir", lambda server_name=None: server_dir)

    class _FakeThread:
        def __init__(self, target, daemon=False):
            self._target = target
            self.daemon = daemon

        def start(self):
            return None

    monkeypatch.setattr(shell.threading, "Thread", _FakeThread)

    shell.invoke_core_action_async("backup", server_name="alpha")

    task = json.loads(task_file.read_text(encoding="utf-8"))
    assert task["action"] == "backup"
    assert task["status"] == "running"
    assert task_lock.exists()


def test_invoke_core_action_async_allows_parallel_distinct_task_channels(monkeypatch, tmp_path):
    server_dir = tmp_path / "servers" / "alpha"
    server_dir.mkdir(parents=True)

    monkeypatch.setattr(shell, "get_server_dir", lambda server_name=None: server_dir)

    class _FakeThread:
        def __init__(self, target, daemon=False):
            self._target = target
            self.daemon = daemon

        def start(self):
            return None

    monkeypatch.setattr(shell.threading, "Thread", _FakeThread)

    shell.invoke_core_action_async("restart", server_name="alpha", task_channel="default")
    shell.invoke_core_action_async("workshop", server_name="alpha", task_channel="workshop")

    default_task = json.loads((server_dir / ".panel_task.json").read_text(encoding="utf-8"))
    workshop_task = json.loads((server_dir / ".panel_task.workshop.json").read_text(encoding="utf-8"))

    assert default_task["action"] == "restart"
    assert default_task["channel"] == "default"
    assert workshop_task["action"] == "workshop"
    assert workshop_task["channel"] == "workshop"
    assert (server_dir / ".panel_task.lock").exists()
    assert (server_dir / ".panel_task.workshop.lock").exists()


def test_get_action_status_uses_requested_task_channel(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        actions,
        "fetch_action_task",
        lambda server_name=None, task_channel="default": calls.append(("task", task_channel)) or {"action": task_channel},
    )
    monkeypatch.setattr(
        actions,
        "fetch_action_log",
        lambda server_name=None, task_channel="default": calls.append(("log", task_channel)) or [task_channel],
    )

    result = actions.get_action_status(channel="workshop", server="alpha", user=None)

    assert result == {"task": {"action": "workshop"}, "log": ["workshop"]}
    assert calls == [("task", "workshop"), ("log", "workshop")]


def test_get_base_args_forwards_panel_skip_prestart_through_sudo(monkeypatch):
    monkeypatch.setenv("PANEL_RUNTIME_USER", "dayz")
    monkeypatch.setattr(
        shell,
        "get_settings",
        lambda: SimpleNamespace(conan_manager_path="/opt/conanserver.sh"),
    )

    import getpass

    monkeypatch.setattr(getpass, "getuser", lambda: "root")

    args = shell._get_base_args(
        server_name="alpha",
        forwarded_env={"PANEL_SKIP_PRESTART": "1"},
    )

    assert args == [
        "sudo",
        "-n",
        "-u",
        "dayz",
        "env",
        "PANEL_SKIP_PRESTART=1",
        "bash",
        "/opt/conanserver.sh",
        "--server",
        "alpha",
    ]


def test_invoke_core_action_async_cleans_lock_when_task_file_write_fails(monkeypatch, tmp_path):
    server_dir = tmp_path / "servers" / "alpha"
    server_dir.mkdir(parents=True)
    task_file = server_dir / ".panel_task.json"
    task_lock = server_dir / ".panel_task.lock"

    monkeypatch.setattr(shell, "get_server_dir", lambda server_name=None: server_dir)

    original_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if Path(path) == task_file and args and args[0].startswith("w"):
            raise OSError("disk full")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)

    with pytest.raises(OSError):
        shell.invoke_core_action_async("backup", server_name="alpha")

    assert not task_lock.exists()
