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
    "worker_start",
})


class ToolSelectionPort(Protocol):
    def select(self, query: str, allowed: frozenset[str], top_k: int = 5) -> list[str]: ...
