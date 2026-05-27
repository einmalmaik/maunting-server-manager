"""DOCKER-USER iptables Defense-in-Depth.

Docker fuegt eigene iptables-Regeln in der ``DOCKER``-Chain ein, die VOR UFW
greifen — d. h. ein versehentliches ``-p 27015:27015/udp`` an ``0.0.0.0`` ist
auf allen Host-Interfaces erreichbar, egal was UFW sagt. Phase 2 erzwingt
zwar explizite Bind-IPs (siehe ``port_publish``), aber wir setzen zusaetzlich
einen **Anti-Leak-Backstop** in der von Docker selbst dafuer vorgesehenen
``DOCKER-USER``-Chain.

Modell (KISS):

1. Beim Panel-Start einmalig: **Baseline-DROP** am Ende von DOCKER-USER fuer
   die MSM-Port-Range (UDP+TCP). Dies blockiert jedes versehentlich
   veroeffentlichte ``0.0.0.0:port`` Mapping in der MSM-Range.
2. Beim Server-Start: spezifische **ACCEPT-Regeln** am Anfang der Chain fuer
   ``(bind_ip, port, protocol)`` — die gewinnen gegen den Baseline-DROP, weil
   sie weiter oben stehen.
3. Beim Server-Stop / -Delete: die ACCEPT-Regeln werden 1:1 wieder entfernt
   (idempotent).

Wenn ``iptables`` nicht verfuegbar oder die DOCKER-USER-Chain nicht da ist,
schluckt der Service alle Aufrufe still — der Bind-IP-Zwang bleibt die
Primaerverteidigung.

iptables-Regeln persistieren NICHT ueber Reboots. Das ist OK: Baseline-DROP
wird im Lifespan beim Panel-Start re-installiert; Server-ACCEPT-Regeln werden
beim Server-Start neu angelegt. Wer den Boot-Zustand persistieren will, kann
``iptables-persistent`` getrennt installieren.
"""

from __future__ import annotations

import logging
import subprocess

from services.port_allocation_service import PORT_RANGE_END, PORT_RANGE_START

logger = logging.getLogger(__name__)

_SYSTEM_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
}

_DOCKER_USER_CHAIN = "DOCKER-USER"
_COMMENT_PREFIX = "MSM"
_BASELINE_COMMENT_UDP = f"{_COMMENT_PREFIX}:baseline:udp"
_BASELINE_COMMENT_TCP = f"{_COMMENT_PREFIX}:baseline:tcp"


def _run_iptables(*args: str, check_only: bool = False) -> subprocess.CompletedProcess | None:
    """Fuehre ``iptables <args>`` aus. Gibt None zurueck, wenn iptables fehlt.

    Versucht zuerst ``sudo -n iptables`` (für den msm-User mit sudoers-Regeln).
    Fällt bei Misserfolg auf direkten Aufruf zurück (saubere Migration).

    ``check_only=True`` deutet darauf hin, dass der Aufruf nur prueft (``-C``)
    — non-zero-Exit ist dann NICHT als Fehler zu werten.
    """
    try:
        # 1. Versuch mit sudo (non-interactive)
        sudo_result = subprocess.run(
            ["sudo", "-n", "iptables", *args],
            check=False,
            capture_output=True,
            text=True,
            env=_SYSTEM_ENV,
            timeout=10,
        )
        if sudo_result.returncode == 0:
            return sudo_result

        # 2. Fallback: direkter iptables-Aufruf
        result = subprocess.run(
            ["iptables", *args],
            check=False,
            capture_output=True,
            text=True,
            env=_SYSTEM_ENV,
            timeout=10,
        )

        if result.returncode == 0 and sudo_result.returncode != 0:
            logger.info(
                "iptables-Befehl erfolgreich ohne sudo ausgeführt (sudoers-Regel fehlt)."
            )

    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("iptables nicht ausfuehrbar (%s)", exc)
        return None

    if result is not None and result.returncode != 0 and not check_only:
        logger.warning(
            "iptables %s fehlgeschlagen: %s",
            " ".join(args), (result.stderr or result.stdout).strip(),
        )
    return result


def _iptables_available() -> bool:
    return _run_iptables("--version") is not None


