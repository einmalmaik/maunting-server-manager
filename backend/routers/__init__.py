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
from .incidents import router as incidents_router
from .change_timeline import router as change_timeline_router
from .guardian import router as guardian_router
from .ai_settings import router as ai_settings_router
from .tasks import router as tasks_router
from .ai_providers import router as ai_providers_router
from .ai_chat import router as ai_chat_router
from .ai_voice import router as ai_voice_router
from .ai_actions import router as ai_actions_router
from .ai_approvals import router as ai_approvals_router
from .ai_autonomy import router as ai_autonomy_router
from .ai_memory import router as ai_memory_router
from .ai_skills import router as ai_skills_router
from .ai_attachments import router as ai_attachments_router
from .credentials import router as credentials_router
from .teams import router as teams_router
from .hoster_admin import router as hoster_admin_router
from .hoster_api import router as hoster_api_router, redeem_router as hoster_handoff_router

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
    "incidents_router",
    "change_timeline_router",
    "guardian_router",
    "ai_settings_router",
    "tasks_router",
    "ai_providers_router",
    "ai_chat_router",
    "ai_voice_router",
    "ai_actions_router",
    "ai_approvals_router",
    "ai_autonomy_router",
    "ai_memory_router",
    "ai_skills_router",
    "ai_attachments_router",
    "credentials_router",
    "teams_router",
    "hoster_admin_router",
    "hoster_api_router",
    "hoster_handoff_router",
]  # noqa: E501
