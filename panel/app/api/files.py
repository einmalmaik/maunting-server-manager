from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, File as FastAPIFile, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from ..models import User
from ..permissions import P_FILES_READ, P_FILES_WRITE, require_perm
from ..server_layout import collect_recent_files, get_servers_root
from .deps import require_server

router = APIRouter()
logger = logging.getLogger(__name__)

_TEXT_EXTENSIONS = {
    ".sh", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".cfg", ".ini",
    ".xml", ".txt", ".log", ".md", ".yaml", ".yml", ".html", ".css",
    ".conf", ".env", ".env.example", ".toml", ".bash", ".zsh",
}
_MAX_EDIT_SIZE = 2 * 1024 * 1024  # 2 MB
_MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
_MAX_EXTRACT_SIZE = 100 * 1024 * 1024  # 100 MB total extracted payload
_MAX_DOWNLOAD_ARCHIVE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB total payload per ZIP
_MAX_DOWNLOAD_FILE_COUNT = 100_000
_PERMISSION_HINT = "Permission denied. Run `./conanserver.sh panel repair` as root if Linux file ownership or write bits are broken."


@dataclass(frozen=True)
class _PlannedUploadNode:
    name: str
    is_dir: bool


@dataclass(frozen=True)
class _DownloadTarget:
    requested_path: Path
    source_path: Path
    relative_path: str


def _get_server_base_dir(server_name: str) -> Path:
    """Resolve and containment-check the base directory for the given server."""
    servers_root = get_servers_root().resolve()
    candidate = (servers_root / server_name).resolve()
    try:
        candidate.relative_to(servers_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid server name.")
    return candidate


def _validate_path(raw_path: str, base_dir: Path, follow_leaf_symlink: bool = True) -> Path:
    """Resolve path and verify it is within base_dir (prevents path traversal).

    When follow_leaf_symlink is False, only the parent directory is fully resolved
    and the leaf name is recomposed without dereferencing it — safe for delete operations
    that target the symlink itself rather than its destination.
    """
    if not raw_path:
        return base_dir
    try:
        p = Path(raw_path)
        if follow_leaf_symlink:
            resolved = p.resolve() if p.is_absolute() else (base_dir / raw_path).resolve()
        else:
            # Resolve the parent, leave the leaf name intact
            full = p if p.is_absolute() else base_dir / raw_path
            if full.name == "..":
                raise HTTPException(status_code=400, detail="Invalid path.")
            resolved = full.parent.resolve() / full.name
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path.")
    base_resolved = base_dir.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path is outside the server directory.")
    return resolved


def _rel(path: Path, base_dir: Path) -> str:
    """Return path relative to base_dir as a string."""
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_log(value: str) -> str:
    """Sanitize a string for use in log messages by stripping ASCII control characters."""
    return "".join(ch for ch in value if ch >= " " or ch == "\t")


def _validate_leaf_name(name: str, *, field: str = "name") -> str:
    cleaned = (name or "").strip()
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"Invalid {field}.")
    if "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail=f"Invalid {field}.")
    return cleaned


def _normalize_relative_upload_path(raw_path: str, *, allow_nested: bool) -> Path:
    normalized = (raw_path or "").replace("\\", "/").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid upload filename.")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        raise HTTPException(status_code=400, detail="Invalid upload filename.")

    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not allow_nested and parts:
        parts = [parts[-1]]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="Invalid upload filename.")
    return Path(*parts)


def _get_case_insensitive_children(parent: Path, cache: dict[Path, dict[str, list[Path]]] | None = None) -> dict[str, list[Path]]:
    if cache is not None and parent in cache:
        return cache[parent]

    entries: dict[str, list[Path]] = {}
    if parent.exists() and parent.is_dir():
        for child in parent.iterdir():
            entries.setdefault(child.name.casefold(), []).append(child)

    if cache is not None:
        cache[parent] = entries
    return entries


def _find_case_insensitive_match(
    parent: Path,
    name: str,
    *,
    cache: dict[Path, dict[str, list[Path]]] | None = None,
    ignore: set[Path] | None = None,
) -> Path | None:
    for candidate in _get_case_insensitive_children(parent, cache).get(name.casefold(), []):
        if ignore and candidate in ignore:
            continue
        return candidate
    return None


