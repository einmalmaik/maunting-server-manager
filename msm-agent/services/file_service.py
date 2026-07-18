"""Filesystem operations with strict path-traversal protection.

All paths are resolved with realpath/resolve and must stay inside
``MSM_SERVERS_DIR / server_id``. Symlink escapes and ``..`` segments are rejected.
"""

from __future__ import annotations

import logging
import json
import glob
import os
import re
import shutil
import tarfile
import zipfile
import uuid
from pathlib import Path
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

MAX_CHUNK_SIZE = 64 * 1024 * 1024
MAX_CHUNKED_UPLOAD_SIZE = 10 * 1024 * 1024 * 1024


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


def disk_info(server_id: str | int) -> dict[str, int]:
    """Return used bytes for this server and free bytes on its node filesystem."""
    root = server_root(server_id)
    usage_target = root if root.exists() else root.parent
    free_bytes = shutil.disk_usage(usage_target).free
    used_bytes = 0
    if root.exists():
        for current_root, dir_names, file_names in os.walk(root, followlinks=False):
            current = Path(current_root)
            dir_names[:] = [name for name in dir_names if not (current / name).is_symlink()]
            for name in file_names:
                path = current / name
                try:
                    if not path.is_symlink():
                        used_bytes += path.stat().st_size
                except OSError:
                    continue
    return {"used_bytes": used_bytes, "free_bytes": free_bytes}


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


def _safe_workshop_pattern(value: str, *, allow_glob: bool) -> str:
    rel = str(value or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or "\x00" in rel:
        raise PathValidationError("Unsafe workshop path")
    if any(part == ".." for part in Path(rel).parts):
        raise PathValidationError("Workshop path traversal is not allowed")
    if not allow_glob and any(char in rel for char in ("*", "?", "[")):
        raise PathValidationError("Workshop target glob is not allowed")
    return rel


def _workshop_sources(root: Path, source_pattern: str) -> list[Path]:
    pattern = _safe_workshop_pattern(source_pattern, allow_glob=True)
    matches = glob.glob(str(root / pattern), recursive=True)
    sources: list[Path] = []
    for raw in matches:
        source = Path(raw).resolve(strict=False)
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise PathEscapeError("Workshop source escapes server directory") from exc
        if source.exists():
            sources.append(source)
    return sources


def _workshop_target(root: Path, target_pattern: str, basename: str) -> Path:
    rendered = _safe_workshop_pattern(
        target_pattern.replace("{BASENAME}", basename),
        allow_glob=False,
    )
    target = root / rendered
    try:
        target.parent.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise PathEscapeError("Workshop target escapes server directory") from exc
    return target


def _workshop_target_ready(root: Path, target: Path) -> bool:
    if not target.exists():
        return False
    if not target.is_symlink():
        return True
    try:
        target.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, ValueError):
        return False
    return True


