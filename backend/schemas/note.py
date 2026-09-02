"""API-Verträge für Notizen, Checklisten und Team-Notizen."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="")
    category: str = Field(default="personal", max_length=64)
    color: str | None = Field(default="primary", max_length=32)
    is_pinned: bool = False
    note_type: str = Field(default="personal", max_length=32)
    team_id: int | None = None


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = None
    category: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    is_pinned: bool | None = None
    is_archived: bool | None = None
    note_type: str | None = Field(default=None, max_length=32)
    team_id: int | None = None


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    note_uid: str
    user_id: int
    creator_name: str | None = None
    title: str
    content: str
    category: str
    color: str | None = "primary"
    is_pinned: bool
    is_archived: bool
    note_type: str
    team_id: int | None = None
    team_name: str | None = None
    can_edit: bool = True
    created_at: datetime
    updated_at: datetime