def _find_case_insensitive_match_or_403(
    parent: Path,
    name: str,
    *,
    cache: dict[Path, dict[str, list[Path]]] | None = None,
    ignore: set[Path] | None = None,
) -> Path | None:
    try:
        return _find_case_insensitive_match(parent, name, cache=cache, ignore=ignore)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied.") from exc


def _build_existing_item_conflict(target: Path, existing: Path, base_dir: Path) -> HTTPException:
    existing_rel = _rel(existing, base_dir)
    if existing.name != target.name and existing.name.casefold() == target.name.casefold():
        detail = (
            "A file or directory with the same name already exists using different casing: "
            f"{existing_rel}. Linux paths should use one consistent lowercase name."
        )
    else:
        detail = f"A file or directory already exists at {existing_rel}."
    return HTTPException(status_code=409, detail=detail)


def _build_planned_upload_conflict(existing: Path, incoming: Path) -> HTTPException:
    if existing.as_posix() == incoming.as_posix():
        detail = f"Upload contains a duplicate item: {incoming.as_posix()}."
    else:
        detail = (
            "Upload contains multiple items that only differ by letter casing: "
            f"{existing.as_posix()} and {incoming.as_posix()}."
        )
    return HTTPException(status_code=409, detail=detail)


def _prepare_upload_destination(dest_file: Path, base_dir: Path) -> None:
    existing = _find_case_insensitive_match_or_403(dest_file.parent, dest_file.name)
    if existing is not None:
        if existing.name == dest_file.name and existing.is_file() and not existing.is_symlink():
            return
        raise _build_existing_item_conflict(dest_file, existing, base_dir)


def _prepare_batch_uploads(dest_dir: Path, uploads: list[UploadFile], base_dir: Path) -> list[tuple[UploadFile, Path]]:
    cache: dict[Path, dict[str, list[Path]]] = {}
    planned_nodes: dict[tuple[Path, str], _PlannedUploadNode] = {}
    planned_paths: dict[tuple[Path, str], Path] = {}
    prepared: list[tuple[UploadFile, Path]] = []

    for upload in uploads:
        relative_path = _normalize_relative_upload_path(upload.filename or "upload", allow_nested=True)

        current_parent = dest_dir
        relative_parent = Path()
        for part in relative_path.parts[:-1]:
            segment_path = relative_parent / part
            sibling_key = (current_parent, part.casefold())
            planned_node = planned_nodes.get(sibling_key)
            if planned_node is not None:
                if not planned_node.is_dir or planned_node.name != part:
                    raise _build_planned_upload_conflict(planned_paths[sibling_key], segment_path)
            else:
                existing = _find_case_insensitive_match_or_403(current_parent, part, cache=cache)
                if existing is not None:
                    if existing.name != part or not existing.is_dir():
                        raise _build_existing_item_conflict(current_parent / part, existing, base_dir)
                planned_nodes[sibling_key] = _PlannedUploadNode(name=part, is_dir=True)
                planned_paths[sibling_key] = segment_path

            current_parent = _validate_path(str(current_parent / part), base_dir)
            relative_parent = segment_path

        leaf_name = relative_path.name
        leaf_key = (current_parent, leaf_name.casefold())
        existing_leaf = planned_nodes.get(leaf_key)
        if existing_leaf is not None:
            raise _build_planned_upload_conflict(planned_paths[leaf_key], relative_path)

        existing = _find_case_insensitive_match_or_403(current_parent, leaf_name, cache=cache)
        if existing is not None:
            if existing.name != leaf_name or not existing.is_file() or existing.is_symlink():
                raise _build_existing_item_conflict(current_parent / leaf_name, existing, base_dir)

        planned_nodes[leaf_key] = _PlannedUploadNode(name=leaf_name, is_dir=False)
        planned_paths[leaf_key] = relative_path
        prepared.append((upload, _validate_path(str(dest_dir / relative_path), base_dir)))

    return prepared


def _write_upload_stream(upload: UploadFile, dest_file: Path, dest_dir: Path) -> None:
    total_size = 0
    tmp_path: Path | None = None
    try:
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest_dir, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = upload.file.read(65536)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > _MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail=f"Upload too large. Maximum is {_MAX_UPLOAD_SIZE} bytes.")
                tmp.write(chunk)
        os.replace(tmp_path, dest_file)
        tmp_path = None
    except Exception:
        if tmp_path is not None:
            _cleanup_temp_file(tmp_path)
        raise


