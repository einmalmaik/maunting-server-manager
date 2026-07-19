from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from config import settings
from services import docker_service
from services.guardian_action_registry import (
    RecoveryContext,
    RecoveryPreconditionError,
    UnsupportedRecoveryAction,
    execute_action,
)
from services.guardian_contract import GuardianConfig


def _guardian(lock_path: str = "runtime/server.lock", protected: list[str] | None = None) -> GuardianConfig:
    return GuardianConfig.model_validate(
        {
            "health_checks": [],
            "recovery": {
                "policies": [],
                "safe_lock_files": [{"path": lock_path, "reason": "synthetic stale lock"}],
            },
            "backups": {"protected_paths": protected or []},
        }
    )


@pytest.fixture()
def server_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "servers"
    (root / "42" / "runtime").mkdir(parents=True)
    monkeypatch.setattr(settings, "servers_dir", str(root))
    return root / "42"


def test_declared_lock_file_removed_only_after_confirmed_stop(
    server_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = server_root / "runtime" / "server.lock"
    lock.write_text("synthetic", encoding="utf-8")
    running = {"value": True}
    monkeypatch.setattr(
        docker_service,
        "inspect_container_state",
        lambda _name: {"running": running["value"], "status": "running" if running["value"] else "exited"},
    )

    def stop(*_args, **_kwargs):
        running["value"] = False
        return {"ok": True}

    monkeypatch.setattr(docker_service, "stop_container", stop)
    monkeypatch.setattr(docker_service, "start_container", lambda _name: {"ok": True})
    result = asyncio.run(
        execute_action(
            "clear_declared_lock_files",
            RecoveryContext(42, "msm-srv-42", _guardian()),
        )
    )
    assert result.ok is True
    assert result.details["removed_files"] == ["runtime/server.lock"]
    assert not lock.exists()


def test_undeclared_and_protected_files_are_never_removed(
    server_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    undeclared = server_root / "runtime" / "other.lock"
    declared = server_root / "runtime" / "server.lock"
    undeclared.write_text("keep", encoding="utf-8")
    declared.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(docker_service, "inspect_container_state", lambda _name: {"running": False})
    with pytest.raises(RecoveryPreconditionError):
        asyncio.run(
            execute_action(
                "clear_declared_lock_files",
                RecoveryContext(42, "msm-srv-42", _guardian(protected=["runtime"])),
            )
        )
    assert undeclared.exists()
    assert declared.exists()


def test_symlink_lock_file_is_rejected(
    server_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = server_root.parent / "outside"
    outside.write_text("keep", encoding="utf-8")
    lock = server_root / "runtime" / "server.lock"
    try:
        lock.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(docker_service, "inspect_container_state", lambda _name: {"running": False})
    with pytest.raises(RecoveryPreconditionError):
        asyncio.run(
            execute_action(
                "clear_declared_lock_files",
                RecoveryContext(42, "msm-srv-42", _guardian()),
            )
        )
    assert outside.exists()


def test_unknown_action_never_falls_back_to_restart(server_root: Path) -> None:
    with pytest.raises(UnsupportedRecoveryAction):
        asyncio.run(execute_action("unknown", RecoveryContext(42, "msm-srv-42", _guardian())))


@pytest.mark.parametrize(
    "path",
    ["../server.lock", "/tmp/server.lock", "runtime/*.lock", "runtime\\server.lock", "runtime/./server.lock"],
)
def test_unsafe_lock_declarations_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        _guardian(path)

