"""Beantwortet: warum kommt niemand auf diesen Server?

Der Fall, um den es geht: Ein Server laeuft, aber niemand verbindet sich. Bisher
konnte das Panel dazu nichts sagen — es kannte die *vergebenen* Ports und den
Serverstatus, aber nicht, ob auf diesen Ports tatsaechlich etwas lauscht.

Der Messcode dafuer existiert laengst; er wurde nur nie in dieser Richtung
gefragt. `port_check_service.is_port_available` beantwortet "ist der Port
frei?", weil es vor einer Portvergabe genau darauf ankommt. Fuer einen
**laufenden** Server ist dieselbe Antwort die Diagnose in Umkehrung: meldet der
Port sich als frei, lauscht dort nichts. Genau derselbe Trick auf entfernten
Nodes ueber `NodeClient.ports_available`.

**Was hier bewusst nicht passiert.** Es gibt keine Aussage darueber, ob ein Port
aus dem Internet erreichbar ist. MSM steht hinter derselben NAT wie der Server;
eine Verbindung des Panels auf die eigene oeffentliche Adresse pruefte
Hairpin-NAT, nicht die Aussenwelt. Ein erfundenes "ist erreichbar" waere
schlimmer als keine Aussage — der Betreiber wuerde an der falschen Stelle
suchen. Stattdessen liefert die Diagnose die belegbaren Teilbefunde und benennt
die Luecke.
"""

from __future__ import annotations

import ipaddress
import logging

from sqlalchemy.orm import Session

from models import Server, ServerPort


logger = logging.getLogger(__name__)

# Typische Docker-Bridge-Netze. Eine Bind-IP darin ist fast immer ein
# Konfigurationsfehler: erreichbar ist sie nur aus anderen Containern.
_DOCKER_NETWORKS = (
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("172.17.0.0/16"),
)


def _classify_bind_ip(bind_ip: str | None) -> dict:
    """Ordnet die Bind-IP ein und benennt das Problem, falls es eins gibt."""
    if not bind_ip:
        return {
            "value": None,
            "kind": "unset",
            "reachable_from_outside_possible": True,
            "note": "Keine Bind-IP gesetzt; der Server lauscht auf allen Adressen.",
        }
    if bind_ip in {"0.0.0.0", "::"}:
        return {
            "value": bind_ip,
            "kind": "any",
            "reachable_from_outside_possible": True,
            "note": "Lauscht auf allen Adressen.",
        }
    try:
        address = ipaddress.IPv4Address(bind_ip)
    except (ValueError, ipaddress.AddressValueError):
        return {
            "value": bind_ip,
            "kind": "invalid",
            "reachable_from_outside_possible": False,
            "note": "Keine gueltige IPv4-Adresse.",
        }

    if address.is_loopback:
        return {
            "value": bind_ip, "kind": "loopback",
            "reachable_from_outside_possible": False,
            "note": "Loopback — nur vom Host selbst erreichbar, nie von aussen.",
        }
    if any(address in network for network in _DOCKER_NETWORKS):
        return {
            "value": bind_ip, "kind": "docker",
            "reachable_from_outside_possible": False,
            "note": "Sieht nach einer Docker-Bridge aus — nur aus anderen Containern erreichbar.",
        }
    if address.is_link_local:
        return {
            "value": bind_ip, "kind": "link_local",
            "reachable_from_outside_possible": False,
            "note": "Link-Local — nur im selben Netzsegment erreichbar.",
        }
    if address.is_private:
        return {
            "value": bind_ip, "kind": "private",
            "reachable_from_outside_possible": True,
            "note": (
                "Private Adresse. Aus dem Internet nur erreichbar, wenn im Router "
                "eine Portweiterleitung darauf zeigt."
            ),
        }
    return {
        "value": bind_ip, "kind": "public",
        "reachable_from_outside_possible": True,
        "note": "Oeffentliche Adresse.",
    }


def _host_interfaces(server: Server) -> tuple[list[dict], str | None]:
    """Interfaces des Hosts, auf dem der Server laeuft.

    Lokal ueber psutil, bei entfernter Node ueber den Agenten. Schlaegt der
    Agent fehl, wird das gemeldet statt eine leere Liste zu liefern — "keine
    Interfaces" waere eine falsche Aussage ueber den Host.
    """
    node = getattr(server, "node", None)
    if node is None or node.is_local:
        from services.network_interfaces_service import list_host_interfaces

        return [interface.to_dict() for interface in list_host_interfaces()], None

    from services.node_client import NodeClient

    try:
        payload = NodeClient.from_node(node, timeout=10.0).interfaces()
    except Exception as exc:
        logger.info("Node-Interfaces nicht abrufbar error=%s", type(exc).__name__)
        return [], "node_unreachable"
    interfaces = payload.get("interfaces") if isinstance(payload, dict) else None
    if not isinstance(interfaces, list):
        return [], "node_unreachable"
    return [item for item in interfaces if isinstance(item, dict)], None


def _server_ports(db: Session, server: Server) -> list[tuple[int, str, str]]:
    rows = (
        db.query(ServerPort)
        .filter(ServerPort.server_id == server.id)
        .order_by(ServerPort.role)
        .all()
    )
    return [(row.port, str(row.protocol or "tcp").lower(), row.role) for row in rows]