def _write_existing_file_in_place(target: Path, content_bytes: bytes) -> None:
    with open(target, "r+b") as handle:
        handle.seek(0)
        handle.write(content_bytes)
        handle.truncate()


def _ensure_target_writable(target: Path) -> None:
    try:
        current_mode = stat.S_IMODE(target.stat().st_mode)
        if not (current_mode & stat.S_IWUSR):
            os.chmod(target, current_mode | stat.S_IWUSR)
    except OSError:
        pass


def _ensure_directory_writable(path: Path, base_dir: Path) -> None:
    # Best-effort repair for the runtime user: walk back up the active server tree
    # and add user write/execute bits where missing before mutating files.
    try:
        current = path.resolve(strict=False)
    except OSError:
        current = path
    try:
        stop_at = base_dir.resolve()
    except OSError:
        stop_at = base_dir

    while True:
        try:
            current.relative_to(stop_at)
        except ValueError:
            break

        try:
            if current.exists() and not current.is_symlink():
                mode = stat.S_IMODE(current.stat().st_mode)
                desired = mode | (stat.S_IXUSR if current.is_dir() else 0) | stat.S_IWUSR | stat.S_IRUSR
                if desired != mode:
                    os.chmod(current, desired)
        except OSError:
            pass

        if current == stop_at:
            break
        current = current.parent


def _cleanup_temp_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _validate_text_payload(path: Path, content: str) -> None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).") from exc
    elif suffix == ".xml":
        try:
            ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid XML: {exc}.") from exc


def _safe_extract_zip(archive_path: Path, destination: Path, base_dir: Path) -> int:
    extracted_files = 0
    total_uncompressed_size = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                member_mode = member.external_attr >> 16
                if stat.S_ISLNK(member_mode):
                    raise HTTPException(status_code=400, detail="ZIP archives containing symlinks are not supported.")
                total_uncompressed_size += max(member.file_size, 0)
                if total_uncompressed_size > _MAX_EXTRACT_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Extracted archive too large. Maximum is {_MAX_EXTRACT_SIZE} bytes.",
                    )

                relative_member = _normalize_relative_upload_path(member.filename, allow_nested=True)
                target = _validate_path(str(destination / relative_member), base_dir)

                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(target, "wb") as handle:
                    shutil.copyfileobj(source, handle)
                extracted_files += 1
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP archive.") from exc
    return extracted_files


def _delete_target(target: Path) -> None:
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _coalesce_delete_targets(raw_paths: list[str], base_dir: Path) -> list[Path]:
    targets: list[Path] = []
    seen: set[Path] = set()

    for raw_path in raw_paths:
        target = _validate_path(raw_path, base_dir, follow_leaf_symlink=False)
        if target == base_dir:
            raise HTTPException(status_code=403, detail="Cannot delete the server root directory.")
        if not target.exists() and not target.is_symlink():
            raise HTTPException(status_code=404, detail=f"Path not found: {raw_path}")
        if target in seen:
            continue
        seen.add(target)
        targets.append(target)

    targets.sort(key=lambda item: len(item.parts))

    filtered: list[Path] = []
    for target in targets:
        if any(target == parent or parent in target.parents for parent in filtered):
            continue
        filtered.append(target)

    filtered.sort(key=lambda item: len(item.parts), reverse=True)
    return filtered


def _resolve_download_target(raw_path: str, base_dir: Path) -> _DownloadTarget:
    target = _validate_path(raw_path, base_dir, follow_leaf_symlink=False)

    if not target.exists() and not target.is_symlink():
        raise HTTPException(status_code=404, detail=f"Path not found: {raw_path}")

    source = target
    if target.is_symlink():
        try:
            source = target.resolve(strict=True)
            source.relative_to(base_dir.resolve())
        except (ValueError, OSError):
            raise HTTPException(status_code=403, detail="Path resolves outside the server directory.")

    return _DownloadTarget(
        requested_path=target,
        source_path=source,
        relative_path=_rel(target, base_dir),
    )


