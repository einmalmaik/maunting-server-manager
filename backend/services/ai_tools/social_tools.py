from __future__ import annotations

from services.ai_tools.base import (
    _function,
    _RATIONALE_SCHEMA,
    _RATIONALE_REQUIRED,
)


def _social_tool_definitions() -> list[dict]:
    """Social- und Freundes-Werkzeuge mit strikter Sicherheitsbindung."""
    return [
        _function(
            "search_messenger_contacts",
            "Sucht im Messenger nach Kontakten (Freunde, Teams, Profile) per Name oder Memory-Alias ('bester Freund'). Fehlertolerant. "
            "Zero-Knowledge: Liefert ausschließlich Benutzernamen und Status, keine Chatverläufe.",
            {
                "query": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Suchbegriff, Beziehungs-Alias (z. B. 'bester Freund'), Spitzname oder Benutzername.",
                },
            },
            ["query"],
        ),
        _function(
            "search_messenger_groups",
            "Sucht nach Gruppen im Messenger, in denen der Benutzer Mitglied ist. "
            "Zero-Knowledge: Liefert Gruppen-ID und Name, niemals Chatverläufe.",
            {
                "query": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Suchbegriff für den Gruppennamen.",
                },
            },
            ["query"],
        ),
    ]
