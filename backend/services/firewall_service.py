"""Firewall-Service — oeffnet und schliesst Ports via UFW.

Phase 2 (Port-Manager):
- UFW-Regeln werden strikt mit dem Server-Lifecycle gekoppelt (open beim Start,
  close beim Stop / Delete).
- Jede MSM-Regel traegt den Comment-Praefix ``MSM `` plus Server-Namen.
- ``cleanup_legacy_msm_ranges()`` raeumt aeltere Port-Spannen aus Phase 1 nur
  dort weg, wo das Comment-Praefix ``MSM`` steht — fremde UFW-Regeln (SSH,
  Caddy, Custom) bleiben unangetastet.

Falls UFW nicht installiert ist, schluckt der Service alle Aufrufe still: das
Panel laeuft auf einer Maschine ohne UFW dann ohne Firewall-Hilfe weiter.
"""

from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# UFW-Comment-Praefix — wird in Regex zur Identifikation eigener Regeln genutzt.
MSM_COMMENT_PREFIX = "MSM"

_SYSTEM_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
}


def _ufw_available() -> bool:
    """True, wenn ``ufw`` auf dem Host installiert ist."""
    try:
        subprocess.run(
            ["ufw", "--version"],
            check=False, capture_output=True, env=_SYSTEM_ENV, timeout=5,
        )
        return True
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _run_ufw(*args: str) -> subprocess.CompletedProcess:
    """Fuehre ``ufw <args>`` mit festem PATH/Locale aus.

    ``check=False`` — Fehler werden geloggt aber nicht propagiert: die Aktion
    soll idempotent sein (z. B. doppeltes Schliessen).
    """
    return subprocess.run(
        ["ufw", *args],
        check=False,
        capture_output=True,
        text=True,
        env=_SYSTEM_ENV,
        timeout=10,
    )


def _allow(port: int, protocol: str, comment: str) -> None:
    result = _run_ufw("allow", f"{port}/{protocol}", "comment", comment)
    if result.returncode != 0:
        logger.warning(
            "UFW allow %s/%s fehlgeschlagen: %s",
            port, protocol, (result.stderr or result.stdout).strip(),
        )


def _delete(port: int, protocol: str) -> None:
    # ``ufw delete allow PORT/PROTO`` ist idempotent: nicht existierende Regeln
    # geben Exit 0 mit "Could not delete ..." aus.
    result = _run_ufw("delete", "allow", f"{port}/{protocol}")
    if result.returncode != 0:
        logger.debug(
            "UFW delete %s/%s ohne Treffer: %s",
            port, protocol, (result.stderr or result.stdout).strip(),
        )


def _comment_for(name: str, role: str) -> str:
    """Baut einen UFW-Kommentar — ``MSM <name> <role>`` (max 32 Zeichen)."""
    # UFW erlaubt bis zu 64 Zeichen Kommentare; wir bleiben konservativ.
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:24]
    return f"{MSM_COMMENT_PREFIX} {safe_name} {role}"


# ── Public API ───────────────────────────────────────────────────────────


def open_ports(
    name: str,
    game_port: int | None,
    query_port: int | None = None,
    rcon_port: int | None = None,
) -> bool:
    """Oeffnet die Spiel-Ports eines Servers in UFW.

    Args:
        name: Server-Name (fliesst in den Kommentar).
        game_port: Haupt-Game-Port (UDP). Wenn ``None`` oder 0 → wird ueber-
            sprungen.
        query_port: optionaler Query-Port (UDP).
        rcon_port: optionaler RCon-Port (TCP).

    Returns:
        ``True``, wenn UFW vorhanden ist und Calls abgesetzt wurden; sonst
        ``False``.
    """
    if not _ufw_available():
        return False
    if game_port:
        _allow(game_port, "udp", _comment_for(name, "game"))
    if query_port:
        _allow(query_port, "udp", _comment_for(name, "query"))
    if rcon_port:
        _allow(rcon_port, "tcp", _comment_for(name, "rcon"))
    return True


def close_ports(
    game_port: int | None,
    query_port: int | None = None,
    rcon_port: int | None = None,
) -> bool:
    """Schliesst (idempotent) die UFW-Regeln eines Servers."""
    if not _ufw_available():
        return False
    if game_port:
        _delete(game_port, "udp")
    if query_port:
        _delete(query_port, "udp")
    if rcon_port:
        _delete(rcon_port, "tcp")
    return True


# ── Legacy-Range-Cleanup ─────────────────────────────────────────────────

# Erkennt die Phase-1-Range-Eintraege aus ``install.sh``:
#   ALLOW       27015:27999/udp  # MSM Game-Server UDP
# UFW formatiert das in ``ufw status numbered`` als
#   [  N] 27015:27999/udp  ALLOW  Anywhere  (# MSM Game-Server UDP)
_RANGE_LINE_RE = re.compile(
    r"^\s*\[\s*(?P<num>\d+)\s*\]\s+"
    r"(?P<rule>\d+:\d+/(?:tcp|udp))\b.*?#\s*MSM\b",
    re.IGNORECASE,
)


def cleanup_legacy_msm_ranges() -> int:
    """Entfernt alte MSM-Port-Spannen (z. B. ``27015:27999/udp``) aus UFW.

    Phase 2 oeffnet nur noch Einzelports. Aeltere Setups haben aus
    ``install.sh`` heraus eine pauschale Range angelegt — diese loeschen wir
    einmalig beim Panel-Start. Wir loeschen NUR Regeln, deren Kommentar mit
    ``MSM`` beginnt — fremde UFW-Regeln bleiben unberuehrt.

    Returns:
        Anzahl der entfernten Regeln (0, wenn UFW fehlt oder nichts zu tun).
    """
    if not _ufw_available():
        return 0

    status = _run_ufw("status", "numbered")
    if status.returncode != 0:
        logger.warning("UFW status numbered fehlgeschlagen: %s", status.stderr.strip())
        return 0

    # Wir sammeln Rule-Muster (NICHT die Nummern — die verschieben sich nach
    # jeder Loeschung). Dann loeschen wir per ``ufw delete allow <rule>`` —
    # das ist idempotent und unabhaengig von der laufenden Numerierung.
    targets: list[str] = []
    for line in status.stdout.splitlines():
        match = _RANGE_LINE_RE.search(line)
        if match:
            rule = match.group("rule")
            if rule not in targets:
                targets.append(rule)

    removed = 0
    for rule in targets:
        result = _run_ufw("delete", "allow", rule)
        if result.returncode == 0:
            removed += 1
            logger.info("Legacy-MSM-Range entfernt: %s", rule)
        else:
            logger.warning(
                "Legacy-MSM-Range %s konnte nicht entfernt werden: %s",
                rule, (result.stderr or result.stdout).strip(),
            )
    return removed
