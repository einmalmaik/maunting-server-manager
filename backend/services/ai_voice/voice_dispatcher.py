from __future__ import annotations

import re
from uuid import uuid4

from database import SessionLocal
from models.user import User
from services import ai_action_service
from services.openai_compatible_adapter import ProviderToolCall
from services.ai_tool_registry import WERKZEUGE, WRITE_TOOLS
from services.semantic_tool_router_adapter import SemanticToolRouterAdapter


def dispatch_voice_action(
    user_id: int,
    arguments: dict,
    *,
    conversation_id: str | None = None,
    herkunft: str = "panel",
    familie: str | None = None,
) -> tuple[object, str | None, dict, list[dict]]:
    """Löst eine per Sprache gewünschte Aktion semantisch über die gesamte Tool-Registry auf.

    Ermöglicht dem Realtime-Sprachmodus Zugriff auf das komplette Werkzeugset
    (Mods, Configs, Ports, Backups, Tasks etc.), ohne dass alle Schemas dauerhaft
    im Sprach-Prompt liegen müssen.
    """
    from services.ai_stream.read_tools import _anzeigeeintrag, _werkzeug_ausfuehren
    from services.ai_stream.write_tools import _persist_write_proposals

    action = str(arguments.get("action") or "").strip()
    explicit_tool = arguments.get("tool_name")
    server_id = arguments.get("server_id")
    raw_params = arguments.get("parameters")
    extra_params = dict(raw_params) if isinstance(raw_params, dict) else {}

    if not action and not explicit_tool:
        wert = {"error": "Keine Aktion angegeben"}
        return wert, "Keine Aktion angegeben", {"tool_name": "execute_server_action", "failed": True}, []

    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            wert = {"error": "Benutzer nicht gefunden"}
            return wert, "Benutzer nicht gefunden", {"tool_name": "execute_server_action", "failed": True}, []
        allowed = (
            ai_action_service.angebotene_werkzeuge(db, user)
            - {"execute_server_action", "worker_start", "worker_cancel", "worker_antwort", "ask_user", "wait_until"}
        )

    target_tool: str | None = None
    if isinstance(explicit_tool, str) and explicit_tool in allowed:
        target_tool = explicit_tool
    elif action:
        router = SemanticToolRouterAdapter()
        router.warm(allowed)
        candidates = router.select(action, allowed, top_k=1)
        if candidates:
            target_tool = candidates[0]

    if not target_tool or target_tool not in allowed:
        wert = {"error": f"Keine passende Aktion für '{action}' verfügbar oder berechtigt."}
        return wert, "Aktion nicht verfügbar", {"tool_name": "execute_server_action", "failed": True}, []

    spec = WERKZEUGE.get(target_tool)
    if spec is None:
        wert = {"error": f"Unbekanntes Zielwerkzeug: {target_tool}"}
        return wert, "Unbekanntes Werkzeug", {"tool_name": "execute_server_action", "failed": True}, []

    target_args: dict[str, object] = dict(extra_params)
    if server_id is not None and "server_id" not in target_args:
        try:
            target_args["server_id"] = int(server_id)
        except (TypeError, ValueError):
            pass

    if "server_id" not in target_args and spec.art in {"server_read", "server_write"}:
        m = re.search(r"server\s*(\d+)", action, re.IGNORECASE)
        if m:
            target_args["server_id"] = int(m.group(1))

    if "query" not in target_args and target_tool in {
        "search_workshop_mods",
        "search_server_files",
        "web_search",
        "email_search",
        "search_docs",
        "search_memory",
    }:
        target_args["query"] = action

    call_id = str(uuid4())
    target_call = ProviderToolCall(id=call_id, name=target_tool, arguments=target_args)

    if spec.art in {"server_write", "global_write"} or target_tool in WRITE_TOOLS:
        if "rationale" not in target_args:
            target_args["rationale"] = (
                f"Per Sprachbefehl angefordert: {action[:100]}"
                if action
                else f"Per Sprachbefehl: {target_tool}"
            )
        if "begruendung" not in target_args:
            target_args["begruendung"] = target_args["rationale"]

        if not conversation_id:
            with SessionLocal() as db_conv:
                from models.ai_conversation import AiConversation
                from services.ai_chat_service import konversation_anlegen
                conv = (
                    db_conv.query(AiConversation)
                    .filter(AiConversation.user_id == user_id)
                    .order_by(AiConversation.updated_at.desc(), AiConversation.id.desc())
                    .first()
                )
                if conv:
                    conversation_id = conv.id
                else:
                    usr = db_conv.get(User, user_id)
                    if usr:
                        conv = konversation_anlegen(db_conv, usr, "Aktionen")
                        conversation_id = conv.id

        if not conversation_id:
            fehler = "Keine aktive Konversation für Schreibvorschlag"
            return {"error": fehler}, fehler, {"tool_name": target_tool, "failed": True}, []

        vorschlaege = _persist_write_proposals(
            user_id=user_id,
            conversation_id=conversation_id,
            tool_calls=[target_call],
            correlation_id=str(uuid4()),
            run_id=None,
        )
        fehler = next((str(v.get("error")) for v in vorschlaege if v.get("error")), None)
        wert = {
            "executed_tool": target_tool,
            "status": "proposal_created",
            "proposals": vorschlaege,
            "message": "Vorschlagskarte wurde im Panel erstellt. Bitte den Benutzer um Bestätigung.",
        }
        return wert, fehler, _anzeigeeintrag(target_call, wert, fehler), vorschlaege

    wert, fehler = _werkzeug_ausfuehren(
        user_id, target_call, herkunft=herkunft, familie=familie
    )
    res_wert = {
        "executed_tool": target_tool,
        "data": wert,
    }
    return res_wert, fehler, _anzeigeeintrag(target_call, wert, fehler), []
