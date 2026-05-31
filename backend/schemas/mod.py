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
    enabled: bool
    dependencies_json: str | None
    install_status: str
    install_action: str | None
    install_progress: int | None
    install_eta_seconds: int | None
    install_started_at: datetime | None
    install_completed_at: datetime | None
    install_error: str | None

    class Config:
        from_attributes = True
