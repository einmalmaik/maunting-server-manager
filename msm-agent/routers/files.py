"""File manager endpoints scoped to MSM_SERVERS_DIR/<server_id>."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import settings
from services import file_service
from services.file_service import PathEscapeError, PathValidationError

router = APIRouter(prefix="/files", tags=["files"])
MAX_SINGLE_UPLOAD_SIZE = 100 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024


class WriteBody(BaseModel):
    content: str = ""


class RenameBody(BaseModel):
    old_path: str = Field(..., min_length=1)
    new_path: str = Field(..., min_length=1)


class RuntimePatch(BaseModel):
    type: str = Field(..., pattern="^(ini|regex)$")
    file: str = Field(..., min_length=1, max_length=512)
    section: str | None = None
    key: str | None = None
    regex: str | None = None
    value: str = ""


class PrepareRuntimeBody(BaseModel):
    ensure_dirs: list[str] = Field(default_factory=list, max_length=128)
    required_files: list[str] = Field(default_factory=list, max_length=128)
    patches: list[RuntimePatch] = Field(default_factory=list, max_length=128)


class MoveBody(BaseModel):
    source_path: str = Field(..., min_length=1)
    target_path: str = Field(..., min_length=1)


class ChunkedUploadInitBody(BaseModel):
    upload_id: str = Field(..., min_length=32, max_length=32)
    path: str = ""
    filename: str = Field(..., min_length=1, max_length=255)
    total_size: int = Field(..., ge=0)


class ConfigCacheBody(BaseModel):
    patterns: list[str] = Field(default_factory=list, max_length=64)


class WorkshopFileAction(BaseModel):
    operation: str = Field(..., pattern="^(copy|symlink)$")
    source: str = Field(..., min_length=1, max_length=512)
    target: str = Field(..., min_length=1, max_length=512)
    required: bool = False


class WorkshopFilesBody(BaseModel):
    workshop_app_id: str = Field(..., pattern="^[0-9]+$")
    workshop_id: str = Field(..., pattern="^[0-9]+$")
    mode: str = Field(..., pattern="^(apply|inspect|cleanup)$")
    actions: list[WorkshopFileAction] = Field(default_factory=list, max_length=32)


class ArchiveBody(BaseModel):
    postgres: dict[str, Any] | None = None


def _map_path_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, PathValidationError):
        return HTTPException(status_code=400, detail=exc.message)
    if isinstance(exc, PathEscapeError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, NotADirectoryError):
        return HTTPException(status_code=400, detail=str(exc) or "Not a directory")
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc) or "Already exists")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail="Permission denied")
    return HTTPException(status_code=500, detail="Internal error")


@router.get("/list")
def list_files(
    server_id: str = Query(...),
    path: str = Query(default=""),
) -> list[dict[str, Any]]:
    try:
        return file_service.list_dir(server_id, path)
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.get("/disk")
def disk_info(server_id: str = Query(...)) -> dict[str, int]:
    try:
        return file_service.disk_info(server_id)
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.get("/read")
def read_file(
    server_id: str = Query(...),
    path: str = Query(...),
) -> dict[str, str]:
    try:
        content = file_service.read_text(server_id, path)
        return {"content": content}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/write")
def write_file(
    body: WriteBody,
    server_id: str = Query(...),
    path: str = Query(...),
) -> dict[str, bool]:
    try:
        file_service.write_text(server_id, path, body.content)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.delete("/delete")
def delete_file(
    server_id: str = Query(...),
    path: str = Query(...),
) -> dict[str, bool]:
    try:
        file_service.delete_path(server_id, path)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/rename")
def rename_file(
    body: RenameBody,
    server_id: str = Query(...),
) -> dict[str, bool]:
    try:
        file_service.rename_path(server_id, body.old_path, body.new_path)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/upload")
async def upload_file(
    server_id: str = Query(...),
    path: str = Query(...),
    file: UploadFile = File(...),
) -> dict[str, bool]:
    tmp_path: Path | None = None
    try:
        target = file_service.safe_path(server_id, path)
        if target == file_service.server_root(server_id):
            raise PathValidationError("Upload path must name a file")
        target.parent.mkdir(parents=True, exist_ok=True)
        destination_mode = target.stat().st_mode & 0o777 if target.is_file() else 0o644
        limit = min(settings.max_upload_size, MAX_SINGLE_UPLOAD_SIZE)
        total = 0
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=target.parent, prefix=".msm-upload-"
        ) as tmp:
            os.fchmod(tmp.fileno(), 0o600)
            tmp_path = Path(tmp.name)
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(status_code=413, detail="Upload too large")
                tmp.write(chunk)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.fchmod(tmp.fileno(), destination_mode)
        os.replace(tmp_path, target)
        tmp_path = None
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_path_errors(exc) from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@router.get("/download")
def download_file(
    server_id: str = Query(...),
    path: str = Query(...),
) -> FileResponse:
    try:
        target = file_service.safe_path(server_id, path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError("File not found")
        return FileResponse(
            path=str(target),
            filename=target.name,
            media_type="application/octet-stream",
        )
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/create-dir")
def create_dir(
    server_id: str = Query(...),
    path: str = Query(...),
) -> dict[str, bool]:
    try:
        file_service.create_dir(server_id, path)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.put("/server-root")
def ensure_server_root(server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.ensure_server_root(server_id)
        return {"ok": True}
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Server directory already exists") from exc
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.delete("/server-root")
def delete_server_root(server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.delete_server_root(server_id)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/prepare-runtime")
def prepare_runtime(body: PrepareRuntimeBody, server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.prepare_runtime(
            server_id,
            ensure_dirs=body.ensure_dirs,
            required_files=body.required_files,
            patches=[patch.model_dump() for patch in body.patches],
        )
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.get("/search")
def search_paths(server_id: str = Query(...), q: str = Query(..., min_length=1, max_length=128)) -> dict[str, Any]:
    try:
        return file_service.search_paths(server_id, q)
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/workshop")
def workshop_files(body: WorkshopFilesBody, server_id: str = Query(...)) -> dict[str, Any]:
    try:
        return file_service.workshop_files(
            server_id,
            workshop_app_id=body.workshop_app_id,
            workshop_id=body.workshop_id,
            actions=[action.model_dump() for action in body.actions],
            mode=body.mode,
        )
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/move")
def move_path(body: MoveBody, server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.move_path(server_id, body.source_path, body.target_path)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/extract")
def extract_archive(server_id: str = Query(...), path: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.extract_archive(server_id, path)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/upload/init")
def init_chunked_upload(body: ChunkedUploadInitBody, server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.init_chunked_upload(
            server_id,
            body.upload_id,
            path=body.path,
            filename=body.filename,
            total_size=body.total_size,
        )
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.put("/upload/{upload_id}/chunk")
async def append_upload_chunk(
    upload_id: str,
    server_id: str = Query(...),
    chunk: UploadFile = File(...),
) -> dict[str, int]:
    try:
        data = await chunk.read(file_service.MAX_CHUNK_SIZE + 1)
        total = file_service.append_upload_chunk(server_id, upload_id, data)
        return {"received": len(data), "total_received": total}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.get("/upload/{upload_id}/status")
def upload_status(upload_id: str, server_id: str = Query(...)) -> dict[str, int | str]:
    try:
        return {"upload_id": upload_id, "received": file_service.upload_status(server_id, upload_id)}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/upload/{upload_id}/finalize")
def finalize_upload(upload_id: str, server_id: str = Query(...)) -> dict[str, Any]:
    try:
        return file_service.finalize_chunked_upload(server_id, upload_id)
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.delete("/upload/{upload_id}")
def abort_upload(upload_id: str, server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.abort_chunked_upload(server_id, upload_id)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/config-cache/create")
def cache_configs(body: ConfigCacheBody, server_id: str = Query(...)) -> dict[str, int]:
    try:
        return {"cached_files": file_service.cache_config_files(server_id, body.patterns)}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/config-cache/restore")
def restore_configs(server_id: str = Query(...)) -> dict[str, int]:
    try:
        return {"restored_files": file_service.restore_config_files(server_id)}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.delete("/config-cache")
def clear_config_cache(server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.clear_config_cache(server_id)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.get("/archive")
def archive_server(server_id: str = Query(...)):
    """Stream a tar.gz of the server directory (panel backup path, Phase 2)."""
    from fastapi.responses import StreamingResponse

    try:
        # Validate root exists before streaming
        root = file_service.server_root(server_id)
        if not root.is_dir():
            raise FileNotFoundError("Server directory not found")
        return StreamingResponse(
            file_service.iter_archive_tar_gz(server_id),
            media_type="application/gzip",
            headers={
                "Content-Disposition": f'attachment; filename="server_{server_id}.tar.gz"'
            },
        )
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/archive")
def archive_server_with_context(body: ArchiveBody, server_id: str = Query(...)):
    """Stream server files plus optional node-local Postgres dumps."""
    from fastapi.responses import StreamingResponse

    try:
        root = file_service.server_root(server_id)
        if not root.is_dir():
            raise FileNotFoundError("Server directory not found")
        return StreamingResponse(
            file_service.iter_archive_tar_gz(server_id, postgres=body.postgres),
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="server_{server_id}.tar.gz"'},
        )
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/restore-archive")
def restore_archive(server_id: str = Query(...), archive: UploadFile = File(...)) -> dict[str, bool]:
    try:
        file_service.restore_backup_archive(server_id, archive.file)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/restore-archive/finalize")
def finalize_restore(server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.finalize_backup_restore(server_id)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc


@router.post("/restore-archive/rollback")
def rollback_restore(server_id: str = Query(...)) -> dict[str, bool]:
    try:
        file_service.rollback_backup_restore(server_id)
        return {"ok": True}
    except Exception as exc:
        raise _map_path_errors(exc) from exc
