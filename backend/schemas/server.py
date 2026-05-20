from datetime import datetime
from pydantic import BaseModel, Field


class ServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    game_type: str = Field(..., pattern=r"^[a-z_]+$")
    auto_restart: bool = False
    restart_interval_hours: int | None = Field(None, ge=1, le=168)
    restart_time_utc: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    cpu_limit_percent: int | None = Field(None, ge=10, le=100)
    ram_limit_mb: int | None = Field(None, ge=512)
    disk_limit_gb: int | None = Field(None, ge=1)


class ServerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    auto_restart: bool | None = None
    restart_interval_hours: int | None = Field(None, ge=1, le=168)
    restart_time_utc: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    cpu_limit_percent: int | None = Field(None, ge=10, le=100)
    ram_limit_mb: int | None = Field(None, ge=512)
    disk_limit_gb: int | None = Field(None, ge=1)


class ServerResponse(BaseModel):
    id: int
    name: str
    game_type: str
    install_dir: str
    linux_user: str
    status: str
    status_message: str | None
    auto_restart: bool
    restart_interval_hours: int | None
    restart_time_utc: str | None
    cpu_limit_percent: int | None
    ram_limit_mb: int | None
    disk_limit_gb: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class ServerStatusResponse(BaseModel):
    id: int
    status: str
    status_message: str | None
    cpu_percent: float | None
    ram_mb: int | None
    disk_mb: int | None
    uptime_seconds: int | None
    players_online: int | None
