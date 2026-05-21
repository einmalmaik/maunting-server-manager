"""Port-Allocation Service — vergibt eindeutige Ports für Game-Server.

Jeder Server bekommt einen Port-Block (game, query, rcon).
Wenn der Nutzer Ports explizit angibt, werden diese validiert.
Sonst werden sie automatisch aus der Range vergeben.
"""

import socket

from sqlalchemy.orm import Session

from models import Server

# Default Port-Range für Game-Server
# 27015-27999 = Steam-ähnliche Range, verhindert Kollisionen mit System-Ports
PORT_RANGE_START = 27015
PORT_RANGE_END = 27999
BLOCK_SIZE = 5  # Pro Server 5 Ports (game, query, rcon + 2 Reserve)


def _is_port_free(port: int) -> bool:
    """Prüft ob ein TCP-Port auf dem lokalen System verfügbar ist."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _get_used_ports(db: Session, exclude_server_id: int | None = None) -> set[int]:
    """Liefert alle belegten Ports aus der Datenbank.

    Args:
        exclude_server_id: Diesen Server ausschließen (für Updates).
    """
    used: set[int] = set()
    query = db.query(Server)
    if exclude_server_id is not None:
        query = query.filter(Server.id != exclude_server_id)
    for srv in query.all():
        for field in (srv.game_port, srv.query_port, srv.rcon_port):
            if field:
                used.add(field)
    return used


def allocate_ports(
    db: Session,
    requested_game_port: int | None = None,
    requested_query_port: int | None = None,
    requested_rcon_port: int | None = None,
    exclude_server_id: int | None = None,
) -> tuple[int, int, int]:
    """Vergibt Ports für einen neuen Game-Server.

    Args:
        db: DB-Session
        requested_game_port: Wenn gesetzt, wird dieser Port genutzt (validiert)
        requested_query_port: Wenn gesetzt, wird dieser genutzt
        requested_rcon_port: Wenn gesetzt, wird dieser genutzt
        exclude_server_id: Diesen Server von der Konfliktprüfung ausschließen

    Returns:
        (game_port, query_port, rcon_port)

    Raises:
        ValueError: Wenn angeforderte Ports belegt sind oder außerhalb der Range.
        RuntimeError: Wenn keine freien Ports verfügbar.
    """
    used = _get_used_ports(db, exclude_server_id=exclude_server_id)

    # ── Explizite Port-Angabe ──
    if requested_game_port:
        game = requested_game_port
        query = requested_query_port or (game + 1)
        rcon = requested_rcon_port or (game + 2)

        for p in (game, query, rcon):
            if p in used:
                raise ValueError(f"Port {p} ist bereits belegt.")
            if not (1024 <= p <= 65535):
                raise ValueError(f"Port {p} außerhalb des gültigen Bereichs (1024-65535).")

        return game, query, rcon

    # ── Automatische Vergabe ──
    # Wir suchen den ersten freien Block in der Range
    for base in range(PORT_RANGE_START, PORT_RANGE_END - BLOCK_SIZE + 1, BLOCK_SIZE):
        block = {base + offset for offset in range(BLOCK_SIZE)}
        if block.isdisjoint(used):
            # Optionale zusätzliche Prüfung: ist der Port auf dem System wirklich frei?
            # Überspringen wir auf nicht-Linux-Systemen (z.B. Windows-Dev)
            try:
                if not _is_port_free(base):
                    continue
            except Exception:
                pass

            game = base
            query = base + 1
            rcon = base + 2
            return game, query, rcon

    raise RuntimeError(
        f"Keine freien Ports in der Range {PORT_RANGE_START}-{PORT_RANGE_END} verfügbar."
    )
