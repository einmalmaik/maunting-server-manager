"""Port-Allocation Service — vergibt eindeutige Ports fuer Game-Server.

Phase 2 (Port-Manager):
- Pruef-Quellen: 1) DB (andere MSM-Server) 2) Host-System via ``ss`` 3) Bind-Probe.
- Beides — Auto- und manuelle Eingabe — laeuft durch den gleichen
  Real-World-Check (TCP fuer RCon, UDP fuer Game/Query).
- ``PortConflictError`` wird vom Router in HTTP 400 uebersetzt.

KISS: keine Pool-Klasse, keine globalen Zustaende — eine reine Funktion plus
eine klar benannte Exception.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import Server
from services.port_check_service import is_port_available
from services.port_role_service import normalize_port_protocol, port_role_base

logger = logging.getLogger(__name__)

# Default Port-Range fuer Game-Server.
# 27015-27999 = Steam-aehnliche Range, verhindert Kollisionen mit System-Ports.
PORT_RANGE_START = 27015
PORT_RANGE_END = 27999
BLOCK_SIZE = 5  # Pro Server 5 Ports (game, query, rcon + 2 Reserve)

# Mapping: welches Port-Feld nutzt welches Protokoll?
PORT_PROTOCOL = {
    "game_port": "udp",
    "query_port": "udp",
    "rcon_port": "tcp",
}


class PortConflictError(ValueError):
    """Wird geworfen, wenn ein Port bereits belegt ist (DB oder Host)."""


def _db_used_ports(db: Session, exclude_server_id: int | None = None) -> set[tuple[int, str]]:
    """Liefert alle Port/Protokoll-Paare, die andere MSM-Server halten."""
    from models.server_port import ServerPort
    query = db.query(ServerPort.port, ServerPort.protocol)
    if exclude_server_id is not None:
        query = query.filter(ServerPort.server_id != exclude_server_id)
    return {(int(port), normalize_port_protocol(protocol)) for port, protocol in query.all()}


def _assert_host_free(port: int, protocol: str, bind_ip: str) -> None:
    """Wirft ``PortConflictError``, wenn Host-System den Port belegt."""
    if not is_port_available(port, protocol, bind_ip):
        raise PortConflictError(
            f"Port {port}/{protocol} ist auf dem Host bereits belegt."
        )


def _result_from_allocated(
    port_requirements: list[tuple[str, str]],
    allocated_ports: dict[str, int],
) -> list[tuple[str, int, str]]:
    res = [
        (role, allocated_ports[role], next(p[1] for p in port_requirements if p[0] == role))
        for role, _ in port_requirements
    ]
    seen_pairs: set[tuple[int, str]] = set()
    for role, port, protocol in res:
        pair = (port, protocol)
        if pair in seen_pairs:
            raise PortConflictError(
                f"Port {port}/{protocol} ist innerhalb dieses Servers doppelt definiert ({role})."
            )
        seen_pairs.add(pair)
    return res


def allocate_ports(
    db: Session,
    requested_game_port: int | None = None,
    requested_query_port: int | None = None,
    requested_rcon_port: int | None = None,
    exclude_server_id: int | None = None,
    bind_ip: str = "0.0.0.0",
    *,
    port_requirements: list[tuple[str, str]] | None = None,
    requested_ports: dict[str, int | None] | None = None,
) -> list[tuple[str, int, str]] | tuple[int, int, int]:
    """Vergibt Ports fuer einen Server (dynamisch oder legacy).

    Wenn ``port_requirements`` uebergeben wird → gibt Liste von ``(role, port, protocol)`` zurueck.
    Sonst (Legacy-Verhalten) → gibt 3-Tuple ``(game_port, query_port, rcon_port)`` zurueck.
    """
    is_legacy = port_requirements is None
    if is_legacy:
        port_requirements = [
            ("game", "udp"),
            ("query", "udp"),
            ("rcon", "tcp"),
        ]
        requested_ports = {}
        if requested_game_port:
            requested_ports["game"] = requested_game_port
            requested_ports["query"] = requested_query_port or (requested_game_port + 1)
            requested_ports["rcon"] = requested_rcon_port or (requested_game_port + 2)
        else:
            if requested_query_port:
                requested_ports["query"] = requested_query_port
            if requested_rcon_port:
                requested_ports["rcon"] = requested_rcon_port

    port_requirements = [
        (role, normalize_port_protocol(proto))
        for role, proto in port_requirements
    ]
    db_used = _db_used_ports(db, exclude_server_id=exclude_server_id)

    # 1) Explizite Vorgaben validieren und setzen
    allocated_ports: dict[str, int] = {}
    if requested_ports:
        for role, req_port in requested_ports.items():
            if req_port is not None:
                proto = next((p[1] for p in port_requirements if p[0] == role), "udp")
                if not (1024 <= req_port <= 65535):
                    raise ValueError(
                        f"Port {req_port} ({role}) ausserhalb des gueltigen Bereichs (1024-65535)."
                    )
                if (req_port, proto) in db_used:
                    raise PortConflictError(
                        f"Port {req_port}/{proto} ({role}) ist bereits an einen anderen MSM-Server vergeben."
                    )
                _assert_host_free(req_port, proto, bind_ip)
                allocated_ports[role] = req_port

    # 2) Uebrige Ports automatisch vergeben
    remaining_reqs = [r for r in port_requirements if r[0] not in allocated_ports]
    for role, proto in list(remaining_reqs):
        base_role = port_role_base(role)
        shared_port = next(
            (
                allocated_port
                for allocated_role, allocated_port in allocated_ports.items()
                if port_role_base(allocated_role) == base_role
            ),
            None,
        )
        if shared_port is None:
            continue
        if (shared_port, proto) in db_used:
            raise PortConflictError(
                f"Port {shared_port}/{proto} ({role}) ist bereits an einen anderen MSM-Server vergeben."
            )
        _assert_host_free(shared_port, proto, bind_ip)
        allocated_ports[role] = shared_port
    remaining_reqs = [r for r in port_requirements if r[0] not in allocated_ports]
    if not remaining_reqs:
        res = _result_from_allocated(port_requirements, allocated_ports)
        if is_legacy:
            return (allocated_ports["game"], allocated_ports["query"], allocated_ports["rcon"])
        return res

    # Blockweise Vergabe: wir suchen eine freie Range
    N = len(port_requirements)
    block_len = max(BLOCK_SIZE, N)
    found_block = False
    temp_allocated = dict(allocated_ports)

    for base in range(PORT_RANGE_START, PORT_RANGE_END - block_len + 1, block_len):
        conflict = False
        temp_allocated = dict(allocated_ports)
        idx = 0
        for role, proto in remaining_reqs:
            base_role = port_role_base(role)
            shared_port = next(
                (
                    allocated_port
                    for allocated_role, allocated_port in temp_allocated.items()
                    if port_role_base(allocated_role) == base_role
                ),
                None,
            )
            if shared_port is not None:
                if (shared_port, proto) in db_used:
                    conflict = True
                    break
                if not is_port_available(shared_port, proto, bind_ip):
                    conflict = True
                    break
                temp_allocated[role] = shared_port
                continue
            while base + idx in temp_allocated.values():
                idx += 1
            cand_port = base + idx
            if (cand_port, proto) in db_used:
                conflict = True
                break
            if not is_port_available(cand_port, proto, bind_ip):
                conflict = True
                break
            temp_allocated[role] = cand_port
            idx += 1

        if not conflict:
            found_block = True
            break

    # Fallback bei starker Fragmentierung: suche einzelne freie Ports
    if not found_block:
        temp_allocated = dict(allocated_ports)
        for role, proto in remaining_reqs:
            base_role = port_role_base(role)
            shared_port = next(
                (
                    allocated_port
                    for allocated_role, allocated_port in temp_allocated.items()
                    if port_role_base(allocated_role) == base_role
                ),
                None,
            )
            if shared_port is not None:
                if (shared_port, proto) in db_used:
                    raise PortConflictError(
                        f"Port {shared_port}/{proto} ({role}) ist bereits an einen anderen MSM-Server vergeben."
                    )
                _assert_host_free(shared_port, proto, bind_ip)
                temp_allocated[role] = shared_port
                continue
            found_port = False
            for p in range(PORT_RANGE_START, PORT_RANGE_END + 1):
                if (p, proto) in db_used or p in temp_allocated.values():
                    continue
                if is_port_available(p, proto, bind_ip):
                    temp_allocated[role] = p
                    found_port = True
                    break
            if not found_port:
                raise RuntimeError(
                    f"Keine freien Ports in der Range {PORT_RANGE_START}-{PORT_RANGE_END} verfuegbar."
                )

    res = _result_from_allocated(port_requirements, temp_allocated)
    if is_legacy:
        return (temp_allocated["game"], temp_allocated["query"], temp_allocated["rcon"])
    return res
