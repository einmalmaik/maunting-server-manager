"""Firewall-Service — öffnet und schließt Ports via UFW.

Falls UFW nicht verfügbar ist, wird stillschweigend übersprungen
(der Admin kann Ports manuell öffnen).
"""

import subprocess


def _ufw_exists() -> bool:
    try:
        subprocess.run(["ufw", "status"], check=False, capture_output=True)
        return True
    except FileNotFoundError:
        return False


def open_ports(name: str, game_port: int, query_port: int | None = None, rcon_port: int | None = None) -> bool:
    """Öffnet UDP-Ports (und TCP für RCon) für einen Game-Server in UFW.

    Args:
        name: Server-Name (für den UFW-Kommentar)
        game_port: Haupt-Game-Port (UDP)
        query_port: Query-Port (UDP)
        rcon_port: RCon-Port (TCP)
    """
    if not _ufw_exists():
        return False

    try:
        subprocess.run(
            ["ufw", "allow", f"{game_port}/udp", "comment", f"MSM {name} game"],
            check=False, capture_output=True,
        )
        if query_port:
            subprocess.run(
                ["ufw", "allow", f"{query_port}/udp", "comment", f"MSM {name} query"],
                check=False, capture_output=True,
            )
        if rcon_port:
            subprocess.run(
                ["ufw", "allow", f"{rcon_port}/tcp", "comment", f"MSM {name} rcon"],
                check=False, capture_output=True,
            )
        return True
    except (FileNotFoundError, OSError):
        return False


def close_ports(game_port: int, query_port: int | None = None, rcon_port: int | None = None) -> bool:
    """Schließt Ports in UFW."""
    if not _ufw_exists():
        return False

    def _delete(port: int, proto: str) -> None:
        try:
            subprocess.run(
                ["ufw", "delete", "allow", f"{port}/{proto}"],
                check=False, capture_output=True,
            )
        except (FileNotFoundError, OSError):
            pass

    _delete(game_port, "udp")
    if query_port:
        _delete(query_port, "udp")
    if rcon_port:
        _delete(rcon_port, "tcp")
    return True
