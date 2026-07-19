"""Durable, owner-only Guardian state outside workload directories."""

from __future__ import annotations

import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

from config import settings


STATE_SCHEMA_VERSION = 1
STATE_FILE_NAMES = frozenset(
    {"desired-state.json", "observed-state.json", "runtime-state.json"}
)


class GuardianStateError(RuntimeError):
    """Base error for durable Guardian state."""


class GuardianStateSecurityError(GuardianStateError):
    """Raised when a state path is unsafe or has insecure object type."""


class CorruptedGuardianStateError(GuardianStateError):
    """Raised after corrupt state was retained for diagnosis."""

    def __init__(self, path: Path, retained_path: Path) -> None:
        super().__init__(f"Guardian state is corrupted: {path.name}")
        self.path = path
        self.retained_path = retained_path


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _existing_components(path: Path):
    current = Path(path.anchor) if path.anchor else Path()
    for part in path.parts[1:] if path.anchor else path.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            yield current


def _reject_symlink_components(path: Path) -> None:
    for component in _existing_components(path):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise GuardianStateSecurityError(
                f"Guardian state path contains a symlink: {component.name}"
            )


def _validate_server_id(server_id: int | str) -> int:
    raw = str(server_id).strip()
    if not raw.isdigit() or raw.startswith("0"):
        raise GuardianStateSecurityError("server_id must be a positive integer")
    value = int(raw)
    if value <= 0:
        raise GuardianStateSecurityError("server_id must be a positive integer")
    return value


class GuardianStateStore:
    def __init__(self, root: str | Path | None = None) -> None:
        configured = Path(root) if root is not None else Path(settings.guardian_state_dir)
        self.root = _absolute_without_resolving(configured)

    def ensure_root(self) -> Path:
        _reject_symlink_components(self.root)
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        _reject_symlink_components(self.root)
        mode = self.root.lstat().st_mode
        if not stat.S_ISDIR(mode):
            raise GuardianStateSecurityError("Guardian root is not a directory")
        os.chmod(self.root, 0o700)
        return self.root

    def server_dir(self, server_id: int | str) -> Path:
        sid = _validate_server_id(server_id)
        root = self.ensure_root()
        target = root / str(sid)
        if target.is_symlink():
            raise GuardianStateSecurityError("Guardian server directory is a symlink")
        target.mkdir(mode=0o700, exist_ok=True)
        if target.is_symlink() or not target.is_dir():
            raise GuardianStateSecurityError("Guardian server path is not a directory")
        os.chmod(target, 0o700)
        return target

    def state_path(self, server_id: int | str, file_name: str) -> Path:
        if file_name not in STATE_FILE_NAMES:
            raise GuardianStateSecurityError("unsupported Guardian state file")
        return self.server_dir(server_id) / file_name

    def write_json(
        self,
        server_id: int | str,
        file_name: str,
        value: dict[str, Any],
    ) -> Path:
        if not isinstance(value, dict) or value.get("schema_version") != STATE_SCHEMA_VERSION:
            raise GuardianStateError("Guardian state requires schema_version=1")
        destination = self.state_path(server_id, file_name)
        if destination.is_symlink():
            raise GuardianStateSecurityError("Guardian state file is a symlink")

        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temporary, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            # Windows does not allow replacing an open file.  Durability is
            # already guaranteed by fsync, so close before the atomic rename.
            os.close(fd)
            fd = -1
            if destination.is_symlink():
                raise GuardianStateSecurityError("Guardian state file became a symlink")
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
            self._fsync_directory(destination.parent)
        finally:
            try:
                if fd >= 0:
                    os.close(fd)
            except OSError:
                pass
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return destination

    def read_json(
        self,
        server_id: int | str,
        file_name: str,
    ) -> dict[str, Any] | None:
        path = self.state_path(server_id, file_name)
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink() or not path.is_file():
            raise GuardianStateSecurityError("Guardian state file is unsafe")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
            try:
                with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
                    data = json.load(stream)
            finally:
                os.close(fd)
            if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
                raise ValueError("unsupported or missing state schema")
            return data
        except GuardianStateSecurityError:
            raise
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            retained = self._retain_corrupted(path)
            raise CorruptedGuardianStateError(path, retained) from exc

    def _retain_corrupted(self, path: Path) -> Path:
        if path.is_symlink():
            raise GuardianStateSecurityError("corrupt Guardian state is a symlink")
        retained = path.with_name(
            f"{path.name}.corrupt-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        )
        os.replace(path, retained)
        os.chmod(retained, 0o600)
        self._fsync_directory(path.parent)
        return retained

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            # Windows and a few filesystems do not support directory fsync.
            pass
        finally:
            os.close(fd)


class GuardianProcessLock:
    """OS-owned process lock for one Guardian state directory."""

    def __init__(self, store: GuardianStateStore) -> None:
        self.store = store
        self.path = store.ensure_root() / ".agent.lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        if self.path.is_symlink():
            raise GuardianStateSecurityError("Guardian process lock is a symlink")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        os.fchmod(fd, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                    os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            os.close(fd)
            raise GuardianStateError(
                "another MSM Agent process already owns the Guardian state directory"
            ) from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd = self._fd
        self._fd = None
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "GuardianProcessLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
