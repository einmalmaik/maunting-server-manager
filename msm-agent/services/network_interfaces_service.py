"""Network-Interfaces Service — listet die Host-IPv4-Adressen auf dem Node.

Identisch zur Implementierung im Backend, um Konsistenz zu wahren.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class HostInterface:
    ip: str
    interface: str
    is_loopback: bool
    is_private: bool
    is_link_local: bool

    def to_dict(self) -> dict:
        return {
            "ip": self.ip,
            "interface": self.interface,
            "is_loopback": self.is_loopback,
            "is_private": self.is_private,
            "is_link_local": self.is_link_local,
        }


_LAN_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("100.64.0.0/10"),  # CGNAT
)


def _classify(ip: str) -> tuple[bool, bool, bool]:
    """(is_loopback, is_private, is_link_local) fuer eine IPv4-Adresse."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except (ValueError, ipaddress.AddressValueError):
        return False, False, False
    is_loopback = addr.is_loopback
    is_link_local = addr.is_link_local
    is_private = (
        not is_loopback
        and not is_link_local
        and any(addr in net for net in _LAN_NETWORKS)
    )
    return is_loopback, is_private, is_link_local


def list_host_interfaces() -> list[HostInterface]:
    """Liefert alle IPv4-Adressen des Hosts, sortiert nach Erreichbarkeit."""
    seen: set[str] = set()
    result: list[HostInterface] = []
    for iface_name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = addr.address
            if not ip or ip in seen:
                continue
            seen.add(ip)
            is_loopback, is_private, is_link_local = _classify(ip)
            result.append(
                HostInterface(
                    ip=ip,
                    interface=iface_name,
                    is_loopback=is_loopback,
                    is_private=is_private,
                    is_link_local=is_link_local,
                )
            )

    def _rank(h: HostInterface) -> int:
        if h.is_loopback:
            return 3
        if h.is_link_local:
            return 2
        if h.is_private:
            return 1
        return 0  # public

    return sorted(result, key=lambda h: (_rank(h), h.ip))


def default_bind_ip() -> str | None:
    """Default-Bind-IP fuer neue Server: erste echte Public-IP."""
    interfaces = list_host_interfaces()
    public = [h for h in interfaces if not h.is_loopback and not h.is_private and not h.is_link_local]
    if public:
        return public[0].ip
    private = [h for h in interfaces if h.is_private]
    if private:
        return private[0].ip
    return None
