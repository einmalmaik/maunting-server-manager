"""Port-Check Service — prueft, ob ein Host-Port wirklich frei ist.

Phase 2 (Port-Manager): Wir verlassen uns NICHT mehr nur auf die DB. Vor jeder
Port-Zuweisung und vor jedem Container-Start muss real geprueft werden, ob ein
TCP- bzw. UDP-Port auf dem Host frei ist — damit Kollisionen mit Host-Diensten
(SSH, Caddy, fremde Container) ausgeschlossen sind.

KISS: zwei kleine, klar benannte Helfer, ein Pruef-Eintrittspunkt:

  - ``_port_in_use_via_ss(port, protocol)``  — autoritativ (sieht auch fremde
    Prozesse mit anderen UIDs), liest die Kernel-Socket-Tabelle via ``ss``.
  - ``_can_bind(port, protocol, bind_ip)``   — finale, atomare Probe direkt vor
    dem eigentlichen Verwenden.
  - ``is_port_available(port, protocol, bind_ip)`` — kombiniert beides.

Subprocess-Aufrufe nutzen einen fixen ``PATH`` und ``LC_ALL=C`` (gleiches
Muster wie ``docker_service``), kein Shell-Mode, keine User-Strings als
Kommando-Argumente.
"""

from __future__ import annotations

import logging
import socket
import subprocess

logger = logging.getLogger(__name__)

# Fester PATH und Locale — verhindert PATH-Hijacking und uebersetzte ss-Ausgaben.
_SYSTEM_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
}

_VALID_PROTOCOLS = ("tcp", "udp")


def _normalize_protocol(protocol: str) -> str:
    proto = protocol.lower().strip()
    if proto not in _VALID_PROTOCOLS:
        raise ValueError(f"Ungueltiges Protokoll: {protocol!r} (erlaubt: tcp, udp)")
    return proto


def _port_in_use_via_ss(port: int, protocol: str) -> bool:
    """Frage den Kernel via ``ss``, ob ein Listener auf ``port`` existiert.

    ``ss`` zeigt Listener anderer User/Container und ist damit autoritativer
    als ein reiner Bind-Versuch. Wenn ``ss`` nicht verfuegbar ist, fallen wir
    auf False zurueck — der spaetere Bind-Versuch faengt das ab.
    """
    proto = _normalize_protocol(protocol)
    # -H: keine Header  -l: nur Listener  -n: numerisch  -t/-u: TCP/UDP
    flag = "-Hltn" if proto == "tcp" else "-Hlun"
    try:
        result = subprocess.run(
            ["ss", flag, "sport", "=", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            env=_SYSTEM_ENV,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("ss-Aufruf fehlgeschlagen: %s", exc)
        return False
    if result.returncode != 0:
        return False
    # Jeder nicht-leere Output bedeutet: es gibt mindestens einen Listener.
    return any(line.strip() for line in result.stdout.splitlines())


def _can_bind(port: int, protocol: str, bind_ip: str) -> bool:
    """Versuche einen kurzen Bind — final-atomare Probe.

    KEIN ``SO_REUSEADDR`` und KEIN ``SO_REUSEPORT``: wir wollen genau das
    Verhalten reproduzieren, das Docker beim Veroeffentlichen erlebt.
    """
    proto = _normalize_protocol(protocol)
    sock_type = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    sock = socket.socket(socket.AF_INET, sock_type)
    try:
        sock.bind((bind_ip, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def is_port_available(port: int, protocol: str, bind_ip: str = "0.0.0.0") -> bool:
    """True, wenn der Port fuer ``protocol`` an ``bind_ip`` frei ist.

    Kombiniert ``ss``-Check (sieht fremde Prozesse) und Bind-Probe (final,
    atomar). Beide muessen sagen "frei".

    Args:
        port: TCP/UDP-Port (1..65535)
        protocol: ``"tcp"`` oder ``"udp"``
        bind_ip: Host-IP fuer die Bind-Probe. Default ``0.0.0.0`` deckt alle
            Interfaces ab — auch das, was Docker beim Default-Publish nutzt.
    """
    if not (1 <= port <= 65535):
        raise ValueError(f"Port {port} ausserhalb des gueltigen Bereichs (1-65535).")
    _normalize_protocol(protocol)  # fail-fast bei Tippfehlern
    if _port_in_use_via_ss(port, protocol):
        return False
    return _can_bind(port, protocol, bind_ip)
