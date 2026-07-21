"""Bounded encrypted history for text files edited through the panel.

History lives below the panel configuration directory, never inside a game
server root. Content is compressed with the standard library and then sealed
through the existing DIS facade. There is deliberately no plaintext fallback.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from config import settings
from services.dis_client import DisClient
from services.file_edit_service import content_revision

MAX_HISTORY_EDIT_SIZE = 512 * 1024
MAX_VERSIONS_PER_FILE = 20
_VERSION_ID = re.compile(r"^[0-9a-f]{32}$")
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


class HistoryNotFound(Exception):
    pass


def _root() -> Path:
    return Path(settings.panel_config_dir).resolve(strict=False) / ".msm-file-history"


def _file_key(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def _directory(server_id: int, relative_path: str) -> Path:
    if server_id <= 0:
        raise ValueError("Invalid server id")
    directory = _root() / str(server_id) / _file_key(relative_path)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def _lock(server_id: int, relative_path: str) -> threading.Lock:
    key = f"{server_id}:{_file_key(relative_path)}"
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _index_path(directory: Path) -> Path:
    return directory / "index.json"


def _read_index(directory: Path) -> list[dict[str, Any]]:
    index = _index_path(directory)
    if not index.exists():
        return []
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("File history index is unavailable") from exc
    return data if isinstance(data, list) else []


def _atomic_text(path: Path, value: str) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=".msm-history-",
        ) as temp_file:
            os.fchmod(temp_file.fileno(), 0o600)
            temp_file.write(value)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        os.replace(temp_path, path)
        temp_path = None
        os.chmod(path, 0o600)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _aad(server_id: int, relative_path: str, version_id: str) -> str:
    return f"msm:file-history:v1:{server_id}:{_file_key(relative_path)}:{version_id}"


def snapshot(server_id: int, relative_path: str, content: str, actor_id: int | None) -> bool:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_HISTORY_EDIT_SIZE:
        return False
    revision = content_revision(encoded)
    with _lock(server_id, relative_path):
        directory = _directory(server_id, relative_path)
        versions = _read_index(directory)
        if versions and versions[0].get("revision") == revision:
            return False

        version_id = uuid.uuid4().hex
        compressed = gzip.compress(encoded, compresslevel=6)
        payload = base64.b64encode(compressed).decode("ascii")
        # DIS errors intentionally propagate: eligible history never falls back
        # to plaintext and the calling write fails closed.
        ciphertext = DisClient.encrypt(payload, aad=_aad(server_id, relative_path, version_id))
        ciphertext_path = directory / f"{version_id}.enc"
        _atomic_text(ciphertext_path, ciphertext)

        record = {
            "id": version_id,
            "created_at": int(time.time()),
            "size": len(encoded),
            "revision": revision,
            "actor_id": actor_id,
        }
        next_versions = [record, *versions]
        removed = next_versions[MAX_VERSIONS_PER_FILE:]
        next_versions = next_versions[:MAX_VERSIONS_PER_FILE]
        try:
            _atomic_text(_index_path(directory), json.dumps(next_versions, separators=(",", ":")))
        except Exception:
            ciphertext_path.unlink(missing_ok=True)
            raise
        for old in removed:
            old_id = str(old.get("id") or "")
            if _VERSION_ID.fullmatch(old_id):
                (directory / f"{old_id}.enc").unlink(missing_ok=True)
        return True


def list_versions(server_id: int, relative_path: str) -> list[dict[str, Any]]:
    with _lock(server_id, relative_path):
        return [dict(item) for item in _read_index(_directory(server_id, relative_path))]


def read_version(server_id: int, relative_path: str, version_id: str) -> dict[str, Any]:
    if not _VERSION_ID.fullmatch(version_id):
        raise HistoryNotFound()
    with _lock(server_id, relative_path):
        directory = _directory(server_id, relative_path)
        versions = _read_index(directory)
        record = next((item for item in versions if item.get("id") == version_id), None)
        ciphertext_path = directory / f"{version_id}.enc"
        if record is None or not ciphertext_path.is_file():
            raise HistoryNotFound()
        ciphertext = ciphertext_path.read_text(encoding="utf-8")
        payload = DisClient.decrypt(ciphertext, aad=_aad(server_id, relative_path, version_id))
        try:
            content = gzip.decompress(base64.b64decode(payload, validate=True)).decode("utf-8")
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            raise RuntimeError("File history content is unavailable") from exc
        return {**record, "content": content}