def _chain_exists() -> bool:
    """True, wenn DOCKER-USER existiert (sonst ist Docker nicht installiert)."""
    result = _run_iptables("-L", _DOCKER_USER_CHAIN, "-n", check_only=True)
    if result is None:
        return False
    return result.returncode == 0


def _rule_exists(args: list[str]) -> bool:
    """True, wenn die exakte Rule (Args inkl. Comment) schon eingetragen ist."""
    result = _run_iptables("-C", *args, check_only=True)
    if result is None:
        return False
    return result.returncode == 0


# ── Public API ───────────────────────────────────────────────────────────


def ensure_baseline_drop(
    range_start: int = PORT_RANGE_START,
    range_end: int = PORT_RANGE_END,
) -> bool:
    """Stellt sicher, dass am ENDE von DOCKER-USER ein DROP fuer die
    MSM-Port-Range steht (UDP + TCP).

    Idempotent: existiert die Regel schon, passiert nichts.
    """
    if not _iptables_available() or not _chain_exists():
        logger.info("iptables/DOCKER-USER nicht verfuegbar — Defense-in-Depth uebersprungen.")
        return False

    for protocol, comment in (
        ("udp", _BASELINE_COMMENT_UDP),
        ("tcp", _BASELINE_COMMENT_TCP),
    ):
        rule = [
            _DOCKER_USER_CHAIN,
            "-p", protocol,
            "--dport", f"{range_start}:{range_end}",
            "-m", "comment", "--comment", comment,
            "-j", "DROP",
        ]
        if _rule_exists(rule):
            continue
        result = _run_iptables("-A", *rule)
        if result is None or result.returncode != 0:
            return False
    return True


def accept_server(
    name: str,
    bind_ip: str,
    game_port: int | None,
    query_port: int | None = None,
    rcon_port: int | None = None,
) -> bool:
    """Erlaubt eingehenden Traffic fuer einen Server ueber DOCKER-USER.

    Fuegt ACCEPT-Regeln am ANFANG der Chain ein (``-I 1``), sodass sie vor
    dem Baseline-DROP greifen. Idempotent.
    """
    if not _iptables_available() or not _chain_exists():
        return False
    return _apply_server_rules("insert", name, bind_ip, game_port, query_port, rcon_port)


def revoke_server(
    name: str,
    bind_ip: str,
    game_port: int | None,
    query_port: int | None = None,
    rcon_port: int | None = None,
) -> bool:
    """Entfernt die ACCEPT-Regeln eines Servers wieder. Idempotent."""
    if not _iptables_available() or not _chain_exists():
        return False
    return _apply_server_rules("delete", name, bind_ip, game_port, query_port, rcon_port)


# ── intern ───────────────────────────────────────────────────────────────


def _apply_server_rules(
    action: str,  # "insert" oder "delete"
    name: str,
    bind_ip: str,
    game_port: int | None,
    query_port: int | None,
    rcon_port: int | None,
) -> bool:
    """Insert oder Delete fuer alle drei Ports."""
    if not bind_ip:
        logger.warning(
            "accept_server/revoke_server fuer '%s' ohne bind_ip — uebersprungen.", name,
        )
        return False
    ok = True
    for port, protocol, role in (
        (game_port, "udp", "game"),
        (query_port, "udp", "query"),
        (rcon_port, "tcp", "rcon"),
    ):
        if not port:
            continue
        comment = f"{_COMMENT_PREFIX}:{_safe(name)}:{role}"
        rule = [
            _DOCKER_USER_CHAIN,
            "-d", bind_ip,
            "-p", protocol,
            "--dport", str(port),
            "-m", "comment", "--comment", comment,
            "-j", "ACCEPT",
        ]
        if action == "insert":
            # Skip wenn schon vorhanden (idempotent).
            if _rule_exists(rule):
                continue
            # ``-I CHAIN 1`` fuegt am Anfang ein.
            rule_for_insert = [_DOCKER_USER_CHAIN, "1", *rule[1:]]
            result = _run_iptables("-I", *rule_for_insert)
        else:  # delete
            # Idempotent: wenn nicht da, NICHT loggen.
            if not _rule_exists(rule):
                continue
            result = _run_iptables("-D", *rule)
        if result is None or result.returncode != 0:
            ok = False
    return ok


def _safe(name: str) -> str:
    """Reduziert den Namen auf iptables-kompatible Comment-Zeichen."""
    import re
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:24]
