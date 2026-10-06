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
    "search_memory",
    "learn_skill",
    "worker_start",
    "execute_server_action",
    "cloudflare_list_zones",
    "cloudflare_list_dns_records",
    "propose_cloudflare_dns_record",
    "propose_cloudflare_dns_delete",
    "search_curseforge_modpacks",
    "search_curseforge_mods",
    "advise_node_placement",
})


class ToolSelectionPort(Protocol):
    def select(self, query: str, allowed: frozenset[str], top_k: int = 5) -> list[str]: ...


def pflichtwerkzeuge(erlaubt: frozenset[str], herkunft: str | None) -> frozenset[str]:
    """Was kein Router-Schnitt entfernen darf, ueber ``HOTSET`` hinaus.

    Aus der App gehoeren Sehen, Zeigen und Programmstarten (``GEHIRN_DESKTOP``)
    immer ins Angebot. Bis zum 05.10.2026 entschied der semantische Router
    auch ueber sie: "klick auf Start" oder "was siehst du?" traf selten die
    Beschreibung von ``desktop_steuern``, und Singra sagte, sie habe keinen
    Zugriff auf den Rechner — bei eingeschaltetem Computer-Use.

    Nur was ``erlaubt`` ohnehin enthaelt: die Rechte und ``herkunft_schnitt``
    haben vorher entschieden, diese Menge oeffnet nichts. Aus dem Panel ist
    sie leer.
    """
    if herkunft != "desktop":
        return frozenset()
    from services.ai_tool_registry import GEHIRN_DESKTOP

    return GEHIRN_DESKTOP & erlaubt
