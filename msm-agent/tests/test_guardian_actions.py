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


def _guardian(
    lock_path: str = "runtime/server.lock",
    protected: list[str] | None = None,
    *,
    before_risky_action: bool = True,
) -> GuardianConfig:
    return GuardianConfig.model_validate(
        {
            "health_checks": [],
            "recovery": {
                "policies": [],
                "safe_lock_files": [{"path": lock_path, "reason": "synthetic stale lock"}],
            },
            "backups": {
                "before_risky_action": before_risky_action,
                "protected_paths": protected or [],
            },
        }
    )


@pytest.fixture()
def server_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "servers"
    (root / "42" / "runtime").mkdir(parents=True)
    monkeypatch.setattr(settings, "servers_dir", str(root))
    monkeypatch.setattr(settings, "guardian_state_dir", str(tmp_path / "guardian"))
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
    assert result.details["backup_created"] is True
    backup = (
        settings.guardian_path()
        / "recovery-backups"
        / "42"
        / result.details["backup_id"]
        / "runtime"
        / "server.lock"
    )
    assert backup.read_text(encoding="utf-8") == "synthetic"
    assert not lock.exists()


def test_lock_file_backup_can_be_explicitly_disabled(
    server_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = server_root / "runtime" / "server.lock"
    lock.write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(docker_service, "inspect_container_state", lambda _name: {"running": False})

    result = asyncio.run(
        execute_action(
            "clear_declared_lock_files",
            RecoveryContext(
                42,
                "msm-srv-42",
                _guardian(before_risky_action=False),
            ),
        )
    )

    assert result.details["backup_created"] is False
    assert not (settings.guardian_path() / "recovery-backups" / "42").exists()


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


def test_failed_lock_file_recovery_restarts_a_previously_running_container(
    server_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = server_root / "runtime" / "server.lock"
    lock.write_text("keep", encoding="utf-8")
    running = {"value": True}
    start_calls: list[str] = []

    monkeypatch.setattr(
        docker_service,
        "inspect_container_state",
        lambda _name: {"running": running["value"]},
    )

    def stop(*_args, **_kwargs):
        running["value"] = False
        return {"ok": True}

    def start(name: str):
        start_calls.append(name)
        running["value"] = True
        return {"ok": True}

    monkeypatch.setattr(docker_service, "stop_container", stop)
    monkeypatch.setattr(docker_service, "start_container", start)

    with pytest.raises(RecoveryPreconditionError):
        asyncio.run(
            execute_action(
                "clear_declared_lock_files",
                RecoveryContext(
                    42,
                    "msm-srv-42",
                    _guardian(protected=["runtime"]),
                ),
            )
        )

    assert start_calls == ["msm-srv-42"]
    assert running["value"] is True
    assert lock.exists()


@pytest.mark.parametrize(
    "path",
    ["../server.lock", "/tmp/server.lock", "runtime/*.lock", "runtime\\server.lock", "runtime/./server.lock"],
)
def test_unsafe_lock_declarations_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        _guardian(path)


def test_unknown_recovery_match_is_rejected_by_agent_contract() -> None:
    raw = _guardian().model_dump(mode="json")
    raw["recovery"]["policies"] = [
        {"match": "typoed_probe_failure", "action": "restart"}
    ]
    with pytest.raises(ValueError):
        GuardianConfig.model_validate(raw)
