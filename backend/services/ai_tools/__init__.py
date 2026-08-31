from __future__ import annotations

import logging
import json
from uuid import UUID, uuid4
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException

from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    READ_TOOLS,
    WERKZEUGE,
    angebotsrechte,
)
from services.ai_tools.base import (
    CONFIRMATION_TTL,
    MAX_CONFIG_CHARS,
    MAX_DIFF_CHARS,
    MAX_DIFF_LINES,
    MAX_READ_CONFIG_CHARS,
    MAX_LOG_CHARS,
    MAX_READ_CONFIG_LINES,
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_FILES,
    MAX_SEARCH_DEPTH,
    MAX_SEARCH_MATCHES,
    MAX_SEARCH_LINE_CHARS,
    MAX_SEARCH_CONTEXT_LINES,
    MAX_PATCH_EDITS,
    MAX_PATCH_CHUNK_CHARS,
    MAX_LISTED_MODS,
    MAX_LISTED_BACKUPS,
    MAX_LISTED_INCIDENTS,
    MAX_LISTED_ACTIONS,
    MAX_LISTED_BLUEPRINTS,
    MAX_LISTED_NODES,
    MAX_LISTED_SERVERS,
    MAX_REASON_CHARS,
    MAX_BACKUP_NAME_CHARS,
    MAX_QUESTION_OPTIONS,
    MAX_QUESTION_CHARS,
    MAX_OPTION_CHARS,
    MAX_OPTION_HINT_CHARS,
    MAX_DESKTOP_INHALT_CHARS,
    MAX_AUFRAEUM_PFADE,
    MAX_INCIDENT_ATTEMPTS,
    MAX_TESTMAILS_JE_STUNDE,
    _SERVER_ID_SCHEMA,
    _MUTEX_TOOLS,
    _RATIONALE_SCHEMA,
    _RATIONALE_REQUIRED,
    _MEMORY_TEAM_SCHEMA,
    _PLAN_SCHEMA,
    _MEMORY_KEY_RE,
    _function,
    _server_function,
    _vorfall_versuche,
    _require_no_arguments,
    _visible_servers,
    _resolve_server,
    _node_health,
    is_binary_text,
    _config_path,
    _positive_int,
)
from services.ai_tools.task_tools import (
    _aufgaben_tool_definitions,
    _worker_tool_definitions,
)
from services.ai_tools.personal_tools import (
    _TESTMAILS,
    _mailbox_and_calendar_tool_definitions,
    _notes_tool_definitions,
    _execute_send_test_email,
)
from services.ai_tools.geo_tools import (
    _voice_tool_definitions,
    voice_control_tool_definitions,
    _region_request,
    execute_realtime_region_initial,
    execute_realtime_region_enrichment,
    _execute_analyze_region,
    _execute_control_region_camera,
)
from services.ai_tools.system_tools import (
    _desktop_tool_definitions,
    _execute_set_agent_name,
    _memory_team,
    _execute_remember,
    question_payload,
    _execute_search_memory,
    _execute_forget_memory,
    _execute_forget_skill,
    _execute_search_docs,
    _execute_read_docs,
    _execute_read_skill,
    _execute_learn_skill,
    _execute_web_search,
)
from services.ai_action_errors import (
    AiActionValidationError,
    AiActionStateError,
)
from services.ai_tools.server_tools import (
    _global_tool_definitions,
    provider_tool_definitions,
    angebotene_werkzeuge,
    _execute_global_read_tool,
    _execute_server_context_tool,
    _execute_mod_tool,
    _execute_file_search,
    execute_read_tool,
)

