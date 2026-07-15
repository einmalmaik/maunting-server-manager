from datetime import datetime

from pydantic import BaseModel, Field


class NodeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    host: str = Field(..., min_length=1, max_length=255)
    # Plaintext agent token — encrypted before DB store; never returned in responses
    auth_token: str = Field(..., min_length=16, max_length=512)


class NodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    auth_token: str | None = Field(default=None, min_length=16, max_length=512)


class NodeOut(BaseModel):
    id: int
    name: str
    host: str
    is_local: bool
    status: str
    cpu_total: float | None = None
    ram_total: int | None = None
    disk_total: int | None = None
    last_heartbeat: datetime | None = None
    server_count: int = 0
    # Optional live metrics from agent (GET /api/nodes/{id})
    metrics: dict | None = None

    class Config:
        from_attributes = True
