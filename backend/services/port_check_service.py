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
import time

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


# Wie lange ein Listener-Schnappschuss wiederverwendet wird.
#
# Frueher fragte jeder einzelne Portcheck ``ss`` mit einem eigenen Prozess.
# Gemessen an einer Servererstellung waren das **1181 Prozessstarts fuer einen
# Server** — der Allokator laeuft einen Portbereich ab, und jeder Kandidat
# kostete ein eigenes ``ss``. Auf einem Windows-Entwicklungsrechner sind das
# ueber hundert Sekunden; auf einem Linux-Host ist es billiger, aber immer noch
# tausend Prozesse fuer eine einzige Anlage.
#
# ``ss`` listet alle Listener in *einem* Aufruf. Der Schnappschuss darf kurz
# altern, weil er ohnehin nur die Vorauswahl ist: verbindlich entscheidet
# ``_can_bind`` unmittelbar vor der Verwendung. Eine Sekunde ist lang genug,
# damit ein Allokationsdurchlauf mit einem Aufruf auskommt, und kurz genug,
# dass ein frisch gestarteter Fremddienst nicht uebersehen wird.
_LISTENER_SNAPSHOT_SECONDS = 1.0

# Protokoll -> (Zeitpunkt, lauschende Ports)
_listener_snapshots: dict[str, tuple[float, frozenset[int]]] = {}


def reset_port_cache_for_tests() -> None:
    """Verwirft die Schnappschuesse — sonst leckt einer in den naechsten Test."""
    _listener_snapshots.clear()


def _parse_listening_ports(output: str) -> frozenset[int]:
    """Liest die Portnummern aus der lokalen Adressspalte von ``ss``.

    Erwartetes Format (``-H``, also ohne Kopfzeile)::

        LISTEN 0 4096   0.0.0.0:22    0.0.0.0:*
        LISTEN 0 511          *:80          *:*
        LISTEN 0 128    [::1]:631       [::]:*

    Die vierte Spalte traegt Adresse und Port. Der Port steht hinter dem
    letzten ``:`` — das trennt auch IPv6-Adressen richtig, deren Adressteil
    selbst Doppelpunkte enthaelt.
    """
    ports: set[int] = set()
    for line in output.splitlines():
        felder = line.split()
        if len(felder) < 4:
            continue
        _, _, port_teil = felder[3].rpartition(":")
        try:
            ports.add(int(port_teil))
        except ValueError:
            # Ein Portname statt einer Zahl kann hier nicht stehen (``-n``
            # erzwingt numerisch); eine unerwartete Zeile wird uebergangen,
            # statt den ganzen Schnappschuss zu verwerfen.
            continue
    return frozenset(ports)


def _listening_ports(protocol: str) -> frozenset[int]:
    """Alle lauschenden Ports dieses Protokolls, aus einem einzigen ``ss``-Aufruf."""
    proto = _normalize_protocol(protocol)
    jetzt = time.monotonic()
    gemerkt = _listener_snapshots.get(proto)
    if gemerkt is not None and jetzt - gemerkt[0] < _LISTENER_SNAPSHOT_SECONDS:
        return gemerkt[1]

    # -H: keine Header  -l: nur Listener  -n: numerisch  -t/-u: TCP/UDP
    flag = "-Hltn" if proto == "tcp" else "-Hlun"
    try:
        result = subprocess.run(
            ["ss", flag],
            capture_output=True,
            text=True,
            timeout=5,
            env=_SYSTEM_ENV,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("ss-Aufruf fehlgeschlagen: %s", exc)
        # Auch der Fehlschlag wird gemerkt. Sonst startet ein System **ohne**
        # ``ss`` weiterhin fuer jeden Kandidaten einen Prozess, der sofort
        # scheitert — genau der teure Fall, den diese Aenderung beseitigt.
        ports = frozenset()
    else:
        ports = _parse_listening_ports(result.stdout) if result.returncode == 0 else frozenset()

    _listener_snapshots[proto] = (jetzt, ports)
    return ports


def _port_in_use_via_ss(port: int, protocol: str) -> bool:
    """Frage den Kernel via ``ss``, ob ein Listener auf ``port`` existiert.

    ``ss`` zeigt Listener anderer User/Container und ist damit autoritativer
    als ein reiner Bind-Versuch. Wenn ``ss`` nicht verfuegbar ist, fallen wir
    auf False zurueck — der spaetere Bind-Versuch faengt das ab.
    """
    return port in _listening_ports(protocol)


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
