from .auth import router as auth_router
from .admin import router as admin_router
from .servers import router as servers_router
from .backups import router as backups_router
from .mods import router as mods_router
from .system import router as system_router
from .steam import router as steam_router
from .panel_settings import router as panel_settings_router
from .files import router as files_router
from .roles import router as roles_router
from .permissions import router as permissions_router
from .blueprints import router as blueprints_router
from .oauth import router as oauth_router
from .databases import router as databases_router

__all__ = [
    "auth_router",
    "admin_router",
    "servers_router",
    "backups_router",
    "mods_router",
    "system_router",
    "steam_router",
    "panel_settings_router",
    "files_router",
    "roles_router",
    "permissions_router",
    "blueprints_router",
    "oauth_router",
    "databases_router",
]