def describe_network(db: Session, server: Server, *, include_host_details: bool) -> dict:
    """Das Gesamtbild: Bind-IP, Ports, Interfaces, Firewall.

    ``include_host_details`` trennt zwei Fragen: *welche Ports hat mein Server*
    darf jeder sehen, der den Server sieht. *Welche Adressen hat der Host und
    was laesst die Firewall durch* ist die Netzstruktur des Betreibers und
    haengt an `server.network.manage`.
    """
    node = getattr(server, "node", None)
    ports = _server_ports(db, server)
    result: dict = {
        "server_id": server.id,
        "status": server.status,
        "bind_ip": _classify_bind_ip(server.public_bind_ip),
        "ports": [
            {"role": role, "port": port, "protocol": protocol}
            for port, protocol, role in ports
        ],
        "node": {
            "is_local": node is None or bool(node.is_local),
            "status": node.status if node is not None else "unassigned",
        },
    }
    if not include_host_details:
        result["host_details"] = "withheld"
        return result

    interfaces, interface_error = _host_interfaces(server)
    result["host_interfaces"] = interfaces
    if interface_error:
        result["host_interfaces_error"] = interface_error

    # Firewall nur fuer den lokalen Host: UFW laeuft dort, wo das Panel laeuft.
    # Fuer eine entfernte Node waere jede Aussage hier geraten.
    if node is None or node.is_local:
        from services.firewall_service import allowed_ports

        allowed = allowed_ports()
        if allowed is None:
            result["firewall"] = {
                "state": "unknown",
                "note": (
                    "Keine aktive UFW-Firewall gefunden. Das heisst nicht, dass "
                    "die Ports gesperrt sind — es heisst, dass MSM es nicht sagen kann."
                ),
            }
        else:
            result["firewall"] = {
                "state": "active",
                "ports": [
                    {
                        "port": port,
                        "protocol": protocol,
                        "allowed": (port, protocol) in allowed,
                    }
                    for port, protocol, _role in ports
                ],
            }
    else:
        result["firewall"] = {
            "state": "remote",
            "note": "Firewall einer entfernten Node wird von hier nicht ausgelesen.",
        }
    return result


def check_reachability(db: Session, server: Server) -> dict:
    """Lauscht auf den Ports tatsaechlich etwas?

    Die Kernaussage entsteht aus der Kombination: Ist der Server laut Panel
    ``running``, waehrend seine Ports frei sind, dann laeuft zwar der Container,
    aber der Dienst darin horcht nicht — oder er horcht auf einer anderen
    Adresse als der eingestellten Bind-IP.
    """
    ports = _server_ports(db, server)
    bind_ip = server.public_bind_ip or "0.0.0.0"
    node = getattr(server, "node", None)
    listening: list[dict] = []
    probe_error: str | None = None

    if node is None or node.is_local:
        from services.port_check_service import is_port_available

        for port, protocol, role in ports:
            try:
                free = is_port_available(port, protocol, bind_ip)
            except (ValueError, OSError) as exc:
                logger.info("Portpruefung fehlgeschlagen error=%s", type(exc).__name__)
                listening.append({"role": role, "port": port, "protocol": protocol,
                                  "listening": None})
                continue
            # Frei heisst: niemand lauscht. Genau die Umkehrung, auf die es
            # hier ankommt.
            listening.append({"role": role, "port": port, "protocol": protocol,
                              "listening": not free})
    elif not ports:
        pass
    else:
        from services.node_client import NodeClient

        try:
            payload = NodeClient.from_node(node, timeout=10.0).ports_available(
                [(port, protocol, role) for port, protocol, role in ports], bind_ip
            )
        except Exception as exc:
            logger.info("Node-Portpruefung fehlgeschlagen error=%s", type(exc).__name__)
            payload = None
            probe_error = "node_unreachable"
        if payload is None:
            listening = [
                {"role": role, "port": port, "protocol": protocol, "listening": None}
                for port, protocol, role in ports
            ]
        else:
            # Der Agent meldet Konflikte, also belegte Ports — und ein belegter
            # Port ist hier der Normalfall: dort lauscht der Server.
            conflicts = payload.get("conflicts") or []
            busy = {
                int(item.get("port"))
                for item in conflicts
                if isinstance(item, dict) and str(item.get("port", "")).isdigit()
            }
            all_free = bool(payload.get("available", False))
            listening = [
                {
                    "role": role, "port": port, "protocol": protocol,
                    "listening": (port in busy) if not all_free else False,
                }
                for port, protocol, role in ports
            ]

    silent = [item for item in listening if item["listening"] is False]
    verdict = "unknown"
    if listening and all(item["listening"] is None for item in listening):
        verdict = "not_measurable"
    elif server.status == "running" and silent:
        verdict = "running_but_not_listening"
    elif server.status != "running" and silent:
        verdict = "stopped_as_expected"
    elif listening and not silent:
        verdict = "listening"

    return {
        "server_id": server.id,
        "status": server.status,
        "bind_ip": bind_ip,
        "ports": listening,
        "verdict": verdict,
        **({"probe_error": probe_error} if probe_error else {}),
        # Ehrlichkeit statt Erfindung: siehe Modul-Docstring.
        "external_check": "unavailable",
        "external_check_reason": (
            "MSM laeuft im selben Netz wie der Server und kann eine Verbindung "
            "von aussen nicht simulieren. Ob eine Portweiterleitung im Router "
            "existiert, laesst sich von hier nicht feststellen."
        ),
    }
