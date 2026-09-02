from pydantic import BaseModel, Field


class PermissionDefResponse(BaseModel):
    key: str
    group: str
    label: str


class PermissionCatalogResponse(BaseModel):
    global_permissions: list[PermissionDefResponse]
    server_permissions: list[PermissionDefResponse]


class MePermissionsResponse(BaseModel):
    is_owner: bool
    role_id: int | None
    role_name: str | None
    role_ids: list[int] = Field(default_factory=list)
    role_names: list[str] = Field(default_factory=list)
    global_keys: list[str]
    # Per-Server-Delegationen des aktuellen Users: server_id -> [permission_keys]
    server_keys: dict[int, list[str]]
