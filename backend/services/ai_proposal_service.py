from __future__ import annotations

import logging
import json
import secrets
import hashlib
import hmac
from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException

from services.ai_action_errors import (
    AiActionValidationError,
    AiActionStateError,
)
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    GLOBAL_WRITE_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
    READ_TOOLS,
    SERVER_READ_TOOLS,
    WERKZEUGE,
    WORKER_STEUERUNG,
    WRITE_TOOLS,
    aufgaben_tools,
)
from services.file_history_service import MAX_HISTORY_EDIT_SIZE
from services.ai_proposals.base import (
    _REPARATUR_RECHTE,
    _LIFECYCLE_RECHTE,
    GuardianKontext,
    AufgabenKontext,
    _aad,
    _json_object,
    _permission_for,
    _require_tool_permission,
    _verlangt_gesichertes_backup,
    guardian_aus_lauf,
    _utc,
    _AusfuehrungsRahmen,
    _Ausgefuehrt,
)
from services.ai_proposals.server_proposals import (
    _PATCH_CONTEXT_LINES,
    _GUARDIAN_ABSCHNITTE,
    REPARATUREN,
    _REPARATUR_FOLGEN,
    _config_payload,
    _zeilenbereich,
    _patch_diff,
    _config_patch_payload,
    _config_set_payload,
    _rationale,
    _server_create_payload,
    _bind_ip_payload,
    _blueprint_change_payload,
    _blueprint_delete_payload,
    _hoster_integration_payload,
    _hoster_product_payload,
    _ai_tarif_role_payload,
    _blueprint_switch_payload,
    _backup_restore_payload,
    _mod_install_payload,
    _mod_toggle_payload,
    _server_repair_payload,
    _blueprint_startwerte,
    _guardian_tuning_payload,
    _aktuelle_restart_zeiten,
    _restart_schedule_payload,
    _backup_schedule_payload,
    _file_delete_payload,
    _modpack_install_payload,
    _execute_server_create,
    _execute_bind_ip_update,
    _execute_hoster_write,
    _execute_mod_install,
    _execute_mod_toggle,
    _execute_file_delete,
    _execute_server_repair,
    _execute_guardian_tuning,
    _ausfuehren_server_lifecycle,
    _ausfuehren_backup,
    _ausfuehren_backup_restore,
    _ausfuehren_server_blueprint_switch,
    _ausfuehren_server_delete,
    _ausfuehren_config_update,
    _ausfuehren_config_patch,
    _ausfuehren_config_set,
    _ausfuehren_bind_ip_update,
    _ausfuehren_mod_install,
    _ausfuehren_mod_toggle,
    _ausfuehren_server_repair,
    _ausfuehren_guardian_tuning,
    _ausfuehren_file_delete,
    _ausfuehren_server_create,
    _ausfuehren_blueprint_change,
    _ausfuehren_blueprint_delete,
    _ausfuehren_hoster_schreiben,
    _ausfuehren_restart_schedule_set,
    _ausfuehren_backup_schedule_set,
    _ausfuehren_modpack_install,
)
from services.ai_proposals.personal_proposals import (
    _email_send_payload,
    _calendar_event_create_payload,
    _calendar_event_delete_payload,
    _calendar_event_update_payload,
    _note_create_payload,
    _note_update_payload,
    _note_delete_payload,
    _ausfuehren_email_send,
    _ausfuehren_calendar_event_create,
    _ausfuehren_calendar_event_update,
    _ausfuehren_calendar_event_delete,
    _ausfuehren_note_create,
    _ausfuehren_note_update,
    _ausfuehren_note_delete,
)
from services.ai_proposals.network_proposals import (
    _cloudflare_dns_payload,
    _cloudflare_dns_delete_payload,
    _ausfuehren_cloudflare_dns,
    _ausfuehren_cloudflare_dns_delete,
)
from services.ai_proposals.task_proposals import (
    _AUFGABEN_FELDER,
    _popup_create_payload,
    _task_set_payload,
    _task_delete_payload,
    _ausfuehren_popup_create,
    _ausfuehren_task_set,
    _ausfuehren_task_delete,
    _ausfuehren_read_tool,
    _ausfuehren_worker_start,
    _ausfuehren_worker_cancel,
)
from services.ai_proposals.lifecycle import (
    _GLOBALE_PAYLOADS,
    _AUSFUEHRUNGEN,
    proposal_response,
    create_proposal,
    owned_proposal,
    _lock_proposal,
    confirm_proposal,
    execute_autonomously,
    execute_proposal,
    _ausfuehrung_protokollieren,
    reconcile_interrupted_actions,
    reject_proposal,
)