def _coalesce_download_targets(raw_paths: list[str], base_dir: Path) -> list[_DownloadTarget]:
    targets: list[_DownloadTarget] = []
    seen: set[Path] = set()

    for raw_path in raw_paths:
        target = _resolve_download_target(raw_path, base_dir)
        if target.requested_path in seen:
            continue
        seen.add(target.requested_path)
        targets.append(target)

    targets.sort(key=lambda item: len(item.requested_path.parts))

    filtered: list[_DownloadTarget] = []
    for target in targets:
        if any(
            target.requested_path == parent.requested_path
            or parent.requested_path in target.requested_path.parents
            for parent in filtered
        ):
            continue
        filtered.append(target)

    return filtered


def _entry_info(path: Path, base_dir: Path) -> dict:
    try:
        st = path.lstat()
    except OSError:
        return {}
    is_link = stat.S_ISLNK(st.st_mode)
    is_dir = stat.S_ISDIR(st.st_mode) if not is_link else path.is_dir()
    ext = path.suffix.lower()
    return {
        "name": path.name,
        "path": _rel(path, base_dir),
        "is_dir": is_dir,
        "is_symlink": is_link,
        "size": st.st_size if not is_dir and not is_link else 0,
        "modified": int(st.st_mtime),
        "is_text": not is_dir and not is_link and any(path.name.lower().endswith(e) for e in _TEXT_EXTENSIONS),
        "extension": ext,
    }


def _write_directory_entry(archive: zipfile.ZipFile, arcname: str) -> None:
    normalized = arcname.strip("/")
    if not normalized:
        return
    archive.writestr(f"{normalized}/", b"")


def _ensure_download_archive_limits(total_size: int, total_files: int) -> None:
    if total_size > _MAX_DOWNLOAD_ARCHIVE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Download archive too large. Maximum is {_MAX_DOWNLOAD_ARCHIVE_SIZE} bytes.",
        )
    if total_files > _MAX_DOWNLOAD_FILE_COUNT:
        raise HTTPException(
            status_code=413,
            detail=f"Download archive contains too many files. Maximum is {_MAX_DOWNLOAD_FILE_COUNT}.",
        )


def _write_download_archive(targets: list[_DownloadTarget], archive_path: Path, base_dir: Path) -> None:
    total_size = 0
    total_files = 0
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for target in targets:
            arc_root = target.relative_path or target.requested_path.name or "server"
            source = target.source_path

            if source.is_file():
                total_size += max(source.stat().st_size, 0)
                total_files += 1
                _ensure_download_archive_limits(total_size, total_files)
                archive.write(source, arcname=arc_root)
                continue

            _write_directory_entry(archive, arc_root)

            for current_root, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
                dirnames.sort()
                filenames.sort()
                current_path = Path(current_root)
                relative_dir = current_path.relative_to(source).as_posix()
                current_arc_root = arc_root if relative_dir == "." else f"{arc_root}/{relative_dir}"

                if not dirnames and not filenames:
                    _write_directory_entry(archive, current_arc_root)

                for filename in filenames:
                    file_path = current_path / filename
                    source_file = file_path
                    if file_path.is_symlink():
                        try:
                            source_file = file_path.resolve(strict=True)
                            source_file.relative_to(base_dir.resolve())
                        except (ValueError, OSError):
                            raise HTTPException(status_code=403, detail="Path resolves outside the server directory.")
                        if not source_file.is_file():
                            continue

                    total_size += max(source_file.stat().st_size, 0)
                    total_files += 1
                    _ensure_download_archive_limits(total_size, total_files)
                    archive.write(source_file, arcname=f"{current_arc_root}/{filename}")


# ── List directory ─────────────────────────────────────────────────────────────

@router.get("/files")
def list_directory(
    path: str = "",
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_READ),
) -> Any:
    base_dir = _get_server_base_dir(server)
    target = _validate_path(path, base_dir) if path else base_dir

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory.")

    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            info = _entry_info(child, base_dir)
            if info:
                entries.append(info)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied.")

    return {
        "path": _rel(target, base_dir),
        "entries": entries,
    }


