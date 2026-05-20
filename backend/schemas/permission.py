from pydantic import BaseModel


class PermissionCreate(BaseModel):
    user_id: int
    server_id: int
    can_start: bool = False
    can_stop: bool = False
    can_restart: bool = False
    can_update: bool = False
    can_edit_config: bool = False
    can_manage_mods: bool = False
    can_backup: bool = False
    can_restore: bool = False
    can_view_console: bool = False
    can_view_logs: bool = False


class PermissionResponse(BaseModel):
    id: int
    user_id: int
    server_id: int
    can_start: bool
    can_stop: bool
    can_restart: bool
    can_update: bool
    can_edit_config: bool
    can_manage_mods: bool
    can_backup: bool
    can_restore: bool
    can_view_console: bool
    can_view_logs: bool

    class Config:
        from_attributes = True
