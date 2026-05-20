"""
DayZ — Config-Parser und Validierung.
DayZ nutzt serverDZ.cfg (C++-Config-Syntax) und XML-Dateien.
"""

from games.base import ConfigField

DAYZ_CONFIG_SCHEMA: list[ConfigField] = [
    ConfigField("hostname", "Server-Name", "text", default="DayZ Server", required=True),
    ConfigField("password", "Server-Passwort", "text", default=""),
    ConfigField("passwordAdmin", "Admin-Passwort", "text", default="", required=True),
    ConfigField("maxPlayers", "Max. Spieler", "number", default=60),
    ConfigField("serverTime", "Server-Zeit", "text", default="8:00"),
    ConfigField("serverTimeAcceleration", "Zeit-Beschleunigung", "number", default=1),
]
