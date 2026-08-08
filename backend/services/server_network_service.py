"""Baut Firewall, iptables und Container nach einer Netzwerkaenderung neu auf.

Diese Schritte standen bisher inline in `PATCH /servers/{id}`. Solange es nur
einen Weg gab, eine Bind-IP zu aendern, war das in Ordnung. Mit dem
KI-Vorschlag `propose_bind_ip_update` gibt es einen zweiten — und ein zweiter
Weg, der die Firewall *fast* genauso behandelt, ist genau die Art von stiller
Abweichung, die Zielpunkt 10 des v4-Zielbilds ausschliesst: es darf nicht
mehrere Arten geben, dieselbe Sache zu tun.

Deshalb steht der Ablauf hier einmal, und beide Wege rufen ihn auf. Der Inhalt
ist unveraendert aus dem Router uebernommen — ein Ortswechsel, kein
Verhaltenswechsel.

**Warum nur bei laufendem Server.** Die Firewallregeln sind an den Lifecycle
gekoppelt: fuer einen gestoppten Server bleiben sie zu. Ein Neuaufbau waere
dort nicht nur ueberfluessig, er wuerde Ports oeffnen, hinter denen nichts
lauscht.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import Server


logger = logging.getLogger(__name__)


def recreate_server_network(
    server: Server,
    old_ports: list[tuple[int, str, str]],
    old_bind_ip: str | None,
) -> bool:
    """Ersetzt die Netzwerkregeln eines Servers durch seinen aktuellen Stand.

    Args:
        server: der bereits **committete** Server mit den neuen Werten.
        old_ports: Ports vor der Aenderung als ``(port, protocol, role)``.
        old_bind_ip: Bind-IP vor der Aenderung.

    Returns:
        True, wenn tatsaechlich neu aufgebaut wurde (Server lief).
    """
    from games import get_plugin
    from games.base import container_name_for
    from services import docker_service
    from services.docker_iptables_service import accept_server as iptables_accept_server
    from services.docker_iptables_service import revoke_server as iptables_revoke_server
    from services.firewall_service import close_ports, open_ports

    plugin = get_plugin(server.game_type)
    was_running = plugin is not None and docker_service.is_running(
        container_name_for(server.id), node=server.node
    )
    if not was_running:
        return False

    close_ports(old_ports, node=server.node, name=server.name)
    if server.node is None or server.node.is_local:
        iptables_revoke_server(server.name, old_bind_ip or "", old_ports)
    # Container stoppen — Plugin.start() legt ihn mit den neuen Ports und
    # Bind-Werten frisch an.
    plugin.stop(server)
    new_ports = [(p.port, p.protocol, p.role) for p in server.ports]
    open_ports(server.name, new_ports, node=server.node)
    if server.node is None or server.node.is_local:
        iptables_accept_server(server.name, server.public_bind_ip or "", new_ports)
    plugin.start(server)
    return True


class BindIpRejected(ValueError):
    """Die gewuenschte Bind-IP ist fuer diesen Server nicht verwendbar."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def assert_bind_ip_usable(db: Session, server: Server, bind_ip: str) -> None:
    """Prueft eine neue Bind-IP, bevor irgendetwas veraendert wird.

    Zwei Fragen, beide vor der Mutation: Gehoert die Adresse ueberhaupt diesem
    Host? Und sind die Ports dort frei? Ohne die erste wuerde ein Tippfehler
    einen laufenden Server in einen Zustand bringen, in dem der Container nicht
    mehr startet — die Adresse existiert nicht, also schlaegt der Bind fehl.
    """
    from services.port_check_service import is_port_available

    node = getattr(server, "node", None)
    if node is None or node.is_local:
        from services.network_interfaces_service import list_host_interfaces

        known = {interface.ip for interface in list_host_interfaces()}
    else:
        from services.node_client import NodeClient

        try:
            payload = NodeClient.from_node(node, timeout=10.0).interfaces()
        except Exception as exc:
            raise BindIpRejected(
                "node_unreachable",
                "Die Node ist nicht erreichbar; die Adresse kann nicht geprueft werden.",
            ) from exc
        entries = payload.get("interfaces") if isinstance(payload, dict) else None
        known = {
            str(item.get("ip"))
            for item in (entries or [])
            if isinstance(item, dict) and item.get("ip")
        }

    if bind_ip not in known:
        raise BindIpRejected(
            "unknown_address",
            "Diese Adresse gehoert nicht zu den Netzwerkschnittstellen dieses Hosts.",
        )

    # Die eigenen Ports zaehlen nicht als Konflikt: laeuft der Server gerade,
    # belegt er sie selbst. Geprueft wird deshalb nur gegen die *neue* Adresse
    # und nur, solange der Server steht.
    if server.status == "running":
        return
    for port_row in server.ports:
        protocol = str(port_row.protocol or "tcp").lower()
        try:
            free = is_port_available(port_row.port, protocol, bind_ip)
        except (ValueError, OSError) as exc:
            logger.info("Portpruefung fehlgeschlagen error=%s", type(exc).__name__)
            continue
        if not free:
            raise BindIpRejected(
                "port_conflict",
                f"Port {port_row.port}/{protocol} ist an dieser Adresse bereits belegt.",
            )
