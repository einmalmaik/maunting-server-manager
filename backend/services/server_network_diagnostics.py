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
import json
import logging
import socket
import struct
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from models import Server, ServerPort


logger = logging.getLogger(__name__)

# Typische Docker-Bridge-Netze. Eine Bind-IP darin ist fast immer ein
# Konfigurationsfehler: erreichbar ist sie nur aus anderen Containern.
_DOCKER_NETWORKS = (
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("172.17.0.0/16"),
)

_cached_public_ip: str | None = None
_cached_public_ip_time: datetime | None = None


def detect_public_ip(timeout: float = 1.5) -> str | None:
    """Ermittelt die öffentliche IPv4 des Hosts mit 15 Minuten Caching."""
    global _cached_public_ip, _cached_public_ip_time
    jetzt = datetime.now(timezone.utc)
    if _cached_public_ip and _cached_public_ip_time and (jetzt - _cached_public_ip_time) < timedelta(minutes=15):
        return _cached_public_ip

    endpoints = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://checkip.amazonaws.com",
    ]
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MSM-Diagnostics/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="ignore").strip()
                if raw:
                    ipaddress.IPv4Address(raw)
                    _cached_public_ip = raw
                    _cached_public_ip_time = jetzt
                    return raw
        except Exception:
            continue
    return None


def _probe_tcp_connect(host: str, port: int, timeout: float = 1.2) -> bool:
    """Prüft per einfachem TCP-Socket-Connect, ob der Port antwortet."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _read_null_string(data: bytes, offset: int) -> tuple[str, int]:
    """Liest einen Null-terminierten UTF-8/Latin-1-String aus einem Byte-Buffer."""
    end = data.find(b"\x00", offset)
    if end == -1:
        return data[offset:].decode("utf-8", errors="replace"), len(data)
    val = data[offset:end].decode("utf-8", errors="replace")
    return val, end + 1


def _probe_a2s_query(host: str, port: int, timeout: float = 1.5) -> dict[str, Any] | None:
    """Sendet ein Source/Steam-A2S_INFO-Query-Paket (ARK, ASA, CS2, DayZ, Palworld, Rust, etc.)."""
    query_pkt = b"\xFF\xFF\xFF\xFF\x54Source Engine Query\x00"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(query_pkt, (host, port))
        resp, _ = sock.recvfrom(4096)
        if not resp or len(resp) < 5:
            return None

        # Challenge Response (0x41) -> mit Challenge erneut senden
        if resp.startswith(b"\xFF\xFF\xFF\xFF\x41") and len(resp) >= 9:
            challenge = resp[5:9]
            sock.sendto(query_pkt + challenge, (host, port))
            resp, _ = sock.recvfrom(4096)
            if not resp or len(resp) < 5:
                return None

        # A2S_INFO Response (0x49)
        if resp.startswith(b"\xFF\xFF\xFF\xFF\x49"):
            offset = 5
            protocol = resp[offset] if len(resp) > offset else 0
            offset += 1
            name, offset = _read_null_string(resp, offset)
            map_name, offset = _read_null_string(resp, offset)
            folder, offset = _read_null_string(resp, offset)
            game, offset = _read_null_string(resp, offset)
            
            steam_id = 0
            if len(resp) >= offset + 2:
                steam_id = struct.unpack("<H", resp[offset:offset+2])[0]
                offset += 2
            
            players = resp[offset] if len(resp) > offset else 0
            offset += 1
            max_players = resp[offset] if len(resp) > offset else 0
            offset += 1
            bots = resp[offset] if len(resp) > offset else 0
            offset += 1
            server_type = chr(resp[offset]) if len(resp) > offset else "d"
            offset += 1
            environment = chr(resp[offset]) if len(resp) > offset else "l"
            offset += 1
            visibility = bool(resp[offset]) if len(resp) > offset else False
            offset += 1
            vac = bool(resp[offset]) if len(resp) > offset else False
            offset += 1

            version = ""
            if offset < len(resp):
                version, _ = _read_null_string(resp, offset)

            return {
                "protocol": "a2s",
                "responded": True,
                "server_name": name,
                "map": map_name,
                "folder": folder,
                "game": game,
                "steam_id": steam_id,
                "players": players,
                "max_players": max_players,
                "bots": bots,
                "server_type": server_type,
                "environment": environment,
                "password_protected": visibility,
                "vac_enabled": vac,
                "version": version,
            }
        return None
    except Exception as exc:
        logger.debug("A2S-Probe fehlgeschlagen host=%s port=%d exc=%s", host, port, exc)
        return None
    finally:
        sock.close()


def _probe_minecraft_ping(host: str, port: int, timeout: float = 1.5) -> dict[str, Any] | None:
    """Sendet einen Minecraft Server List Ping (SLP) Handshake + Request."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            # Handshake Packet: packet_id 0x00, protocol_version -1 (0xFF), host, port, next_state 1
            host_bytes = host.encode("utf-8")
            data = b"\x00\xff\x05" + struct.pack(">B", len(host_bytes)) + host_bytes + struct.pack(">H", port) + b"\x01"
            data_len = len(data)
            # Length prefix (VarInt) + Data
            s.sendall(struct.pack(">B", data_len) + data)
            # Status Request: length 1, packet_id 0x00
            s.sendall(b"\x01\x00")
            
            # Read response length
            raw = s.recv(4096)
            if not raw:
                return None
            
            # Find JSON payload start '{'
            idx = raw.find(b"{")
            if idx != -1:
                try:
                    payload = json.loads(raw[idx:].decode("utf-8", errors="ignore"))
                    players = payload.get("players") or {}
                    version = payload.get("version") or {}
                    desc = payload.get("description")
                    motd = desc if isinstance(desc, str) else (desc.get("text", "") if isinstance(desc, dict) else "")
                    return {
                        "protocol": "minecraft",
                        "responded": True,
                        "server_name": motd,
                        "version": version.get("name", ""),
                        "players": players.get("online", 0),
                        "max_players": players.get("max", 0),
                    }
                except Exception:
                    pass
            return {"protocol": "minecraft", "responded": True}
    except Exception:
        return None


