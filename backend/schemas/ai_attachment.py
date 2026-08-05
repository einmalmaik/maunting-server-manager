"""Metadaten-only API fuer isolierte AI-Anhaenge."""

from datetime import datetime

from pydantic import BaseModel


class AiAttachmentResponse(BaseModel):
    id: str
    conversation_id: str
    original_name: str
    media_type: str
    size_bytes: int
    status: str
    rejection_code: str | None
    created_at: datetime