@router.get("/files/recent")
def list_recent_files(
    path: str = "",
    limit: int = 20,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_READ),
) -> Any:
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100.")

    base_dir = _get_server_base_dir(server)
    try:
        recent_files = collect_recent_files(base_dir, limit=limit, root_path=path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied.")
    return {"path": path, "entries": recent_files}


# ── Read file content ──────────────────────────────────────────────────────────

@router.get("/files/content")
def read_file(
    path: str,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_READ),
) -> Any:
    base_dir = _get_server_base_dir(server)
    target = _validate_path(path, base_dir)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    if target.is_symlink():
        try:
            target.resolve(strict=True).relative_to(base_dir.resolve())
        except (ValueError, OSError):
            raise HTTPException(status_code=403, detail="Path resolves outside the server directory.")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file.")

    st = target.stat()
    size = st.st_size
    if size > _MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large to edit ({size} bytes). Maximum is {_MAX_EDIT_SIZE}.")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied.")

    return {
        "path": _rel(target, base_dir),
        "content": content,
        "size": size,
        "modified": int(st.st_mtime),
    }


# ── Write file content ─────────────────────────────────────────────────────────

class FileWriteBody(BaseModel):
    path: str
    content: str


@router.put("/files/content")
def write_file(
    body: FileWriteBody,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_WRITE),
) -> Any:
    base_dir = _get_server_base_dir(server)
    target = _validate_path(body.path, base_dir)

    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path is a directory.")
    if target.is_symlink():
        try:
            target.resolve(strict=True).relative_to(base_dir.resolve())
        except (ValueError, OSError):
            raise HTTPException(status_code=403, detail="Path resolves outside the server directory.")

    # Normalize line endings to LF for Linux compatibility.
    # This prevents CRLF issues when editing config files via the web panel on Windows.
    normalized_content = body.content.replace('\r\n', '\n').replace('\r', '\n')
    _validate_text_payload(target, normalized_content)
    content_bytes = normalized_content.encode("utf-8")
    if len(content_bytes) > _MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail=f"Content too large ({len(content_bytes)} bytes). Maximum is {_MAX_EDIT_SIZE}.")

    # Capture existing file metadata before overwriting so we can restore it.
    existing_stat: os.stat_result | None = None
    if target.exists() and not target.is_symlink():
        try:
            existing_stat = target.stat()
        except OSError:
            pass

    tmp_path: Path | None = None
    try:
        _ensure_directory_writable(target.parent, base_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp.write(content_bytes)
            tmp_path = Path(tmp.name)
        if existing_stat is not None:
            try:
                os.chmod(tmp_path, stat.S_IMODE(existing_stat.st_mode))
                if hasattr(os, "chown"):
                    os.chown(tmp_path, existing_stat.st_uid, existing_stat.st_gid)
            except OSError:
                pass  # Best-effort — proceed with default ownership
        try:
            os.replace(tmp_path, target)
            tmp_path = None
        except PermissionError:
            if target.exists():
                _ensure_target_writable(target)
                _write_existing_file_in_place(target, content_bytes)
                _cleanup_temp_file(tmp_path)
                tmp_path = None
            else:
                raise
    except PermissionError:
        if tmp_path is not None:
            _cleanup_temp_file(tmp_path)
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except OSError as exc:
        if tmp_path is not None:
            _cleanup_temp_file(tmp_path)
        logger.error("file write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to write file.")

    rel = _rel(target, base_dir)
    logger.info("audit: user=%s server=%s action=write path=%s size=%d", _safe_log(user.username), _safe_log(server), _safe_log(rel), len(content_bytes))
    return {"ok": True, "path": rel}


# ── Upload file ────────────────────────────────────────────────────────────────

@router.post("/files/upload")
async def upload_file(
    path: str,
    file: UploadFile,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_WRITE),
) -> Any:
    base_dir = _get_server_base_dir(server)
    dest_dir = _validate_path(path, base_dir)

    if not dest_dir.is_dir():
        raise HTTPException(status_code=400, detail="Upload destination must be a directory.")

    upload_name = _normalize_relative_upload_path(file.filename or "upload", allow_nested=False)
    dest_file = _validate_path(str(dest_dir / upload_name), base_dir)
    _prepare_upload_destination(dest_file, base_dir)

    try:
        _ensure_directory_writable(dest_dir, base_dir)
        await run_in_threadpool(_write_upload_stream, file, dest_file, dest_dir)
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except HTTPException:
        raise
    except OSError as exc:
        logger.error("file upload failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to upload file.")

    rel = _rel(dest_file, base_dir)
    logger.info("audit: user=%s server=%s action=upload path=%s", _safe_log(user.username), _safe_log(server), _safe_log(rel))
    return {"ok": True, "path": rel}


