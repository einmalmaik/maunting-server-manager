from datetime import datetime
from pydantic import BaseModel


class BackupResponse(BaseModel):
    id: int
    server_id: int
    name: str | None
    filename: str
    size_mb: int | None
    created_at: datetime
    expires_at: datetime | None

    class Config:
        from_attributes = True
