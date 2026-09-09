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
        _function(
            "propose_message_contact",
            "Schlägt Direktnachricht an einen Kontakt vor. Löst Memory-Aliase ('bester Freund'), Spitznamen und Tippfehler automatisch auf. Zero-Knowledge E2EE.",
            {
                "recipient_username": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Benutzername, Beziehungs-Alias oder Spitzname des Empfängers.",
                },
                "message_text": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Textinhalt der Nachricht.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["recipient_username", "message_text", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_message_group",
            "Schlägt Gruppennachricht im Messenger vor. Wird mit Gruppen-E2EE verschlüsselt an die Mailbox übertragen.",
            {
                "group_name_or_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Name oder ID der Zielgruppe.",
                },
                "message_text": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Textinhalt der Nachricht.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["group_name_or_id", "message_text", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_message_friend",
            "Schlägt Nachricht an einen Freund im Social Hub vor. Löst Memory-Aliase, Spitznamen und Tippfehler automatisch auf. Erfordert Benutzerbestätigung.",
            {
                "friend_username": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Benutzername, Beziehungs-Alias oder Spitzname des Freundes.",
                },
                "message_text": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Textinhalt der Nachricht.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["friend_username", "message_text", *_RATIONALE_REQUIRED],
        ),
    ]
