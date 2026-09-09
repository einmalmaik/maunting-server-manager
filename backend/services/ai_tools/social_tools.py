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
            "Sucht im Messenger nach Kontakten (Freunde, Teamkollegen oder öffentliche Profile) anhand von Name oder Benutzername. "
            "Datenschutz (Zero-Knowledge): Liefert ausschließlich Benutzernamen und Status zurück, niemals Chatverläufe oder Nachrichten.",
            {
                "query": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Suchbegriff für den Namen oder Benutzernamen des Kontakts (z. B. 'Max', 'anna', 'alice').",
                },
            },
            ["query"],
        ),
        _function(
            "search_messenger_groups",
            "Sucht nach Gruppen im Messenger, in denen der Benutzer Mitglied ist, anhand des Gruppennamens. "
            "Datenschutz (Zero-Knowledge): Liefert ausschließlich Gruppen-ID und Name zurück, niemals Chatverläufe.",
            {
                "query": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Suchbegriff für den Gruppennamen (z. B. 'Entwickler', 'Gaming').",
                },
            },
            ["query"],
        ),
        _function(
            "propose_message_contact",
            "Schlägt das Senden einer Direktnachricht an einen Kontakt (Freund, Teammitglied oder öffentlichen Benutzer) im Messenger vor. "
            "Wird Ende-zu-Ende verschlüsselt an die Blind-Mailbox des Kontakts übertragen.",
            {
                "recipient_username": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Benutzername des Empfängers.",
                },
                "message_text": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Vollständiger Textinhalt der Nachricht.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["recipient_username", "message_text", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_message_group",
            "Schlägt das Senden einer Nachricht in eine Chat-Gruppe im Messenger vor, in der der Benutzer Mitglied ist. "
            "Wird mit Gruppen-E2EE verschlüsselt an die Blind-Mailbox der Gruppe übertragen.",
            {
                "group_name_or_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Name oder ID der Zielgruppe.",
                },
                "message_text": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Vollständiger Textinhalt der Nachricht.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["group_name_or_id", "message_text", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_message_friend",
            "Schlägt das Verfassen und Senden einer Nachricht an einen bestätigten Freund im Social Hub vor. "
            "Sicherheit: Die Nachricht darf ausschließlich an bereits bestätigte Freunde gesendet werden. "
            "Erfordert zwingend eine Bestätigung des Benutzers vor dem Senden.",
            {
                "friend_username": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Benutzername des bestätigten Freundes.",
                },
                "message_text": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Vollständiger Textinhalt der Nachricht.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["friend_username", "message_text", *_RATIONALE_REQUIRED],
        ),
    ]
