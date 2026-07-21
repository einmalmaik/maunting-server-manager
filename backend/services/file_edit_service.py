"""Small helpers for conflict-safe text editing and filesystem metadata.

Callers must resolve and authorize the target path before invoking this module.
No absolute path is ever returned to API clients.
"""
from __future__ import annotations

import hashlib
import os
import stat as stat_module
import tempfile
import threading
from pathlib import Path
from typing import Any


class FileRevisionConflict(Exception):
    def __init__(self, current_revision: str | None) -> None:
        super().__init__("File changed since it was opened")
        self.current_revision = current_revision


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(target: Path) -> threading.Lock:
    key = str(target)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def content_revision(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _identity_name(value: int, *, group: bool) -> str | None:
    try:
        if os.name != "posix":
            return None
        if group:
            import grp

            return grp.getgrgid(value).gr_name
        import pwd

        return pwd.getpwuid(value).pw_name
    except (KeyError, ImportError, OSError):
        return None


def metadata(target: Path) -> dict[str, Any]:
    info = target.stat(follow_symlinks=False)
    return {
        "size": info.st_size if target.is_file() else 0,
        "modified": info.st_mtime,
        "mode": format(stat_module.S_IMODE(info.st_mode), "04o"),
        "owner": _identity_name(info.st_uid, group=False),
        "group": _identity_name(info.st_gid, group=True),
    }


def read_text(target: Path) -> dict[str, Any]:
    data = target.read_bytes()
    return {
        "content": data.decode("utf-8", errors="replace"),
        "revision": content_revision(data),
        **metadata(target),
    }


def write_text(
    target: Path,
    content: str,
    *,
    expected_revision: str | None = None,
    create_only: bool = False,
) -> dict[str, Any]:
    """Atomically replace a text file after an optimistic revision check."""
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    with _lock_for(target):
        if create_only and target.exists():
            raise FileExistsError("Target file already exists")
        current_revision = content_revision(target.read_bytes()) if target.is_file() else None
        if expected_revision is not None and current_revision != expected_revision:
            raise FileRevisionConflict(current_revision)

        previous_mode = stat_module.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=target.parent,
                prefix=".msm-edit-",
            ) as temp_file:
                os.fchmod(temp_file.fileno(), 0o600)
                temp_file.write(encoded)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)
            os.replace(temp_path, target)
            temp_path = None
            os.chmod(target, previous_mode)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    return {
        "revision": content_revision(encoded),
        **metadata(target),
    }
