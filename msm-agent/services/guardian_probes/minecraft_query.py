"""Minecraft query protocol Guardian probe."""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from services.guardian_contract import ProbeConfig
from services.guardian_probes import ProbeResult, _result, _validated_ip

PROBE_TYPE = "minecraft-query"


def _udp_exchange(host: str, port: int, timeout: float, payload: bytes, max_bytes: int = 65_535) -> bytes:
    address = _validated_ip(host)
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.connect((str(address), port))
        sock.send(payload)
        data = sock.recv(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("UDP response exceeds size limit")
        return data


def _minecraft_query_sync(config: ProbeConfig) -> bool:
    session = struct.unpack(">i", struct.pack(">I", int(time.monotonic_ns()) & 0x7FFFFFFF))[0]
    session_bytes = struct.pack(">i", session)
    response = _udp_exchange(
        config.target_host or "",
        int(config.target_port or 0),
        config.timeout_seconds,
        b"\xfe\xfd\x09" + session_bytes,
    )
    if len(response) < 7 or response[0] != 9 or response[1:5] != session_bytes:
        return False
    challenge_text = response[5:].split(b"\x00", 1)[0]
    if not challenge_text or len(challenge_text) > 16:
        return False
    challenge = int(challenge_text.decode("ascii"))
    stat_response = _udp_exchange(
        config.target_host or "",
        int(config.target_port or 0),
        config.timeout_seconds,
        b"\xfe\xfd\x00" + session_bytes + struct.pack(">i", challenge),
    )
    return len(stat_response) >= 6 and stat_response[0] == 0 and stat_response[1:5] == session_bytes


async def execute(config: ProbeConfig, _container_name: str) -> ProbeResult:
    started = time.monotonic()
    try:
        healthy = await asyncio.wait_for(
            asyncio.to_thread(_minecraft_query_sync, config),
            timeout=config.timeout_seconds * 2 + 0.5,
        )
    except (OSError, ValueError, UnicodeError, asyncio.TimeoutError):
        healthy = False
    return _result(started, healthy, "minecraft_query_ok" if healthy else "minecraft_query_failed")
