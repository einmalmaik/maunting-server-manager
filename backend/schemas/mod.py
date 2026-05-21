from datetime import datetime
from pydantic import BaseModel


class ModResponse(BaseModel):
    id: int
    server_id: int
    workshop_id: str
    name: str | None
    last_updated: datetime | None
    installed_version: int | None
    load_order: int | None
    auto_update: bool
    dependencies_json: str | None

    class Config:
        from_attributes = True
