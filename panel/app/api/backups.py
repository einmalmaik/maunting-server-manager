from __future__ import annotations

import logging
import os
import re
import stat
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..models import AuditLog, User
from ..permissions import P_BACKUPS_CREATE, P_BACKUPS_RESTORE, P_BACKUPS_VIEW, require_perm
from ..server_layout import get_server_base_dir
from ..shell import PanelCommandError, fetch_backup_runs, invoke_core_action
from .deps import get_db, require_server, require_server_with_info

router = APIRouter()
logger = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(?:-\d{2})?$")
_TIMESTAMP_FMT = "%Y-%m-%d_%H-%M"
_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_MAX_EDIT_SIZE = 2 * 1024 * 1024
_PERMISSION_HINT = "Permission denied. Run `./conanserver.sh panel repair` as root if Linux file ownership or write bits are broken."


def _record_audit(
    db: Session,
    user: User,
    action: str,
    target: str | None,
    status_value: str,
    detail: str | None,
) -> None:
    entry = AuditLog(
        user_id=user.id,
        actor_username=user.username,
        action=action,
        target=target,
        status=status_value,
        detail=detail,
    )
    db.add(entry)
    try:
        db.commit()
    except Exception as exc:
        logger.exception("Failed to record audit log action=%s user=%s: %s", action, user.username, exc)
        db.rollback()


def _clean_cli_detail(detail: str | None, fallback: str) -> str:
    if not detail:
        return fallback
    cleaned = _ANSI_ESCAPE_RE.sub("", detail).replace("\r", "")
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return fallback
    preferred_matches: list[str] = []
    for line in lines:
        lowered = line.lower()
        if (
            "error" in lowered
            or "failed" in lowered
            or "cannot" in lowered
            or "could not" in lowered
            or "fehler" in lowered
            or "fehl" in lowered
            or "konnte nicht" in lowered
        ):
            preferred_matches.append(line)
    if preferred_matches:
        return preferred_matches[-1]
    return lines[-1]


def _get_backup_run_dir(server: str, timestamp: str) -> Path:
    try:
        run_dir = get_server_base_dir(server) / "backup" / timestamp
        if not run_dir.is_dir():
            raise HTTPException(status_code=404, detail="Backup run not found.")
        return run_dir
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT) from exc


def _resolve_live_path(base_dir: Path, raw_path: str) -> Path:
    if not raw_path:
        raise HTTPException(status_code=400, detail="Path is required.")
    try:
        candidate = (base_dir / raw_path).resolve()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT) from exc
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Path is outside the server directory.") from exc
    return candidate


def _backup_member_for_path(base_dir: Path, run_dir: Path, live_path: Path) -> tuple[Path, str]:
    rel = live_path.relative_to(base_dir).as_posix()
    parts = Path(rel).parts
    if len(parts) >= 3 and parts[0] == "serverfiles" and parts[1] == "ConanSandbox" and parts[2] == "Saved":
        return run_dir / "conan-saved.tar", "/".join(parts[1:])
    if rel in {"config.ini", "workshop.cfg", "mod_timestamps.json"}:
        return run_dir / "conan-panel.tar", rel
    raise HTTPException(
        status_code=400,
        detail="Only Conan save files and panel config files can be restored individually in this version.",
    )


def _read_member_bytes(archive_path: Path, member_name: str) -> bytes:
    if not archive_path.is_file():
        raise HTTPException(status_code=404, detail="Requested archive is not present in this backup.")
    try:
        with tarfile.open(archive_path, "r") as archive:
            try:
                member = archive.getmember(member_name)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="File not found in the selected backup.") from exc
            if not member.isfile():
                raise HTTPException(status_code=400, detail="Selected backup entry is not a file.")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise HTTPException(status_code=404, detail="File not found in the selected backup.")
            return extracted.read()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT) from exc
    except tarfile.TarError as exc:
        raise HTTPException(status_code=500, detail="Failed to read the selected backup archive.") from exc


def _ensure_parent_writable(path: Path, base_dir: Path) -> None:
    current = path
    base_resolved = base_dir.resolve()
    while True:
        try:
            current.resolve().relative_to(base_resolved)
        except ValueError:
            break
        try:
            mode = stat.S_IMODE(current.stat().st_mode)
            desired = mode | stat.S_IWUSR | stat.S_IRUSR | (stat.S_IXUSR if current.is_dir() else 0)
            if desired != mode:
                os.chmod(current, desired)
        except OSError:
            pass
        if current == base_resolved or current.parent == current:
            break
        current = current.parent


