from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from services.guardian_state_store import (
    CorruptedGuardianStateError,
    GuardianProcessLock,
    GuardianStateError,
    GuardianStateSecurityError,
    GuardianStateStore,
)


def test_atomic_state_write_round_trip_and_permissions(tmp_path: Path) -> None:
    store = GuardianStateStore(tmp_path / "guardian")
    payload = {"schema_version": 1, "server_id": 42, "generation": 3}
    path = store.write_json(42, "desired-state.json", payload)

    assert store.read_json(42, "desired-state.json") == payload
    assert not list(path.parent.glob("*.tmp"))
    if os.name != "nt":
        assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_state_requires_known_file_and_schema(tmp_path: Path) -> None:
    store = GuardianStateStore(tmp_path / "guardian")
    with pytest.raises(GuardianStateSecurityError):
        store.write_json(1, "../state.json", {"schema_version": 1})
    with pytest.raises(GuardianStateError):
        store.write_json(1, "runtime-state.json", {"schema_version": 2})


def test_corrupt_state_is_retained_and_never_reset(tmp_path: Path) -> None:
    store = GuardianStateStore(tmp_path / "guardian")
    path = store.state_path(5, "runtime-state.json")
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(CorruptedGuardianStateError) as caught:
        store.read_json(5, "runtime-state.json")

    assert not path.exists()
    assert caught.value.retained_path.is_file()
    assert caught.value.retained_path.read_text(encoding="utf-8") == "{broken"


def test_symlinked_state_file_is_rejected(tmp_path: Path) -> None:
    store = GuardianStateStore(tmp_path / "guardian")
    target = tmp_path / "outside.json"
    target.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    state_path = store.state_path(1, "desired-state.json")
    try:
        state_path.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(GuardianStateSecurityError):
        store.read_json(1, "desired-state.json")


def test_only_one_process_lock_owner_is_allowed(tmp_path: Path) -> None:
    store = GuardianStateStore(tmp_path / "guardian")
    first = GuardianProcessLock(store)
    second = GuardianProcessLock(store)
    first.acquire()
    try:
        with pytest.raises(GuardianStateError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()

