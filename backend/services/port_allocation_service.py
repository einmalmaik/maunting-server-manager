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


def _db_used_ports(db: Session, exclude_server_id: int | None = None) -> set[int]:
    """Liefert alle Ports, die andere MSM-Server bereits in der DB halten."""
    used: set[int] = set()
    query = db.query(Server)
    if exclude_server_id is not None:
        query = query.filter(Server.id != exclude_server_id)
    for srv in query.all():
        for field in (srv.game_port, srv.query_port, srv.rcon_port):
            if field:
                used.add(field)
    return used


def _assert_host_free(port: int, protocol: str, bind_ip: str) -> None:
    """Wirft ``PortConflictError``, wenn Host-System den Port belegt."""
    if not is_port_available(port, protocol, bind_ip):
        raise PortConflictError(
            f"Port {port}/{protocol} ist auf dem Host bereits belegt."
        )


def allocate_ports(
    db: Session,
    requested_game_port: int | None = None,
    requested_query_port: int | None = None,
    requested_rcon_port: int | None = None,
    exclude_server_id: int | None = None,
    bind_ip: str = "0.0.0.0",
) -> tuple[int, int, int]:
    """Vergibt drei Ports (game, query, rcon) fuer einen Server.

    Args:
        db: SQLAlchemy-Session.
        requested_game_port: Wenn gesetzt → strikt validieren; sonst Auto.
        requested_query_port: optionaler Override (gegen exclude_server_id).
        requested_rcon_port: optionaler Override.
        exclude_server_id: Diesen Server bei Konflikt-Pruefung ignorieren
            (notwendig fuer Updates).
        bind_ip: Host-IP fuer die Real-World-Bind-Probe. ``0.0.0.0`` deckt
            alle Interfaces ab — auch der Docker-Default-Publish-Bind.

    Returns:
        ``(game_port, query_port, rcon_port)``.

    Raises:
        PortConflictError: Port bereits in DB belegt oder vom Host gehalten.
        ValueError: Port ausserhalb des erlaubten Bereichs.
        RuntimeError: Kein freier Block in der Range verfuegbar.
    """
    db_used = _db_used_ports(db, exclude_server_id=exclude_server_id)

    # ── Explizite Port-Angabe ─────────────────────────────────────────────
    if requested_game_port:
        game = requested_game_port
        query = requested_query_port or (game + 1)
        rcon = requested_rcon_port or (game + 2)

        for port, field in ((game, "game_port"), (query, "query_port"), (rcon, "rcon_port")):
            if not (1024 <= port <= 65535):
                raise ValueError(
                    f"Port {port} ({field}) ausserhalb des gueltigen Bereichs (1024-65535)."
                )
            if port in db_used:
                raise PortConflictError(
                    f"Port {port} ({field}) ist bereits an einen anderen MSM-Server vergeben."
                )
            _assert_host_free(port, PORT_PROTOCOL[field], bind_ip)

        return game, query, rcon

    # ── Automatische Vergabe ──────────────────────────────────────────────
    for base in range(PORT_RANGE_START, PORT_RANGE_END - BLOCK_SIZE + 1, BLOCK_SIZE):
        block = (base, base + 1, base + 2)  # game, query, rcon
        if any(p in db_used for p in block):
            continue
        # Real-World-Check: alle drei Ports muessen frei sein (UDP fuer
        # game+query, TCP fuer rcon).
        try:
            for port, field in zip(block, ("game_port", "query_port", "rcon_port")):
                _assert_host_free(port, PORT_PROTOCOL[field], bind_ip)
        except PortConflictError:
            continue
        return block

    raise RuntimeError(
        f"Keine freien Ports in der Range {PORT_RANGE_START}-{PORT_RANGE_END} verfuegbar."
    )
