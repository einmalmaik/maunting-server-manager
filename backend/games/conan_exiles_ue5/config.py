"""
Conan Exiles UE5 — Config-Parser und Validierung.
Hier können später INI-Parser und Validierungsregeln ergänzt werden.
"""

from games.base import ConfigField

CONAN_CONFIG_SCHEMA: list[ConfigField] = [
    ConfigField("MaxNumbPlayers", "Max. Spieler", "number", default=40),
    ConfigField("ServerPassword", "Server-Passwort", "text", default=""),
    ConfigField("AdminPassword", "Admin-Passwort", "text", default="", required=True),
    ConfigField("serverVoiceChat", "Voice Chat", "bool", default=True),
    ConfigField("serverVoiceChat3D", "3D Voice Chat", "bool", default=True),
]