def probe_game_query(host: str, port: int, protocol: str, *, game_type: str | None = None) -> dict[str, Any] | None:
    """Probt generic oder protokollspezifisch den Spielserver."""
    proto = str(protocol).lower()
    if proto == "udp":
        a2s = _probe_a2s_query(host, port)
        if a2s:
            return a2s
    elif proto == "tcp":
        if game_type == "minecraft" or port == 25565:
            mc = _probe_minecraft_ping(host, port)
            if mc:
                return mc
        if _probe_tcp_connect(host, port):
            return {"protocol": "tcp", "responded": True}
    return None


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

    # ── Game-Query-Probing (A2S, Minecraft SLP, TCP Connect) ──────────
    query_details: list[dict[str, Any]] = []
    local_probe_ip = "127.0.0.1" if bind_ip in {"0.0.0.0", "::", None} else bind_ip

    if server.status == "running" and verdict == "listening":
        for p in ports:
            port_num = p[0]
            protocol = p[1]
            role = p[2]
            probe_res = probe_game_query(
                local_probe_ip, port_num, protocol, game_type=getattr(server, "game_type", None)
            )
            if probe_res:
                query_details.append({
                    "port": port_num,
                    "protocol": protocol,
                    "role": role,
                    "query": probe_res,
                })

    public_ip = detect_public_ip()
    external_check = "unavailable"
    external_reason = (
        "MSM laeuft im selben Netz wie der Server und kann eine Verbindung "
        "von aussen nicht simulieren. Ob eine Portweiterleitung im Router "
        "existiert, laesst sich von hier nicht feststellen."
    )
    external_reachability: dict[str, Any] = {
        "status": "unavailable",
        "public_ip": public_ip,
        "note": external_reason,
    }

    if public_ip and server.status == "running" and verdict == "listening":
        # Öffentlichen WAN-Query-Probe versuchen
        wan_responsive = False
        wan_query_res = None
        for p in ports:
            port_num = p[0]
            protocol = p[1]
            q_res = probe_game_query(
                public_ip, port_num, protocol, game_type=getattr(server, "game_type", None)
            )
            if q_res:
                wan_responsive = True
                wan_query_res = q_res
                break

        if wan_responsive:
            external_check = "reachable"
            external_reason = f"Der Server antwortet auf öffentlicher IP {public_ip} auf Spielanfragen."
            external_reachability = {
                "status": "reachable",
                "public_ip": public_ip,
                "query": wan_query_res,
                "note": external_reason,
            }
        else:
            external_check = "unavailable"
            external_reason = (
                f"Lokal antwortet der Server, aber über die öffentliche IP {public_ip} "
                "wurde keine Antwort erhalten. Ob eine Portweiterleitung im Router "
                "existiert, laesst sich von hier nicht feststellen."
            )
            external_reachability = {
                "status": "unreachable_or_nat_restricted",
                "public_ip": public_ip,
                "note": external_reason,
            }

    return {
        "server_id": server.id,
        "status": server.status,
        "bind_ip": bind_ip,
        "ports": listening,
        "verdict": verdict,
        **({"probe_error": probe_error} if probe_error else {}),
        **({"game_queries": query_details} if query_details else {}),
        "external_check": external_check,
        "external_check_reason": external_reason,
        "external_reachability": external_reachability,
    }
