"""Filesystem operations with strict path-traversal protection.

All paths are resolved with realpath/resolve and must stay inside
``MSM_SERVERS_DIR / server_id``. Symlink escapes and ``..`` segments are rejected.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


class PathEscapeError(Exception):
    """Raised when a path would leave the allowed server root."""

    def __init__(self, message: str = "Path outside allowed server directory") -> None:
        super().__init__(message)
        self.message = message


class PathValidationError(Exception):
    """Raised for malformed relative paths (absolute, empty server_id, etc.)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def server_root(server_id: str | int) -> Path:
    """Return the absolute root directory for a server_id under MSM_SERVERS_DIR."""
    sid = str(server_id).strip()
    if not sid or sid in {".", ".."} or "/" in sid or "\\" in sid or ".." in sid:
        raise PathValidationError("Invalid server_id")
    base = settings.servers_path()
    root = (base / sid).resolve(strict=False)
    try:
        root.relative_to(base)
    except ValueError as exc:
        raise PathEscapeError("server_id escapes servers directory") from exc
    return root


def safe_path(server_id: str | int, rel_path: str) -> Path:
    """Resolve ``rel_path`` strictly inside the server root.

    - Rejects absolute paths.
    - Rejects ``..`` segments before resolve (defense in depth).
    - After resolve (symlinks expanded), requires path under server root.
    """
    if rel_path is None:
        rel_path = ""
    # Normalize empty / "." to server root
    rel = rel_path.strip().replace("\\", "/")
    if rel.startswith("/"):
        raise PathValidationError("Absolute paths are not allowed")
    parts = Path(rel).parts if rel else ()
    if any(p == ".." for p in parts):
        raise PathValidationError("Path traversal (..) is not allowed")

    root = server_root(server_id)
    candidate = (root / rel).resolve(strict=False) if rel else root
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError() from exc
    return candidate


def list_dir(server_id: str | int, rel_path: str = "") -> list[dict[str, Any]]:
    target = safe_path(server_id, rel_path)
    if not target.exists():
        raise FileNotFoundError("Path not found")
    if not target.is_dir():
        raise NotADirectoryError("Not a directory")

    entries: list[dict[str, Any]] = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            # Re-check each entry stays inside root (symlink defense)
            entry_resolved = entry.resolve(strict=False)
            entry_resolved.relative_to(server_root(server_id))
        except (ValueError, OSError):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
            size = stat.st_size if entry.is_file() else 0
            mtime = int(stat.st_mtime)
        except OSError:
            size = 0
            mtime = 0
        entries.append(
            {
                "name": entry.name,
                "path": str(entry.relative_to(server_root(server_id))).replace("\\", "/"),
                "is_dir": entry.is_dir(),
                "size": size,
                "mtime": mtime,
            }
        )
    return entries


def read_text(server_id: str | int, rel_path: str) -> str:
    target = safe_path(server_id, rel_path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("File not found")
    if target.stat().st_size > settings.max_read_size:
        raise ValueError(f"File exceeds max read size ({settings.max_read_size} bytes)")
    return target.read_text(encoding="utf-8", errors="replace")


def write_text(server_id: str | int, rel_path: str, content: str) -> None:
    target = safe_path(server_id, rel_path)
    parent = target.parent
    # Parent must stay inside server root (mkdir does not re-validate)
    root = server_root(server_id)
    try:
        parent.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise PathEscapeError() from exc
    parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def delete_path(server_id: str | int, rel_path: str) -> None:
    if not rel_path or rel_path.strip() in {".", ""}:
        raise PathValidationError("Cannot delete server root")
    target = safe_path(server_id, rel_path)
    if not target.exists():
        raise FileNotFoundError("Path not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def rename_path(server_id: str | int, old_path: str, new_path: str) -> None:
    src = safe_path(server_id, old_path)
    dst = safe_path(server_id, new_path)
    if not src.exists():
        raise FileNotFoundError("Source not found")
    if dst.exists():
        raise FileExistsError("Destination already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)


def create_dir(server_id: str | int, rel_path: str) -> None:
    if not rel_path or rel_path.strip() in {".", ""}:
        raise PathValidationError("Invalid directory path")
    target = safe_path(server_id, rel_path)
    target.mkdir(parents=True, exist_ok=True)


def write_upload(server_id: str | int, rel_path: str, data: bytes) -> None:
    if len(data) > settings.max_upload_size:
        raise ValueError(f"Upload exceeds max size ({settings.max_upload_size} bytes)")
    target = safe_path(server_id, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def ensure_servers_dir() -> None:
    path = settings.servers_path()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create servers_dir: %s", exc)
