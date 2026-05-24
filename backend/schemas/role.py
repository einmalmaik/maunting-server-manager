from datetime import datetime

from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str | None = Field(None, max_length=255)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str | None = Field(None, max_length=255)
    permissions: list[str] | None = None


class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AssignRoleRequest(BaseModel):
    role_id: int | None  # None = Rolle entfernen


class ServerPermissionsRequest(BaseModel):
    permissions: list[str] = Field(default_factory=list)


class ServerPermissionsResponse(BaseModel):
    server_id: int
    permissions: list[str]
