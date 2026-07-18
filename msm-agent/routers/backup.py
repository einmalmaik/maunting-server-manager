"""POST /backup/create and /backup/restore — agent-direct S3 (Phase 6).

Request body holds ephemeral S3 credentials + encryption key (RAM only).
Never logged. Not persisted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.s3_backup_service import AgentBackupError, create_encrypted_s3_backup, restore_encrypted_s3_backup

router = APIRouter(prefix="/backup", tags=["backup"])


class S3ConfigIn(BaseModel):
    endpoint: str | None = None
    access_key: str = Field(..., min_length=1)
    secret_key: str = Field(..., min_length=1)
    bucket: str = Field(..., min_length=1)
    region: str | None = None


class BackupCreateIn(BaseModel):
    server_id: str = Field(..., min_length=1, max_length=64)
    s3_config: S3ConfigIn
    encryption_key: str = Field(..., min_length=16, description="base64 AES-256 key")
    s3_key: str = Field(..., min_length=1, max_length=512)
    postgres: dict[str, Any] | None = None


class BackupRestoreIn(BaseModel):
    server_id: str = Field(..., min_length=1, max_length=64)
    s3_config: S3ConfigIn
    encryption_key: str = Field(..., min_length=16)
    s3_key: str = Field(..., min_length=1, max_length=512)
    postgres: dict[str, Any] | None = None


def _s3_dict(cfg: S3ConfigIn) -> dict[str, Any]:
    return {
        "endpoint": cfg.endpoint or "",
        "access_key": cfg.access_key,
        "secret_key": cfg.secret_key,
        "bucket": cfg.bucket,
        "region": cfg.region or "",
    }


@router.post("/create")
def backup_create(body: BackupCreateIn) -> dict[str, Any]:
    try:
        return create_encrypted_s3_backup(
            body.server_id,
            s3=_s3_dict(body.s3_config),
            encryption_key_b64=body.encryption_key,
            s3_object_key=body.s3_key,
            postgres=body.postgres,
        )
    except AgentBackupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/restore")
def backup_restore(body: BackupRestoreIn) -> dict[str, Any]:
    try:
        return restore_encrypted_s3_backup(
            body.server_id,
            s3=_s3_dict(body.s3_config),
            encryption_key_b64=body.encryption_key,
            s3_object_key=body.s3_key,
            postgres=body.postgres,
        )
    except AgentBackupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
