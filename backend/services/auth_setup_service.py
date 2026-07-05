"""Erkennung und Recovery fuer interaktive Auth-Flows in Server-Containern.

Rein generisch: kein Wissen ueber einzelne Spiele. Erkennung laeuft ueber
Log-Pattern + Filesystem, Blueprint-Schema bleibt unangetastet.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

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


# Files die explizit als Auth-Files bekannt sind (Hytale, andere Spiele
# mit bekannter Persistenz). Diese werden IMMER moved, auch wenn das
# Generic-Pattern unten nicht matcht.
_KNOWN_AUTH_FILES: frozenset[str] = frozenset({
    ".hytale-auth-tokens.json",
    ".hytale-downloader-credentials.json",
})

# Generische Pattern: alles was nach Credential/Token aussieht.
# Case-insensitive, weil manche Spiele SCREAMING_SNAKE oder camelCase nutzen.
_GENERIC_AUTH_PATTERNS = (
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"auth.*token", re.IGNORECASE),
    re.compile(r"token.*auth", re.IGNORECASE),
    re.compile(r"\btoken\b.*\.json$", re.IGNORECASE),
)


def _is_credential_file(name: str) -> bool:
    if name in _KNOWN_AUTH_FILES:
        return True
    if not name.endswith(".json"):
        return False
    return any(p.search(name) for p in _GENERIC_AUTH_PATTERNS)


def move_credentials(install_dir: os.PathLike[str] | str) -> int:
    """Moves all credential files in ``install_dir`` to ``<name>.bak``.

    Returns the number of files moved. Idempotent: re-running is a no-op.
    """
    base = Path(install_dir)
    moved = 0
    for entry in base.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix != ".json":
            continue
        if not _is_credential_file(entry.name):
            continue
        backup = entry.with_suffix(entry.suffix + ".bak")
        if backup.exists():
            backup.unlink()  # overwrite any stale backup
        entry.rename(backup)
        moved += 1
    return moved


def wait_for_credentials(
    install_dir: os.PathLike[str] | str,
    *,
    timeout: float = 120.0,
    poll_interval: float = 1.0,
) -> Optional[Path]:
    """Pollt ``install_dir`` auf frische Credential-Files.

    Success-Bedingung: ein Live-Credential-File (kein ``.bak``-Pendant)
    ist im Verzeichnis. Das ist genau der Zustand, den der In-Container-Auth-Flow
    hinterlaesst: ``move_credentials`` hat das Original zu ``<name>.bak``
    weggeschoben, der Container schreibt jetzt das frische File ohne
    ``.bak``.

    Returnt den Pfad des neuen Credential-Files, oder None bei Timeout.
    """
    base = Path(install_dir)
    deadline = time.monotonic() + timeout
    # Schnellerer Initial-Polling: erste 30s alle 0.5s, danach alle 2s.
    fast_until = time.monotonic() + min(30.0, timeout)

    while time.monotonic() < deadline:
        for entry in base.iterdir():
            if not entry.is_file():
                continue
            if not _is_credential_file(entry.name):
                continue
            # Live-Credential-Files haben einen Namen OHNE .bak.
            # ``.bak``-Dateien sind die weg geschobenen Originale und
            # zaehlen nicht als "frisch" - der Container hat sie nicht
            # angelegt.
            if entry.suffix == ".bak":
                continue
            return entry
        if time.monotonic() < fast_until:
            time.sleep(min(0.5, poll_interval / 2))
        else:
            time.sleep(poll_interval)
    return None