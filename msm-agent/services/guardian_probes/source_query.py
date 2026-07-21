"""Source engine query protocol Guardian probe."""

from __future__ import annotations

import asyncio
import socket
import time
from services.guardian_contract import ProbeConfig
from services.guardian_probes import ProbeResult, _result, _validated_ip

PROBE_TYPE = "source-query"


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


def _source_query_sync(config: ProbeConfig) -> bool:
    query = b"\xff\xff\xff\xffTSource Engine Query\x00"
    response = _udp_exchange(
        config.target_host or "",
        int(config.target_port or 0),
        config.timeout_seconds,
        query,
    )
    if len(response) < 5 or response[:4] != b"\xff\xff\xff\xff":
        return False
    if response[4] == 0x41:
        if len(response) != 9:
            return False
        response = _udp_exchange(
            config.target_host or "",
            int(config.target_port or 0),
            config.timeout_seconds,
            query + response[5:9],
        )
    return len(response) >= 6 and response[:4] == b"\xff\xff\xff\xff" and response[4] == 0x49


async def execute(config: ProbeConfig, _container_name: str) -> ProbeResult:
    started = time.monotonic()
    try:
        healthy = await asyncio.wait_for(
            asyncio.to_thread(_source_query_sync, config),
            timeout=config.timeout_seconds * 2 + 0.5,
        )
    except (OSError, ValueError, asyncio.TimeoutError):
        healthy = False
    return _result(started, healthy, "source_query_ok" if healthy else "source_query_failed")
