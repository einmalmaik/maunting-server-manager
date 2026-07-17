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
from .webhooks_outbound import router as webhooks_outbound_router
from .singra_webhook import router as singra_webhook_router
from .backup_config import router as backup_config_router
from .panel_backups import router as panel_backups_router
from .panel_database import router as panel_database_router
from .nodes import router as nodes_router

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
    "webhooks_outbound_router",
    "singra_webhook_router",
    "backup_config_router",
    "panel_backups_router",
    "panel_database_router",
    "nodes_router",
]  # noqa: E501
