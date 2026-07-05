"""Erkennung und Recovery fuer interaktive Auth-Flows in Server-Containern.

Rein generisch: kein Wissen ueber einzelne Spiele. Erkennung laeuft ueber
Log-Pattern + Filesystem, Blueprint-Schema bleibt unangetastet.
"""
from __future__ import annotations

import re

# OAuth/Auth-Fehler im Server-Log. Hytale, generic-discord-bots, anything
# that uses oauth2 against a third-party token service matches this.
_OAUTH_PATTERNS = (
    re.compile(r'oauth2:.*"(invalid_grant|invalid_token|expired)"', re.IGNORECASE),
    re.compile(r"refresh token expired", re.IGNORECASE),
    re.compile(r"could not get signed URL.*manifest", re.IGNORECASE),
    re.compile(r"please visit the following URL to authenticate", re.IGNORECASE),
    re.compile(r"authorization code:", re.IGNORECASE),
)


def detect_auth_required(log_lines: list[str]) -> bool:
    """True wenn der Container-Output einen interaktiven Auth-Flow verlangt.

    Reine Funktion; keine Side-Effects, keine DB-Zugriffe. Wird sowohl im
    Lifecycle-Thread nach Container-Exit als auch im WebSocket-Stream
    periodisch aufgerufen.
    """
    for line in log_lines:
        for pattern in _OAUTH_PATTERNS:
            if pattern.search(line):
                return True
    return False