def _write_bytes_atomic(target: Path, data: bytes, base_dir: Path) -> None:
    existing_stat = None
    if target.exists():
        existing_stat = target.stat()
    _ensure_parent_writable(target.parent, base_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            handle.write(data)
            tmp_path = Path(handle.name)
        if existing_stat is not None:
            try:
                os.chmod(tmp_path, stat.S_IMODE(existing_stat.st_mode))
                if hasattr(os, "chown"):
                    os.chown(tmp_path, existing_stat.st_uid, existing_stat.st_gid)
            except OSError:
                pass
        tmp_path.replace(target)
        tmp_path = None
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT) from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _iter_backup_entries(run_dir: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    archives = []
    saved_archive = run_dir / "conan-saved.tar"
    profile_archive = run_dir / "conan-panel.tar"
    if saved_archive.is_file():
        archives.append(saved_archive)
    if profile_archive.is_file():
        archives.append(profile_archive)

    for archive_path in archives:
        try:
            with tarfile.open(archive_path, "r") as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    rel_path = f"serverfiles/{member.name}" if archive_path.name == "conan-saved.tar" else member.name
                    entries.append(
                        {
                            "path": rel_path,
                            "size": member.size,
                            "archive": archive_path.name,
                        }
                    )
        except PermissionError:
            continue
        except tarfile.TarError:
            continue
    entries.sort(key=lambda item: str(item["path"]))
    return entries


class RestoreBody(BaseModel):
    timestamp: str

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        if not _TIMESTAMP_RE.match(v):
            raise ValueError("Invalid timestamp format. Expected YYYY-MM-DD_HH-MM or YYYY-MM-DD_HH-MM-SS.")
        try:
            datetime.strptime(v[:16], _TIMESTAMP_FMT)
        except ValueError as exc:
            raise ValueError("Invalid timestamp value.") from exc
        return v


class BackupFileBody(RestoreBody):
    path: str


@router.get("/backups")
def list_backups(
    user: User = require_perm(P_BACKUPS_VIEW),
    server_info: dict[str, str] = Depends(require_server_with_info),
):
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    try:
        return fetch_backup_runs(server_name=server, manager_path=manager_path)
    except PanelCommandError as exc:
        detail = exc.result.stderr or str(exc)
        logger.error("backup list failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to list backups.")


@router.get("/backups/files")
def list_backup_files(
    timestamp: str,
    user: User = require_perm(P_BACKUPS_VIEW),
    server: str = Depends(require_server),
):
    body = RestoreBody(timestamp=timestamp)
    run_dir = _get_backup_run_dir(server, body.timestamp)
    return {"timestamp": body.timestamp, "entries": _iter_backup_entries(run_dir)}


@router.get("/backups/file-content")
def get_backup_file_content(
    timestamp: str,
    path: str,
    user: User = require_perm(P_BACKUPS_VIEW),
    server: str = Depends(require_server),
):
    body = BackupFileBody(timestamp=timestamp, path=path)
    base_dir = get_server_base_dir(server)
    live_path = _resolve_live_path(base_dir, body.path)
    run_dir = _get_backup_run_dir(server, body.timestamp)
    archive_path, member_name = _backup_member_for_path(base_dir, run_dir, live_path)
    data = _read_member_bytes(archive_path, member_name)
    if len(data) > _MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="Backup file is too large to compare in the panel.")
    return {
        "timestamp": body.timestamp,
        "path": body.path,
        "archive": archive_path.name,
        "content": data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n"),
    }


@router.post("/backups/create")
def create_backup(
    db: Session = Depends(get_db),
    user: User = require_perm(P_BACKUPS_CREATE),
    server_info: dict[str, str] = Depends(require_server_with_info),
):
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    try:
        result = invoke_core_action("backup", server_name=server, manager_path=manager_path)
        _record_audit(db, user, "backup.create", None, "success", result.stdout or "OK")
        return {"ok": True}
    except PanelCommandError as exc:
        detail = exc.result.stderr or exc.result.stdout or str(exc)
        _record_audit(db, user, "backup.create", None, "failed", detail)
        logger.error("backup.create failed: %s", detail)
        raise HTTPException(
            status_code=500,
            detail=_clean_cli_detail(detail, "Backup creation failed."),
        )


@router.post("/backups/restore")
def restore_backup(
    body: RestoreBody,
    db: Session = Depends(get_db),
    user: User = require_perm(P_BACKUPS_RESTORE),
    server_info: dict[str, str] = Depends(require_server_with_info),
):
    server = server_info["name"]
    manager_path = server_info["manager_path"]
    try:
        result = invoke_core_action("backup", "restore", body.timestamp, server_name=server, manager_path=manager_path)
        _record_audit(db, user, "backup.restore", body.timestamp, "success", result.stdout or "OK")
        return {"ok": True}
    except PanelCommandError as exc:
        detail = exc.result.stderr or exc.result.stdout or str(exc)
        _record_audit(db, user, "backup.restore", body.timestamp, "failed", detail)
        logger.error("backup.restore failed for %s: %s", body.timestamp, detail)
        raise HTTPException(
            status_code=500,
            detail=_clean_cli_detail(detail, "Backup restore failed."),
        )


@router.post("/backups/restore-file")
def restore_backup_file(
    body: BackupFileBody,
    db: Session = Depends(get_db),
    user: User = require_perm(P_BACKUPS_RESTORE),
    server: str = Depends(require_server),
):
    base_dir = get_server_base_dir(server)
    live_path = _resolve_live_path(base_dir, body.path)
    run_dir = _get_backup_run_dir(server, body.timestamp)
    archive_path, member_name = _backup_member_for_path(base_dir, run_dir, live_path)

    try:
        data = _read_member_bytes(archive_path, member_name)
        _write_bytes_atomic(live_path, data, base_dir)
        _record_audit(db, user, "backup.restore_file", body.path, "success", body.timestamp)
        return {"ok": True, "path": body.path, "timestamp": body.timestamp}
    except HTTPException as exc:
        _record_audit(db, user, "backup.restore_file", body.path, "failed", str(exc.detail))
        raise
    except PermissionError as exc:
        _record_audit(db, user, "backup.restore_file", body.path, "failed", str(exc))
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT) from exc
    except OSError as exc:
        _record_audit(db, user, "backup.restore_file", body.path, "failed", str(exc))
        logger.error("backup.restore_file failed for %s: %s", body.path, exc)
        raise HTTPException(status_code=500, detail="Backup file restore failed.")
