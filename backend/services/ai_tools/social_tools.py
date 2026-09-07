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
