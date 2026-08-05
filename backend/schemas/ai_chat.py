"""API-Vertraege fuer persistente AI-Gespraeche."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class AiConversationCreate(BaseModel):
    title: str = Field(default="Neue Unterhaltung", min_length=1, max_length=160)
    server_id: int | None = Field(default=None, ge=1)

    @field_validator("title")
    @classmethod
    def title_must_contain_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Titel darf nicht leer sein")
        return normalized


class AiConversationResponse(BaseModel):
    id: str
    server_id: int | None
    title: str
    created_at: datetime
    updated_at: datetime


class AiMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    status: str
    provider_id: int | None
    model: str | None
    created_at: datetime


class AiConversationDetail(AiConversationResponse):
    messages: list[AiMessageResponse] = Field(default_factory=list)


class AiChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16_000)
    provider_id: int = Field(ge=1)
    request_id: UUID
