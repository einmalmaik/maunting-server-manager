from __future__ import annotations

from services.ai_tool_registry import WERKZEUGE


def realtime_tool_schema(entry: dict) -> dict | None:
    func = entry.get("function") if isinstance(entry, dict) else None
    if not isinstance(func, dict):
        return None
    name = func.get("name")
    if not isinstance(name, str) or name not in WERKZEUGE:
        return None
    spec = WERKZEUGE[name]
    description = func.get("description") or ""
    if spec.art in {"server_write", "global_write"}:
        description = description + " Im Sprachmodus nur Vorschlag erzeugen; Bestaetigung via voice_resolve_latest_proposal."
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": func.get("parameters"),
    }


def chat_tool_schema(entry: dict) -> dict | None:
    func = entry.get("function") if isinstance(entry, dict) else None
    if not isinstance(func, dict):
        return None
    name = func.get("name")
    if not isinstance(name, str) or name not in WERKZEUGE:
        return None
    return {"type": "function", "function": func}
