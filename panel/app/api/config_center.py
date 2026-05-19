from __future__ import annotations

import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..permissions import P_FILES_READ, P_FILES_WRITE, require_perm
from ..server_layout import (
    SERVERDZ_SCHEMA_SOURCE,
    collect_recent_files,
    get_mission_folder,
    get_server_base_dir,
    get_server_cfg_path,
    resolve_quick_directories,
    resolve_quick_files,
)
from ..serverdz import parse_serverdz, render_serverdz
from .deps import require_server

router = APIRouter()
logger = logging.getLogger(__name__)
_PERMISSION_HINT = "Permission denied. Run `./conanserver.sh panel repair` as root if Linux file ownership or write bits are broken."


class ServerDzBody(BaseModel):
    known: dict[str, Any]
    custom_raw: str = ""


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


def _write_text_atomic(target: Path, content: str, base_dir: Path) -> None:
    existing_stat = None
    if target.exists():
        existing_stat = target.stat()

    _ensure_parent_writable(target.parent, base_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, mode="w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            tmp_path = Path(handle.name)
        if existing_stat is not None:
            try:
                os.chmod(tmp_path, stat.S_IMODE(existing_stat.st_mode))
                if hasattr(os, "chown"):
                    os.chown(tmp_path, existing_stat.st_uid, existing_stat.st_gid)
            except OSError:
                pass
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@router.get("/config/overview")
def get_config_overview(
    server: str = Depends(require_server),
    user: Any = require_perm(P_FILES_READ),
) -> Any:
    try:
        base_dir = get_server_base_dir(server)
        return {
            "mission_folder": get_mission_folder(base_dir),
            "quick_files": resolve_quick_files(base_dir),
            "quick_directories": resolve_quick_directories(base_dir),
            "recent_files": collect_recent_files(base_dir, limit=20),
            "schema_source": SERVERDZ_SCHEMA_SOURCE,
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        logger.error("config overview failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to build config overview.")


@router.get("/config/serverdz")
@router.get("/config/serverconfig")
def get_serverdz(
    server: str = Depends(require_server),
    user: Any = require_perm(P_FILES_READ),
) -> Any:
    try:
        base_dir = get_server_base_dir(server)
        cfg_path = get_server_cfg_path(base_dir)
        raw = cfg_path.read_text(encoding="utf-8", errors="replace") if cfg_path.exists() else ""
        parsed = parse_serverdz(raw)
        return {
            "path": str(Path(cfg_path).relative_to(base_dir).as_posix()),
            "raw": raw,
            "known": parsed["known"],
            "custom_raw": parsed["custom_raw"],
            "groups": parsed["groups"],
            "fields": parsed["fields"],
            "schema_source": SERVERDZ_SCHEMA_SOURCE,
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        logger.error("server config read failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read ServerSettings.ini.")


@router.put("/config/serverdz")
@router.put("/config/serverconfig")
def put_serverdz(
    body: ServerDzBody,
    server: str = Depends(require_server),
    user: Any = require_perm(P_FILES_WRITE),
) -> Any:
    try:
        base_dir = get_server_base_dir(server)
        cfg_path = get_server_cfg_path(base_dir)
        rendered = render_serverdz(body.known, body.custom_raw.replace("\r\n", "\n").replace("\r", "\n"))
        _write_text_atomic(cfg_path, rendered, base_dir)
        parsed = parse_serverdz(rendered)
        return {
            "ok": True,
            "path": str(Path(cfg_path).relative_to(base_dir).as_posix()),
            "raw": rendered,
            "known": parsed["known"],
            "custom_raw": parsed["custom_raw"],
            "groups": parsed["groups"],
            "fields": parsed["fields"],
            "schema_source": SERVERDZ_SCHEMA_SOURCE,
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail=_PERMISSION_HINT)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except OSError as exc:
        logger.error("server config write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to write ServerSettings.ini.")
