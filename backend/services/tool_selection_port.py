from __future__ import annotations

from typing import Protocol

HOTSET = frozenset({
    "list_my_servers",
    "read_server_status",
    "read_server_logs",
    "analyze_region",
    "control_region_camera",
    "web_search",
    "search_docs",
    "calendar_read",
    "notes_read",
    "propose_calendar_event_create",
    "propose_note_create",
    "remember",
    "search_memory",
    "learn_skill",
    "worker_start",
    "execute_server_action",
    "cloudflare_list_zones",
    "search_curseforge_modpacks",
    "advise_node_placement",
})


class ToolSelectionPort(Protocol):
    def select(self, query: str, allowed: frozenset[str], top_k: int = 5) -> list[str]: ...
