"""Begrenzte API-Vertraege fuer einsehbares AI-Memory."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


MemoryScope = Literal["user", "server", "team", "panel"]


class AiMemoryWrite(BaseModel):
    scope: MemoryScope
    server_id: int | None = Field(default=None, ge=1)
    team_id: int | None = Field(default=None, ge=1)
    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    value: str = Field(min_length=1, max_length=2000)


class AiMemoryPreferenceWrite(BaseModel):
    enabled: bool


class AiMemoryResponse(BaseModel):
    id: str
    scope: MemoryScope
    server_id: int | None
    team_id: int | None = None
    key: str
    value: str
    # "user" = du hast es hinterlegt, "ai" = die KI hat es sich gemerkt.
    # Sichtbar, damit niemand raten muss, woher ein Eintrag stammt.
    origin: Literal["user", "ai"] = "user"
    use_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AiMemoryPreferenceResponse(BaseModel):
    enabled: bool
