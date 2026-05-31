"""Helpers fuer stabile Server-Port-Rollen.

Blueprints duerfen dieselbe fachliche Rolle mehrfach mit verschiedenen
Protokollen deklarieren, z. B. ``query`` einmal UDP und einmal TCP. Die DB-Rolle
muss trotzdem eindeutig bleiben, weil API-Payloads als ``role -> value`` Maps
arbeiten.
"""

from __future__ import annotations

from typing import Iterable, Protocol


STANDARD_PORT_ROLES = {"game", "query", "rcon", "voice", "web"}
VALID_PORT_PROTOCOLS = {"tcp", "udp"}


class BlueprintPortLike(Protocol):
    name: object
    protocol: object


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def normalize_port_protocol(protocol: str) -> str:
    value = protocol.strip().lower()
    if value not in VALID_PORT_PROTOCOLS:
        raise ValueError("Port-Protokoll muss tcp oder udp sein.")
    return value


def port_role_base(role: str) -> str:
    """Fachliche Basisrolle fuer gleiche-Port-Automatik und UI-Labels."""
    for base in STANDARD_PORT_ROLES:
        if role == base or role.startswith(f"{base}_"):
            return base
    return role


def blueprint_port_requirements(ports: Iterable[BlueprintPortLike]) -> list[tuple[str, str]]:
    """Mappt Blueprint-Ports auf eindeutige ``(role, protocol)``-Tupel.

    Erste Standardrolle bleibt kompatibel (``query``), weitere gleiche Rollen
    werden stabil nummeriert (``query_2``). ``custom`` behält das bestehende
    ``custom_N``-Schema.
    """
    counts: dict[str, int] = {}
    custom_idx = 1
    requirements: list[tuple[str, str]] = []

    for port in ports:
        name = _value(port.name)
        protocol = normalize_port_protocol(_value(port.protocol))
        if name == "custom":
            role = f"custom_{custom_idx}"
            custom_idx += 1
        else:
            count = counts.get(name, 0) + 1
            counts[name] = count
            role = name if count == 1 else f"{name}_{count}"
        requirements.append((role, protocol))

    return requirements