@router.post("/files/upload/batch")
async def upload_files(
    path: str,
    files: list[UploadFile] = FastAPIFile(...),
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_WRITE),
) -> Any:
    base_dir = _get_server_base_dir(server)
    dest_dir = _validate_path(path, base_dir)

    if not dest_dir.is_dir():
        raise HTTPException(status_code=400, detail="Upload destination must be a directory.")
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    prepared_uploads = _prepare_batch_uploads(dest_dir, files, base_dir)

    written_paths: list[str] = []
    for upload, dest_file in prepared_uploads:
        try:
            _ensure_directory_writable(dest_file.parent, base_dir)
            await run_in_threadpool(_write_upload_stream, upload, dest_file, dest_dir)
        except PermissionError:
            raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
        except HTTPException:
            raise
        except OSError as exc:
            logger.error("batch upload failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to upload files.") from exc
        written_paths.append(_rel(dest_file, base_dir))

    logger.info(
        "audit: user=%s server=%s action=upload-batch count=%d",
        _safe_log(user.username),
        _safe_log(server),
        len(written_paths),
    )
    return {"ok": True, "paths": written_paths}


# ── Delete file or directory ──────────────────────────────────────────────────

@router.delete("/files")
def delete_path(
    path: str,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_WRITE),
) -> Any:
    base_dir = _get_server_base_dir(server)
    target = _validate_path(path, base_dir, follow_leaf_symlink=False)

    if not target.exists() and not target.is_symlink():
        raise HTTPException(status_code=404, detail="Path not found.")
    if target == base_dir:
        raise HTTPException(status_code=403, detail="Cannot delete the server root directory.")

    rel = _rel(target, base_dir)
    try:
        _ensure_directory_writable(target.parent, base_dir)
        _delete_target(target)
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except OSError as exc:
        logger.error("delete failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete.")

    logger.info("audit: user=%s server=%s action=delete path=%s", _safe_log(user.username), _safe_log(server), _safe_log(rel))
    return {"ok": True}


class DeleteManyBody(BaseModel):
    paths: list[str]


@router.post("/files/delete-batch")
def delete_paths(
    body: DeleteManyBody,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_WRITE),
) -> Any:
    if not body.paths:
        raise HTTPException(status_code=400, detail="No paths provided.")

    base_dir = _get_server_base_dir(server)
    targets = _coalesce_delete_targets(body.paths, base_dir)

    deleted: list[str] = []
    try:
        for target in targets:
            rel = _rel(target, base_dir)
            _ensure_directory_writable(target.parent, base_dir)
            _delete_target(target)
            deleted.append(rel)
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except OSError as exc:
        logger.error("batch delete failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete selected paths.")

    logger.info(
        "audit: user=%s server=%s action=delete-batch count=%d",
        _safe_log(user.username),
        _safe_log(server),
        len(deleted),
    )
    return {"ok": True, "paths": deleted}


class RenameBody(BaseModel):
    path: str
    new_name: str


@router.patch("/files/rename")
def rename_path(
    body: RenameBody,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_WRITE),
) -> Any:
    base_dir = _get_server_base_dir(server)
    source = _validate_path(body.path, base_dir, follow_leaf_symlink=False)

    if not source.exists() and not source.is_symlink():
        raise HTTPException(status_code=404, detail="Path not found.")
    if source == base_dir:
        raise HTTPException(status_code=403, detail="Cannot rename the server root directory.")

    new_name = _validate_leaf_name(body.new_name, field="target name")
    target = _validate_path(str(source.parent / new_name), base_dir, follow_leaf_symlink=False)
    if target == source and target.name == source.name:
        return {"ok": True, "path": _rel(source, base_dir)}
    existing = _find_case_insensitive_match_or_403(source.parent, new_name, ignore={source})
    if existing is not None:
        raise _build_existing_item_conflict(target, existing, base_dir)

    try:
        _ensure_directory_writable(source.parent, base_dir)
        source.rename(target)
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except OSError as exc:
        logger.error("rename failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to rename path.")

    rel = _rel(target, base_dir)
    logger.info(
        "audit: user=%s server=%s action=rename path=%s new_name=%s",
        _safe_log(user.username),
        _safe_log(server),
        _safe_log(_rel(source, base_dir)),
        _safe_log(new_name),
    )
    return {"ok": True, "path": rel, "name": new_name}


