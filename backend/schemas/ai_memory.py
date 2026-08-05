"""Begrenzte API-Vertraege fuer einsehbares AI-Memory."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


MemoryScope = Literal["user", "server", "panel"]


class AiMemoryWrite(BaseModel):
    scope: MemoryScope
    server_id: int | None = Field(default=None, ge=1)
    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    value: str = Field(min_length=1, max_length=2000)


class AiMemoryPreferenceWrite(BaseModel):
    enabled: bool


class AiMemoryResponse(BaseModel):
    id: str
    scope: MemoryScope
    server_id: int | None
    key: str
    value: str
    created_at: datetime
    updated_at: datetime


class AiMemoryPreferenceResponse(BaseModel):
    enabled: bool
