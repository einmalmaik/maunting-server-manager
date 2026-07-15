from datetime import datetime
from pydantic import BaseModel


class NodeCreate(BaseModel):
    name: str
    host: str


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
    server_count: int

    class Config:
        from_attributes = True
