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

    Versucht zuerst ``sudo -n ufw`` (für den msm-User mit sudoers-Regeln).
    Fällt bei Misserfolg (keine sudoers-Regel oder sudo nicht verfügbar)
    auf direkten ``ufw``-Aufruf zurück. Das erlaubt eine saubere Migration.

    ``check=False`` — Fehler werden nur geloggt, nicht propagiert.
    """
    # 1. Versuch mit sudo (non-interactive)
    sudo_result = subprocess.run(
        ["sudo", "-n", "ufw", *args],
        check=False,
        capture_output=True,
        text=True,
        env=_SYSTEM_ENV,
        timeout=10,
    )
    if sudo_result.returncode == 0:
        return sudo_result

    # 2. Fallback: direkter ufw-Aufruf (für Systeme ohne angepasste sudoers)
    direct_result = subprocess.run(
        ["ufw", *args],
        check=False,
        capture_output=True,
        text=True,
        env=_SYSTEM_ENV,
        timeout=10,
    )

    # Wenn sudo fehlgeschlagen ist, aber wir im Fallback Erfolg hatten,
    # loggen wir einen Hinweis (einmalig pro Prozesslauf wäre noch besser,
    # aber für KISS reicht es hier).
    if direct_result.returncode == 0 and sudo_result.returncode != 0:
        logger.info(
            "UFW-Befehl erfolgreich ohne sudo ausgeführt (sudoers-Regel fehlt oder sudo nicht verfügbar)."
        )

    return direct_result


def _allow(port: int, protocol: str, comment: str) -> bool:
    result = _run_ufw("allow", f"{port}/{protocol}", "comment", comment)
    if result.returncode != 0:
        logger.warning(
            "UFW allow %s/%s fehlgeschlagen: %s",
            port, protocol, (result.stderr or result.stdout).strip(),
        )
        return False
    return True


def _delete(port: int, protocol: str) -> bool:
    # ``ufw delete allow PORT/PROTO`` ist idempotent: nicht existierende Regeln
    # geben Exit 0 mit "Could not delete ..." aus.
    result = _run_ufw("delete", "allow", f"{port}/{protocol}")
    if result.returncode != 0:
        logger.debug(
            "UFW delete %s/%s ohne Treffer: %s",
            port, protocol, (result.stderr or result.stdout).strip(),
        )
    return True


def _comment_for(name: str, role: str) -> str:
    """Baut einen UFW-Kommentar — ``MSM <name> <role>`` (max 32 Zeichen)."""
    # UFW erlaubt bis zu 64 Zeichen Kommentare; wir bleiben konservativ.
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:24]
    return f"{MSM_COMMENT_PREFIX} {safe_name} {role}"


def _format_ports_summary(
    game_port: int | None | list[tuple[int, str, str]],
    query_port: int | None = None,
    rcon_port: int | None = None,
) -> str:
    if isinstance(game_port, list):
        items = [f"{p}/{proto}" for p, proto, _ in game_port if p]
        return ", ".join(items) if items else "keine"
    parts = []
    if game_port:
        parts.append(f"{game_port}/udp (game)")
    if query_port:
        parts.append(f"{query_port}/udp (query)")
    if rcon_port:
        parts.append(f"{rcon_port}/tcp (rcon)")
    return ", ".join(parts) if parts else "keine"


# ── Public API ───────────────────────────────────────────────────────────


def open_ports(
    name: str,
    game_port: int | None | list[tuple[int, str, str]],
    query_port: int | None = None,
    rcon_port: int | None = None,
    *,
    node=None,
    db=None,
    server_id: int | None = None,
    user_id: int | None = None,
    reason: str = "Server gestartet",
) -> bool:
    """Oeffnet die Spiel-Ports eines Servers in UFW und zeichnet AuditLog auf.

    Args:
        name: Server-Name (fliesst in den Kommentar).
        game_port: Haupt-Game-Port (UDP) ODER Liste von ``(port, protocol, role)``-Tupeln.
        query_port: optionaler Query-Port (UDP).
        rcon_port: optionaler RCon-Port (TCP).
        node: optionaler Remote-Node.
        db: optionaler DB-Session für Audit-Log.
        server_id: optionaler Server-ID für Audit-Log Target.
        user_id: optionaler User-ID für Audit-Log Actor.
        reason: Begründung für das AuditLog.

    Returns:
        ``True``, wenn UFW vorhanden ist und Calls abgesetzt wurden; sonst ``False``.
    """
    success = False
    if node is not None and not getattr(node, "is_local", True):
        if not isinstance(game_port, list):
            raise ValueError("Remote firewall requires normalized port list")
        from services.node_client import NodeClient

        try:
            NodeClient.from_node(node).firewall_update("open", name, game_port)
            success = True
        except Exception as exc:
            logger.warning("Remote firewall update 'open' failed (non-critical): %s", exc)
            success = False
    elif not _ufw_available():
        success = False
    elif isinstance(game_port, list):
        success = True
        for port, protocol, role in game_port:
            if port:
                _allow(port, protocol, _comment_for(name, role))
    else:
        success = True
        if game_port:
            _allow(game_port, "udp", _comment_for(name, "game"))
        if query_port:
            _allow(query_port, "udp", _comment_for(name, "query"))
        if rcon_port:
            _allow(rcon_port, "tcp", _comment_for(name, "rcon"))

    if success and db is not None and server_id is not None:
        try:
            from services.audit_service import record_privileged_action
            summary = _format_ports_summary(game_port, query_port, rcon_port)
            record_privileged_action(
                db,
                user_id=user_id,
                action="server.firewall_opened",
                target_type="server",
                target_id=server_id,
                details={
                    "server_name": name,
                    "ports": summary,
                    "reason": reason,
                },
                commit=True,
            )
        except Exception as exc:
            logger.warning("AuditLog fuer open_ports fehlgeschlagen: %s", exc)

    return success


def close_ports(
    game_port: int | None | list[tuple[int, str, str]],
    query_port: int | None = None,
    rcon_port: int | None = None,
    *,
    node=None,
    name: str = "server",
    db=None,
    server_id: int | None = None,
    user_id: int | None = None,
    reason: str = "Server gestoppt",
) -> bool:
    """Schliesst (idempotent) die UFW-Regeln eines Servers und protokolliert dies im AuditLog."""
    deleted_any = False
    if node is not None and not getattr(node, "is_local", True):
        if not isinstance(game_port, list):
            raise ValueError("Remote firewall requires normalized port list")
        from services.node_client import NodeClient

        try:
            NodeClient.from_node(node).firewall_update("close", name, game_port)
            deleted_any = True
        except Exception as exc:
            logger.warning("Remote firewall update 'close' failed (non-critical): %s", exc)
            deleted_any = False
    elif not _ufw_available():
        deleted_any = False
    elif isinstance(game_port, list):
        deleted_any = True
        for port, protocol, _ in game_port:
            if port:
                _delete(port, protocol)
    else:
        deleted_any = True
        if game_port:
            _delete(game_port, "udp")
        if query_port:
            _delete(query_port, "udp")
        if rcon_port:
            _delete(rcon_port, "tcp")

    if db is not None and server_id is not None and deleted_any:
        try:
            from services.audit_service import record_privileged_action
            summary = _format_ports_summary(game_port, query_port, rcon_port)
            record_privileged_action(
                db,
                user_id=user_id,
                action="server.firewall_closed",
                target_type="server",
                target_id=server_id,
                details={
                    "server_name": name,
                    "ports": summary,
                    "reason": reason,
                },
                commit=True,
            )
        except Exception as exc:
            logger.warning("AuditLog fuer close_ports fehlgeschlagen: %s", exc)

    return deleted_any


def reconcile_firewall_rules(db) -> int:
    """Audit & Reconciliation: Schließt verwaiste Firewall-Regeln gestoppter/gecrashtes Server.

    Prüft alle Server mit status != 'running' und ruft close_ports auf.
    Ein AuditLog wird geschrieben, falls eine offene Regel geschlossen wird.

    Returns:
        Anzahl der bereinigten gestoppten Server.
    """
    from models import Server
    from services.server_lifecycle_service import _ports

    try:
        non_running = db.query(Server).filter(Server.status != "running").all()
    except Exception as exc:
        logger.warning("Fehler beim Abfragen nicht-laufender Server fuer Firewall-Reconciliation: %s", exc)
        return 0

    reconciled = 0
    for srv in non_running:
        try:
            ports_list = _ports(srv)
            if close_ports(
                ports_list,
                node=srv.node,
                name=srv.name,
                db=db,
                server_id=srv.id,
                reason="Audit-Reconciliation: Veraltete Ports für gestoppten Server geschlossen",
            ):
                reconciled += 1
        except Exception as exc:
            logger.warning("Fehler bei Firewall-Reconciliation für Server %s: %s", srv.id, exc)

    return reconciled


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
