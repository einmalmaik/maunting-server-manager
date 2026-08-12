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


def reassign_conflicting_ports(db, server: Server) -> list[dict]:
    """Vergibt die Ports neu, die auf dem Host jemand anderes belegt.

    Der Fall aus dem Betrieb: ein Server startet nicht mehr, weil ein anderer
    Dienst inzwischen auf seinem Port horcht. Guardian sieht nur "startet nicht";
    die Ursache steht im Log als "address already in use".

    **Nur fuer gestoppte Server.** Bei einem laufenden haelt sein eigener
    Container die Ports, jede Pruefung meldete sie als belegt, und die Funktion
    wuerde dem Server reihum neue Ports geben, die er gar nicht braucht.

    Der Ablauf besteht ausschliesslich aus vorhandenen Teilen: `allocate_ports`
    sucht freie Nummern, `ServerPort` haelt sie, `recreate_server_network` baut
    Firewall und iptables um. Ein zweiter Weg, der die Firewall *fast* genauso
    behandelt, waere genau die stille Abweichung, die dieses Modul vermeiden
    soll — deshalb steht die Funktion hier und nicht neben dem Aufrufer.

    Bewusst **enger** als der PATCH-Endpunkt: der kann beliebige Wunschports,
    Protokollwechsel und Bind-IP-Aenderungen. Hier gibt es genau eine Handlung —
    belegte Rolle bekommt eine freie Nummer. Was der Router mehr kann, bleibt
    beim Router.

    Returns:
        Je gewechselter Port ein ``{"role", "old", "new", "protocol"}``. Leere
        Liste heisst: es war kein Port belegt, es gab nichts zu tun.
    """
    from games.base import container_name_for
    from models.server_port import ServerPort
    from services import docker_service, port_check_service
    from services.port_allocation_service import PortConflictError, allocate_ports

    if docker_service.is_running(container_name_for(server.id), node=server.node):
        return []

    bind_ip = server.public_bind_ip or "0.0.0.0"
    node = getattr(server, "node", None)
    # Die Hostpruefung gilt nur fuer den eigenen Rechner. Auf einem entfernten
    # Node saehe das Panel seine eigenen freien Ports statt der des Nodes und
    # erklaerte jeden Port fuer frei — dieselbe Unterscheidung trifft der
    # PATCH-Endpunkt mit `check_host`.
    if node is not None and not getattr(node, "is_local", True):
        return []

    alt = [(p.role, p.port, p.protocol) for p in server.ports]
    belegt = {
        rolle
        for rolle, port, protokoll in alt
        if not port_check_service.is_port_available(port, protokoll, bind_ip)
    }
    if not belegt:
        return []

    # Belegte Rollen auf None — `allocate_ports` sucht genau fuer diese eine
    # freie Nummer und laesst die uebrigen unangetastet.
    wunsch = {rolle: (None if rolle in belegt else port) for rolle, port, _ in alt}
    anforderungen = [(rolle, protokoll) for rolle, _, protokoll in alt]
    try:
        vergeben = allocate_ports(
            db,
            exclude_server_id=server.id,
            bind_ip=bind_ip,
            port_requirements=anforderungen,
            requested_ports=wunsch,
            node_id=getattr(server, "node_id", None),
        )
    except (PortConflictError, ValueError, RuntimeError) as exc:
        raise PortReassignmentFailed(str(exc)) from exc

    alte_nach_rolle = {rolle: (port, protokoll) for rolle, port, protokoll in alt}
    db.query(ServerPort).filter(ServerPort.server_id == server.id).delete()
    gewechselt: list[dict] = []
    for rolle, port_val, protokoll in vergeben:
        db.add(ServerPort(server_id=server.id, role=rolle, port=port_val, protocol=protokoll))
        vorher = alte_nach_rolle.get(rolle, (None, protokoll))[0]
        if vorher != port_val:
            gewechselt.append(
                {"role": rolle, "old": vorher, "new": port_val, "protocol": protokoll}
            )
    db.commit()
    db.refresh(server)

    # Der Server war gestoppt, also baut `recreate_server_network` nichts neu
    # (es kehrt mit False zurueck). Die Firewallregeln der alten Ports muessen
    # trotzdem weg — sonst bleiben Loecher offen, die niemand mehr braucht.
    _alte_regeln_zuruecknehmen(server, [(p, prot, r) for r, p, prot in alt])
    return gewechselt


def _alte_regeln_zuruecknehmen(
    server: Server, alte_ports: list[tuple[int, str, str]]
) -> None:
    """Nimmt Firewall- und iptables-Regeln der alten Ports zurueck.

    Getrennt von `recreate_server_network`, weil der Server hier **nicht**
    laeuft: es gibt nichts zu stoppen und nichts zu starten, nur aufzuraeumen.
    Fehler sind hier kein Grund abzubrechen — die Ports sind in der Datenbank
    bereits umgeschrieben, und eine ueberzaehlige Regel ist ein kleineres
    Problem als ein halb umgestellter Server.
    """
    from services.docker_iptables_service import revoke_server as iptables_revoke_server
    from services.firewall_service import close_ports, open_ports

    try:
        close_ports(alte_ports, node=server.node, name=server.name)
        if server.node is None or server.node.is_local:
            iptables_revoke_server(server.name, server.public_bind_ip or "", alte_ports)
        neu = [(p.port, p.protocol, p.role) for p in server.ports]
        open_ports(server.name, neu, node=server.node)
    except Exception as exc:  # noqa: BLE001 - Aufraeumen darf nicht scheitern lassen
        logger.warning(
            "Firewall-Umstellung nach Portwechsel unvollstaendig (server_id=%s): %s",
            server.id, type(exc).__name__,
        )


class PortReassignmentFailed(RuntimeError):
    """Es gab keine freien Ports oder die Vergabe wurde abgelehnt."""


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
