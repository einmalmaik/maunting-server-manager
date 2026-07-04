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
    # S3-Cloud-Status (M1). Lokale Backups haben s3_key=None, encrypted=False.
    s3_key: str | None = None
    s3_bucket: str | None = None
    encrypted: bool = False

    class Config:
        from_attributes = True
