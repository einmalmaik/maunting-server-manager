from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException

from .models import User
from .api.deps import get_current_user

# ── Permission constants ───────────────────────────────────────────────────────

P_DASHBOARD_VIEW        = "dashboard.view"
P_SERVER_START          = "dashboard.server.start"
P_SERVER_STOP           = "dashboard.server.stop"
P_SERVER_RESTART        = "dashboard.server.restart"
P_SERVER_INSTALL        = "dashboard.server.install"
P_SERVER_UPDATE         = "dashboard.server.update"
P_SERVER_VALIDATE       = "dashboard.server.validate"
P_SERVER_WIPE           = "dashboard.server.wipe"
P_WORKSHOP_UPDATE       = "dashboard.workshop.update"

P_CONSOLE_VIEW          = "console.view"
P_CONSOLE_LOG           = "console.view.log"
P_CONSOLE_TMUX          = "console.view.tmux"
P_RCON_SEND             = "console.rcon.send"

P_FILES_READ            = "files.read"
P_FILES_WRITE           = "files.write"

P_MODS_VIEW             = "mods.view"
P_MODS_INSTALL          = "mods.install"
P_MODS_MANAGE           = "mods.manage"
P_MODS_UPDATE           = "mods.update"
P_MODS_REORDER          = "mods.reorder"

P_BACKUPS_VIEW          = "backups.view"
P_BACKUPS_CREATE        = "backups.create"
P_BACKUPS_RESTORE       = "backups.restore"

P_SERVERS_VIEW          = "servers.view"
P_SERVERS_CREATE        = "servers.create"
P_SERVERS_DELETE        = "servers.delete"

P_AUTORESTART_VIEW      = "autorestart.view"
P_AUTORESTART_MANAGE    = "autorestart.manage"

P_USERS_VIEW            = "users.view"
P_USERS_MANAGE          = "users.manage"

# All known permissions with human-readable labels
ALL_PERMISSIONS: dict[str, str] = {
    P_DASHBOARD_VIEW:     "View dashboard",
    P_SERVER_START:       "Start server",
    P_SERVER_STOP:        "Stop server",
    P_SERVER_RESTART:     "Restart server",
    P_SERVER_INSTALL:     "Install server files",
    P_SERVER_UPDATE:      "Update server files",
    P_SERVER_VALIDATE:    "Validate server files",
    P_SERVER_WIPE:        "Wipe server data",
    P_WORKSHOP_UPDATE:    "Run workshop update",
    P_CONSOLE_VIEW:       "View console page",
    P_CONSOLE_LOG:        "View server log console",
    P_CONSOLE_TMUX:       "View tmux console",
    P_RCON_SEND:          "Send RCON commands",
    P_FILES_READ:         "Read files",
    P_FILES_WRITE:        "Write / edit files",
    P_MODS_VIEW:          "View mods",
    P_MODS_INSTALL:       "Install / add mods",
    P_MODS_MANAGE:        "Manage mods (remove, toggle)",
    P_MODS_UPDATE:        "Update mods",
    P_MODS_REORDER:       "Reorder mods",
    P_BACKUPS_VIEW:       "View backups",
    P_BACKUPS_CREATE:     "Create backups",
    P_BACKUPS_RESTORE:    "Restore backups",
    P_SERVERS_VIEW:       "View / switch servers",
    P_SERVERS_CREATE:     "Create servers",
    P_SERVERS_DELETE:     "Delete servers",
    P_AUTORESTART_VIEW:   "View autorestart schedule",
    P_AUTORESTART_MANAGE: "Configure autorestart",
    P_USERS_VIEW:         "View user list",
    P_USERS_MANAGE:       "Manage users",
}

def get_effective_permissions(user: User) -> frozenset[str]:
    """Return the effective permission set for a user.

    Owner always gets everything.
    Every other user gets exactly the permissions stored in user.permissions.
    """
    if user.role == "owner":
        return frozenset(ALL_PERMISSIONS.keys())
    if user.permissions:
        try:
            parsed = json.loads(user.permissions)
            if isinstance(parsed, list) and all(isinstance(p, str) for p in parsed):
                return frozenset(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return frozenset()


def has_permission(user: User, *perms: str) -> bool:
    effective = get_effective_permissions(user)
    return all(p in effective for p in perms)


def require_perm(*perms: str) -> Any:
    """FastAPI dependency factory: raises 403 if the current user lacks any of *perms*.

    Usage::

        @router.post("/something")
        def endpoint(user: User = require_perm("dashboard.server.start"), ...):
            ...
    """
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role == "owner":
            return user
        effective = get_effective_permissions(user)
        for p in perms:
            if p not in effective:
                raise HTTPException(status_code=403, detail="Permission denied.")
        return user

    return Depends(_dep)
