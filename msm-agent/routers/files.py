"""File manager endpoints scoped to MSM_SERVERS_DIR/<server_id>."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import settings
from services import file_service
from services.file_service import PathEscapeError, PathValidationError

router = APIRouter(prefix="/files", tags=["files"])


class WriteBody(BaseModel):
    content: str = ""


class RenameBody(BaseModel):
    old_path: str = Field(..., min_length=1)
    new_path: str = Field(..., min_length=1)


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
    try:
        data = await file.read()
        if len(data) > settings.max_upload_size:
            raise HTTPException(status_code=413, detail="Upload too large")
        file_service.write_upload(server_id, path, data)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_path_errors(exc) from exc


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