# ── Create directory ───────────────────────────────────────────────────────────

class MkdirBody(BaseModel):
    path: str


@router.post("/files/mkdir")
def make_directory(
    body: MkdirBody,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_WRITE),
) -> Any:
    base_dir = _get_server_base_dir(server)
    target = _validate_path(body.path, base_dir)

    existing = _find_case_insensitive_match_or_403(target.parent, target.name)
    if existing is not None:
        if existing == target and existing.is_dir() and existing.name == target.name:
            rel = _rel(target, base_dir)
            logger.info("audit: user=%s server=%s action=mkdir path=%s", _safe_log(user.username), _safe_log(server), _safe_log(rel))
            return {"ok": True, "path": rel}
        raise _build_existing_item_conflict(target, existing, base_dir)
    try:
        _ensure_directory_writable(target.parent, base_dir)
        target.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except OSError as exc:
        logger.error("mkdir failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create directory.")

    rel = _rel(target, base_dir)
    logger.info("audit: user=%s server=%s action=mkdir path=%s", _safe_log(user.username), _safe_log(server), _safe_log(rel))
    return {"ok": True, "path": rel}


class ExtractArchiveBody(BaseModel):
    path: str


@router.post("/files/extract")
async def extract_archive(
    body: ExtractArchiveBody,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_WRITE),
) -> Any:
    base_dir = _get_server_base_dir(server)
    archive_path = _validate_path(body.path, base_dir)

    if not archive_path.exists():
        raise HTTPException(status_code=404, detail="Archive not found.")
    if not archive_path.is_file():
        raise HTTPException(status_code=400, detail="Archive path is not a file.")
    if archive_path.suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="Only ZIP archives can be extracted.")

    destination = _validate_path(str(archive_path.parent / archive_path.stem), base_dir)
    if destination.exists():
        raise HTTPException(status_code=409, detail="Extraction target already exists.")

    try:
        _ensure_directory_writable(destination.parent, base_dir)
        destination.mkdir(parents=True, exist_ok=False)
        extracted_files = await run_in_threadpool(_safe_extract_zip, archive_path, destination, base_dir)
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except HTTPException:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except OSError as exc:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        logger.error("archive extract failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to extract archive.") from exc

    rel = _rel(destination, base_dir)
    logger.info("audit: user=%s server=%s action=extract path=%s", _safe_log(user.username), _safe_log(server), _safe_log(rel))
    return {"ok": True, "path": rel, "files": extracted_files}


# ── Download file ──────────────────────────────────────────────────────────────

class DownloadManyBody(BaseModel):
    paths: list[str]


@router.post("/files/download-batch")
def download_batch(
    body: DownloadManyBody,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_READ),
) -> Any:
    if not body.paths:
        raise HTTPException(status_code=400, detail="No paths provided.")

    base_dir = _get_server_base_dir(server)
    targets = _coalesce_download_targets(body.paths, base_dir)

    archive_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="conan-panel-download-", suffix=".zip", delete=False) as tmp:
            archive_path = Path(tmp.name)
        _write_download_archive(targets, archive_path, base_dir)
    except HTTPException:
        _cleanup_temp_file(archive_path)
        raise
    except OSError as exc:
        _cleanup_temp_file(archive_path)
        logger.error("batch download failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to prepare download archive.") from exc

    filename = f"{server}-files-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    logger.info(
        "audit: user=%s server=%s action=download-batch count=%d",
        _safe_log(user.username),
        _safe_log(server),
        len(targets),
    )
    return FileResponse(
        path=str(archive_path),
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(_cleanup_temp_file, archive_path),
    )


@router.get("/files/download")
def download_file(
    path: str,
    server: str = Depends(require_server),
    user: User = require_perm(P_FILES_READ),
) -> Any:
    base_dir = _get_server_base_dir(server)
    target = _validate_path(path, base_dir)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    if target.is_symlink():
        try:
            target.resolve(strict=True).relative_to(base_dir.resolve())
        except (ValueError, OSError):
            raise HTTPException(status_code=403, detail="Path resolves outside the server directory.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(path=str(target), filename=target.name, media_type=media_type)
