from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.source_service import SourceInstallError, install_github, install_http

router = APIRouter(prefix="/sources", tags=["sources"])


class HttpSourceBody(BaseModel):
    server_id: str
    url: str
    sha256: str | None = None
    archive_type: str
    extract_to: str | None = None


class GithubSourceBody(BaseModel):
    server_id: str
    repo: str
    branch: str = "main"
    token: str | None = None
    setup_commands: list[list[str]] = Field(default_factory=list, max_length=8)
    sub_path: str | None = None
    runtime_image: str


def _run(operation) -> dict[str, Any]:
    try:
        return operation()
    except SourceInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Source installation failed") from exc


@router.post("/http")
def install_http_source(body: HttpSourceBody) -> dict[str, Any]:
    return _run(lambda: install_http(
        body.server_id,
        url=body.url,
        sha256=body.sha256,
        archive_type=body.archive_type,
        extract_to=body.extract_to,
    ))


@router.post("/github")
def install_github_source(body: GithubSourceBody) -> dict[str, Any]:
    return _run(lambda: install_github(
        body.server_id,
        repo=body.repo,
        branch=body.branch,
        token=body.token,
        setup_commands=body.setup_commands,
        sub_path=body.sub_path,
        runtime_image=body.runtime_image,
    ))
