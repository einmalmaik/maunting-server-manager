from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class PanelPopupResponse(BaseModel):
    id: int
    title: str
    content_markdown: str
    is_active: bool
    start_at: datetime | None = None
    end_at: datetime | None = None
    button_text: str | None = None
    button_url: str | None = None
    created_at: datetime
    updated_at: datetime


class PanelPopupCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content_markdown: str = Field(..., min_length=1, max_length=32000)
    is_active: bool = True
    start_at: datetime | None = None
    end_at: datetime | None = None
    button_text: str | None = Field(None, max_length=100)
    button_url: str | None = Field(None, max_length=2048)


class PanelPopupUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    content_markdown: str | None = Field(None, min_length=1, max_length=32000)
    is_active: bool | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    button_text: str | None = Field(None, max_length=100)
    button_url: str | None = Field(None, max_length=2048)


class PopupDismissRequest(BaseModel):
    mode: str = Field("snooze", pattern="^(snooze|permanent)$")
