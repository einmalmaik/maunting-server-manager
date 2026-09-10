from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


from services.chat_media_validator import MAX_MEDIA_BYTES


class ChatMediaUploadRequest(BaseModel):
    blind_mailbox_id: str = Field(..., min_length=16, max_length=64)
    ciphertext_blob: str = Field(..., min_length=1, max_length=MAX_MEDIA_BYTES + 4096)
    file_name: str = Field(..., min_length=1, max_length=256)
    media_type: str = Field(default="application/octet-stream", max_length=64)
    group_id: int | None = None
    recipient_id: int | None = None


class ChatMediaUploadResponse(BaseModel):
    id: str
    blind_mailbox_id: str
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


class ChatMediaSignedUrlResponse(BaseModel):
    media_id: str
    signed_url: str
    expires_at: datetime
