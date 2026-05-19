from __future__ import annotations

import socket
import struct


SERVERDATA_AUTH = 3
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RconError(RuntimeError):
    pass


def _packet(request_id: int, packet_type: int, body: str) -> bytes:
    payload = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _read_packet(sock: socket.socket) -> tuple[int, int, str]:
    raw_size = sock.recv(4)
    if len(raw_size) != 4:
        raise RconError("RCON connection closed before a response was received.")
    (size,) = struct.unpack("<i", raw_size)
    if size < 10 or size > 65535:
        raise RconError("RCON returned an invalid packet size.")
    payload = b""
    while len(payload) < size:
        chunk = sock.recv(size - len(payload))
        if not chunk:
            raise RconError("RCON connection closed during response read.")
        payload += chunk
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    body = payload[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, body


def send_rcon_command(host: str, port: int, password: str, command: str, *, timeout: float = 10.0) -> str:
    if not password:
        raise RconError("RCON password is not configured.")
    if not command.strip():
        raise RconError("RCON command is empty.")

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(_packet(1, SERVERDATA_AUTH, password))
        auth_id, _auth_type, _auth_body = _read_packet(sock)
        if auth_id == -1:
            raise RconError("RCON authentication failed.")

        sock.sendall(_packet(2, SERVERDATA_EXECCOMMAND, command))
        _response_id, _response_type, response = _read_packet(sock)
        if _response_type not in {SERVERDATA_RESPONSE_VALUE, SERVERDATA_EXECCOMMAND}:
            raise RconError("RCON returned an unexpected response.")
        return response