def workshop_files(
    server_id: str | int,
    *,
    workshop_app_id: str,
    workshop_id: str,
    actions: list[dict[str, Any]],
    mode: str,
) -> dict[str, Any]:
    """Apply, inspect, or remove blueprint Workshop runtime artifacts on-node."""
    if mode not in {"apply", "inspect", "cleanup"}:
        raise PathValidationError("Invalid workshop operation")
    if not str(workshop_app_id).isdigit() or not str(workshop_id).isdigit():
        raise PathValidationError("Invalid workshop identifier")

    root = server_root(server_id)
    root.mkdir(parents=True, exist_ok=True)
    targets: list[Path] = []

    for action in actions:
        operation = str(action.get("operation") or "")
        if operation not in {"copy", "symlink"}:
            raise PathValidationError("Invalid workshop file operation")
        source_pattern = str(action.get("source") or "")
        target_pattern = str(action.get("target") or "")
        _safe_workshop_pattern(source_pattern, allow_glob=True)
        _safe_workshop_pattern(
            target_pattern.replace("{BASENAME}", "synthetic-target"),
            allow_glob=False,
        )
        sources = _workshop_sources(root, source_pattern)
        if bool(action.get("required")) and not sources and mode != "cleanup":
            raise FileNotFoundError("Required workshop source not found")

        if mode == "cleanup" and "{BASENAME}" in target_pattern and not sources:
            cleanup_pattern = _safe_workshop_pattern(
                target_pattern.replace("{BASENAME}", "*"), allow_glob=True
            )
            for raw in glob.glob(str(root / cleanup_pattern)):
                candidate = Path(raw)
                try:
                    candidate.parent.resolve(strict=False).relative_to(root)
                except ValueError as exc:
                    raise PathEscapeError("Workshop cleanup target escapes server directory") from exc
                targets.append(candidate)
        else:
            basenames = [source.name for source in sources] if "{BASENAME}" in target_pattern else [""]
            targets.extend(_workshop_target(root, target_pattern, name) for name in basenames)

        if mode == "apply":
            for source in sources:
                target = _workshop_target(root, target_pattern, source.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                if operation == "copy":
                    if not source.is_file():
                        raise PathValidationError("Workshop copy source is not a file")
                    if target.is_symlink() or target.is_file():
                        target.unlink()
                    elif target.exists():
                        raise PathValidationError("Workshop copy target is not a file")
                    shutil.copy2(source, target)
                else:
                    if target.is_symlink():
                        target.unlink()
                    elif target.exists():
                        raise FileExistsError("Workshop target already exists")
                    target.symlink_to(os.path.relpath(source, target.parent), target_is_directory=source.is_dir())

    unique_targets = list(dict.fromkeys(targets))
    if mode == "cleanup":
        for target in unique_targets:
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                shutil.rmtree(target)
        for rel in (
            f"steamapps/workshop/content/{workshop_app_id}/{workshop_id}",
            f"steamapps/workshop/downloads/{workshop_app_id}/{workshop_id}",
        ):
            cache = safe_path(server_id, rel)
            if cache.is_symlink() or cache.is_file():
                cache.unlink(missing_ok=True)
            elif cache.is_dir():
                shutil.rmtree(cache)

    ready = all(_workshop_target_ready(root, target) for target in unique_targets)
    return {
        "ok": True,
        "ready": ready,
        "targets": [target.relative_to(root).as_posix() for target in unique_targets],
        "target_basenames": [target.name for target in unique_targets],
    }


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


def ensure_server_root(server_id: str | int) -> None:
    """Create the server root itself; only the authenticated panel calls this."""
    server_root(server_id).mkdir(parents=True, exist_ok=False)


def delete_server_root(server_id: str | int) -> None:
    """Remove one complete server root after the panel authorized server deletion."""
    root = server_root(server_id)
    if not root.exists():
        return
    if not root.is_dir():
        raise PathValidationError("Server root is not a directory")
    shutil.rmtree(root)


def _set_ini_value(target: Path, section: str, key: str, value: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if target.exists() else []
    section_header = f"[{section}]"
    in_section = found_section = wrote_key = False
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not wrote_key:
                output.append(f"{key}={value}\n")
                wrote_key = True
            in_section = stripped == section_header
            found_section = found_section or in_section
            output.append(line)
        elif in_section and not wrote_key and stripped.startswith(f"{key}="):
            output.append(f"{key}={value}\n")
            wrote_key = True
        else:
            output.append(line)
    if in_section and not wrote_key:
        output.append(f"{key}={value}\n")
    if not found_section:
        if output and output[-1].strip():
            output.append("\n")
        output.extend([f"{section_header}\n", f"{key}={value}\n"])
    target.write_text("".join(output), encoding="utf-8")


def prepare_runtime(
    server_id: str | int,
    *,
    ensure_dirs: list[str],
    required_files: list[str],
    patches: list[dict[str, str | None]],
) -> None:
    """Apply declarative, blueprint-validated runtime file preparation on-node."""
    for rel_path in ensure_dirs:
        safe_path(server_id, rel_path).mkdir(parents=True, exist_ok=True)
    for patch in patches:
        target = safe_path(server_id, str(patch.get("file") or ""))
        patch_type = patch.get("type")
        if patch_type == "ini":
            section = str(patch.get("section") or "")
            key = str(patch.get("key") or "")
            if not section or not key:
                raise PathValidationError("Invalid INI patch")
            _set_ini_value(target, section, key, str(patch.get("value") or ""))
        elif patch_type == "regex":
            pattern = str(patch.get("regex") or "")
            if pattern and target.exists():
                content = target.read_text(encoding="utf-8", errors="replace")
                target.write_text(
                    re.sub(pattern, str(patch.get("value") or ""), content),
                    encoding="utf-8",
                )
        else:
            raise PathValidationError("Unknown runtime patch type")
    missing = [rel_path for rel_path in required_files if not safe_path(server_id, rel_path).is_file()]
    if missing:
        raise FileNotFoundError("Required runtime files are missing: " + ", ".join(missing))


def search_paths(server_id: str | int, query: str, *, limit: int = 200) -> dict[str, Any]:
    root = server_root(server_id)
    if not root.exists():
        return {"q": query, "results": [], "truncated": False}
    needle = query.lower()
    results: list[dict[str, Any]] = []
    for current_root, dirs, files in os.walk(root):
        for name in dirs + files:
            if needle not in name.lower():
                continue
            full = Path(current_root) / name
            try:
                resolved = full.resolve(strict=False)
                resolved.relative_to(root)
            except (ValueError, OSError):
                continue
            results.append({
                "name": name,
                "path": full.relative_to(root).as_posix(),
                "is_dir": full.is_dir(),
            })
            if len(results) >= limit:
                return {"q": query, "results": results, "truncated": True}
    return {"q": query, "results": results, "truncated": False}


def move_path(server_id: str | int, source_path: str, target_path: str) -> None:
    source = safe_path(server_id, source_path)
    target = safe_path(server_id, target_path)
    root = server_root(server_id)
    if source == root:
        raise PathValidationError("Cannot move server root")
    if not source.exists():
        raise FileNotFoundError("Source not found")
    if target.exists():
        raise FileExistsError("Destination already exists")
    try:
        target.relative_to(source)
        raise PathValidationError("Destination is inside source")
    except ValueError:
        pass
    if not target.parent.is_dir():
        raise FileNotFoundError("Destination directory not found")
    shutil.move(str(source), str(target))


def _validated_archive_destination(root: Path, raw_name: str) -> Path:
    if not raw_name or "\x00" in raw_name or Path(raw_name).is_absolute():
        raise PathValidationError("Unsafe archive member")
    target = (root / raw_name).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError("Archive member escapes server directory") from exc
    return target


def extract_archive(server_id: str | int, rel_path: str) -> None:
    archive = safe_path(server_id, rel_path)
    if not archive.is_file():
        raise FileNotFoundError("Archive not found")
    destination = archive.parent.resolve()
    lower = archive.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            for member in handle.infolist():
                _validated_archive_destination(destination, member.filename)
                if member.is_dir():
                    continue
                mode = (member.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise PathValidationError("Archive symlinks are not allowed")
            handle.extractall(destination)
        return
    if any(lower.endswith(ext) for ext in (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")):
        with tarfile.open(archive, mode="r:*") as handle:
            members = handle.getmembers()
            for member in members:
                _validated_archive_destination(destination, member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise PathValidationError("Archive links and devices are not allowed")
            handle.extractall(destination, members=members)
        return
    raise PathValidationError("Unsupported archive type")


def restore_backup_archive(server_id: str | int, source) -> None:
    """Atomically replace one server root from a validated tar.gz stream."""
    root = server_root(server_id)
    parent = root.parent
    staging = parent / f".{root.name}-restore-{uuid.uuid4().hex}"
    previous = parent / f".{root.name}-pre-restore"
    archive_path = parent / f".{root.name}-restore-{uuid.uuid4().hex}.tar.gz"
    staging.mkdir(mode=0o700)
    try:
        written = 0
        with archive_path.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_CHUNKED_UPLOAD_SIZE:
                    raise ValueError("Restore archive exceeds maximum size")
                output.write(chunk)
        with tarfile.open(archive_path, "r:gz") as handle:
            members = handle.getmembers()
            for member in members:
                _validated_archive_destination(staging, member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise PathValidationError("Archive links and devices are not allowed")
            handle.extractall(
                staging,
                members=members,
                filter=tarfile.data_filter if hasattr(tarfile, "data_filter") else None,
            )
        if previous.exists():
            shutil.rmtree(previous)
        if root.exists():
            root.rename(previous)
        staging.rename(root)
    except Exception:
        if previous.exists() and not root.exists():
            previous.rename(root)
        raise
    finally:
        archive_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def finalize_backup_restore(server_id: str | int) -> None:
    root = server_root(server_id)
    previous = root.parent / f".{root.name}-pre-restore"
    shutil.rmtree(previous, ignore_errors=True)


def rollback_backup_restore(server_id: str | int) -> None:
    root = server_root(server_id)
    previous = root.parent / f".{root.name}-pre-restore"
    if root.exists():
        shutil.rmtree(root)
    if previous.exists():
        previous.rename(root)


def _upload_paths(server_id: str | int, upload_id: str) -> tuple[Path, Path]:
    if len(upload_id) != 32 or not upload_id.isalnum():
        raise PathValidationError("Invalid upload id")
    directory = safe_path(server_id, ".msm-uploads")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{upload_id}.part", directory / f"{upload_id}.json"


def init_chunked_upload(
    server_id: str | int, upload_id: str, *, path: str, filename: str, total_size: int
) -> None:
    if total_size < 0 or total_size > MAX_CHUNKED_UPLOAD_SIZE:
        raise ValueError("Upload exceeds maximum size")
    if not filename or "/" in filename or "\\" in filename:
        raise PathValidationError("Invalid filename")
    safe_path(server_id, path)
    safe_path(server_id, f"{path}/{filename}" if path else filename)
    part, meta = _upload_paths(server_id, upload_id)
    if part.exists() or meta.exists():
        raise FileExistsError("Upload already exists")
    part.touch()
    meta.write_text(
        json.dumps({"path": path, "filename": filename, "total_size": total_size}),
        encoding="utf-8",
    )


def append_upload_chunk(server_id: str | int, upload_id: str, data_stream: Any) -> int:
    part, meta = _upload_paths(server_id, upload_id)
    if not part.exists() or not meta.exists():
        raise FileNotFoundError("Upload not found")
    current = part.stat().st_size
    expected = int(json.loads(meta.read_text(encoding="utf-8"))["total_size"])
    
    total_written = 0
    with part.open("ab") as handle:
        while block := data_stream.read(1024 * 1024):  # 1MB buffer
            total_written += len(block)
            if current + total_written > MAX_CHUNKED_UPLOAD_SIZE or (expected and current + total_written > expected):
                raise ValueError("Upload exceeds declared size")
            if total_written > MAX_CHUNK_SIZE:
                raise ValueError("Chunk exceeds maximum size")
            handle.write(block)
            
    return current + total_written


def upload_status(server_id: str | int, upload_id: str) -> int:
    part, meta = _upload_paths(server_id, upload_id)
    if not part.exists() or not meta.exists():
        raise FileNotFoundError("Upload not found")
    return part.stat().st_size


def finalize_chunked_upload(server_id: str | int, upload_id: str) -> dict[str, Any]:
    part, meta = _upload_paths(server_id, upload_id)
    if not part.exists() or not meta.exists():
        raise FileNotFoundError("Upload not found")
    values = json.loads(meta.read_text(encoding="utf-8"))
    actual_size = part.stat().st_size
    expected = int(values["total_size"])
    if expected and actual_size != expected:
        raise ValueError(f"Upload incomplete ({actual_size}/{expected} bytes)")
    rel_path = f"{values['path']}/{values['filename']}" if values["path"] else values["filename"]
    target = safe_path(server_id, rel_path)
    if target.exists():
        raise FileExistsError("Destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part, target)
    meta.unlink(missing_ok=True)
    return {"name": values["filename"], "size": actual_size, "path": rel_path}


def abort_chunked_upload(server_id: str | int, upload_id: str) -> None:
    part, meta = _upload_paths(server_id, upload_id)
    part.unlink(missing_ok=True)
    meta.unlink(missing_ok=True)


def cache_config_files(server_id: str | int, patterns: list[str]) -> int:
    root = server_root(server_id)
    cache_dir = safe_path(server_id, ".msm-config-cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / "manual-configs.tar"
    selected: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.rglob(pattern):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] in {"steamapps", ".msm-config-cache", ".msm-uploads"}:
                continue
            if "workshop" in relative.parts:
                continue
            selected[relative.as_posix()] = path
    if not selected:
        archive.unlink(missing_ok=True)
        return 0
    with tarfile.open(archive, "w") as handle:
        for relative, path in sorted(selected.items()):
            handle.add(path, arcname=relative, recursive=False)
    return len(selected)


def restore_config_files(server_id: str | int) -> int:
    root = server_root(server_id)
    archive = safe_path(server_id, ".msm-config-cache/manual-configs.tar")
    if not archive.is_file():
        return 0
    with tarfile.open(archive, "r") as handle:
        members = handle.getmembers()
        for member in members:
            _validated_archive_destination(root, member.name)
            if not member.isfile():
                raise PathValidationError("Config cache contains invalid entry")
        handle.extractall(root, members=members)
    return len(members)


def clear_config_cache(server_id: str | int) -> None:
    cache_dir = safe_path(server_id, ".msm-config-cache")
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


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


def iter_archive_tar_gz(
    server_id: str | int,
    postgres: dict[str, Any] | None = None,
):
    """Yield tar.gz chunks of the server root (for panel backup streaming).

    Uses tarfile in streaming mode; path escape is prevented by walking only
    under server_root after realpath checks.
    """
    import io
    import tempfile

    root = server_root(server_id)
    if not root.is_dir():
        raise FileNotFoundError("Server directory not found")

    postgres_dumps: dict[str, str] = {}
    if postgres:
        from services.postgres_service import dump_databases

        postgres_dumps = dump_databases(
            admin_password=str(postgres.get("admin_password") or ""),
            database_names=list(postgres.get("database_names") or []),
        )

    with tempfile.TemporaryFile() as buf:
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                current = Path(dirpath)
                dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
                for name in filenames:
                    full = current / name
                    try:
                        resolved = full.resolve(strict=False)
                        resolved.relative_to(root)
                    except (ValueError, OSError):
                        continue
                    if full.is_symlink():
                        continue
                    arcname = resolved.relative_to(root).as_posix()
                    try:
                        tar.add(str(full), arcname=arcname, recursive=False)
                    except OSError:
                        continue
            for database_name, sql_text in postgres_dumps.items():
                payload = sql_text.encode("utf-8")
                info = tarfile.TarInfo(f".msm/postgres/{database_name}.sql")
                info.size = len(payload)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(payload))
        buf.seek(0)
        while chunk := buf.read(64 * 1024):
            yield chunk
