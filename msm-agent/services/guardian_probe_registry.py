"""Bounded, protocol-correct Guardian health probes."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import struct
import time
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

import httpx

from services import docker_service
from services.guardian_contract import PROBE_TYPES, ProbeConfig


MAX_PROTOCOL_PACKET_BYTES = 1_048_576


@dataclass(frozen=True)
class ProbeResult:
    healthy: bool
    code: str
    duration_ms: int
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class UnsupportedProbeError(ValueError):
    pass


def _result(started: float, healthy: bool, code: str, **evidence: Any) -> ProbeResult:
    return ProbeResult(
        healthy=healthy,
        code=code,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        evidence=evidence,
    )


def _validated_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("probe target_host must be a concrete IP address") from exc
    if address.is_unspecified or address.is_link_local or address.is_multicast or address.is_reserved:
        raise ValueError("probe target_host is not allowed")
    return address


async def _process_probe(config: ProbeConfig, container_name: str) -> ProbeResult:
    started = time.monotonic()
    state = await asyncio.to_thread(docker_service.inspect_container_state, container_name)
    running = bool(state and state.get("running"))
    return _result(
        started,
        running,
        "process_running" if running else "process_not_running",
        container_state=(state or {}).get("status", "missing"),
        oom_killed=bool((state or {}).get("oom_killed")),
    )


async def _tcp_probe(config: ProbeConfig, _container_name: str) -> ProbeResult:
    started = time.monotonic()
    _validated_ip(config.target_host or "")
    writer: asyncio.StreamWriter | None = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(config.target_host, config.target_port),
            timeout=config.timeout_seconds,
        )
        return _result(started, True, "tcp_connected")
    except (OSError, asyncio.TimeoutError):
        return _result(started, False, "tcp_connect_failed")
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def _udp_mapping_probe(config: ProbeConfig, container_name: str) -> ProbeResult:
    started = time.monotonic()
    state = await asyncio.to_thread(docker_service.inspect_container_state, container_name)
    expected = int(config.target_port or 0)
    mapped = False
    matched_container_port: str | None = None
    for container_port, bindings in ((state or {}).get("port_bindings") or {}).items():
        if not str(container_port).lower().endswith("/udp"):
            continue
        for binding in bindings or []:
            if int(binding.get("host_port") or 0) == expected:
                mapped = True
                matched_container_port = str(container_port)
                break
        if mapped:
            break
    return _result(
        started,
        mapped,
        "udp_mapping_present" if mapped else "udp_mapping_missing",
        host_port=expected,
        container_port=matched_container_port,
    )


async def _http_probe(config: ProbeConfig, _container_name: str) -> ProbeResult:
    started = time.monotonic()
    address = _validated_ip(config.target_host or "")
    host = f"[{address}]" if address.version == 6 else str(address)
    url = f"http://{host}:{config.target_port}{config.path}"
    timeout = httpx.Timeout(config.timeout_seconds)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream("GET", url) as response:
                if 300 <= response.status_code < 400:
                    return _result(started, False, "http_redirect_rejected", status=response.status_code)
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > config.max_response_bytes:
                        return _result(started, False, "http_response_too_large", status=response.status_code)
                healthy = response.status_code in config.expected_statuses
                return _result(
                    started,
                    healthy,
                    "http_expected_status" if healthy else "http_unexpected_status",
                    status=response.status_code,
                    response_bytes=total,
                )
    except (httpx.HTTPError, OSError, asyncio.TimeoutError):
        return _result(started, False, "http_request_failed")


def _encode_varint(value: int) -> bytes:
    result = bytearray()
    unsigned = value & 0xFFFFFFFF
    while True:
        current = unsigned & 0x7F
        unsigned >>= 7
        result.append(current | (0x80 if unsigned else 0))
        if not unsigned:
            return bytes(result)


async def _read_varint(reader: asyncio.StreamReader) -> int:
    value = 0
    for index in range(5):
        byte = (await reader.readexactly(1))[0]
        value |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return value
    raise ValueError("Minecraft VarInt exceeds five bytes")


async def _minecraft_status_probe(config: ProbeConfig, _container_name: str) -> ProbeResult:
    started = time.monotonic()
    _validated_ip(config.target_host or "")
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(config.target_host, config.target_port),
            timeout=config.timeout_seconds,
        )
        host_bytes = (config.target_host or "").encode("utf-8")
        handshake = (
            _encode_varint(0)
            + _encode_varint(47)
            + _encode_varint(len(host_bytes))
            + host_bytes
            + struct.pack(">H", int(config.target_port or 0))
            + _encode_varint(1)
        )
        writer.write(_encode_varint(len(handshake)) + handshake + b"\x01\x00")
        await writer.drain()

        async def read_response() -> dict[str, Any]:
            packet_length = await _read_varint(reader)
            if packet_length < 2 or packet_length > MAX_PROTOCOL_PACKET_BYTES:
                raise ValueError("Minecraft status packet length is invalid")
            packet_id = await _read_varint(reader)
            if packet_id != 0:
                raise ValueError("Minecraft status packet ID is invalid")
            json_length = await _read_varint(reader)
            if json_length < 2 or json_length > packet_length or json_length > MAX_PROTOCOL_PACKET_BYTES:
                raise ValueError("Minecraft status JSON length is invalid")
            raw = await reader.readexactly(json_length)
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("version"), dict):
                raise ValueError("Minecraft status response is malformed")
            return parsed

        response = await asyncio.wait_for(read_response(), timeout=config.timeout_seconds)
        return _result(
            started,
            True,
            "minecraft_status_ok",
            protocol=(response.get("version") or {}).get("protocol"),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        return _result(started, False, "minecraft_status_failed")
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


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


async def _minecraft_query_probe(config: ProbeConfig, _container_name: str) -> ProbeResult:
    started = time.monotonic()
    try:
        healthy = await asyncio.wait_for(
            asyncio.to_thread(_minecraft_query_sync, config),
            timeout=config.timeout_seconds * 2 + 0.5,
        )
    except (OSError, ValueError, UnicodeError, asyncio.TimeoutError):
        healthy = False
    return _result(started, healthy, "minecraft_query_ok" if healthy else "minecraft_query_failed")


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


async def _source_query_probe(config: ProbeConfig, _container_name: str) -> ProbeResult:
    started = time.monotonic()
    try:
        healthy = await asyncio.wait_for(
            asyncio.to_thread(_source_query_sync, config),
            timeout=config.timeout_seconds * 2 + 0.5,
        )
    except (OSError, ValueError, asyncio.TimeoutError):
        healthy = False
    return _result(started, healthy, "source_query_ok" if healthy else "source_query_failed")


ProbeFunction = Callable[[ProbeConfig, str], Awaitable[ProbeResult]]
PROBE_REGISTRY: dict[str, ProbeFunction] = {
    "process": _process_probe,
    "tcp": _tcp_probe,
    "udp_port_mapping": _udp_mapping_probe,
    "http-ping": _http_probe,
    "minecraft-status": _minecraft_status_probe,
    "minecraft-query": _minecraft_query_probe,
    "source-query": _source_query_probe,
}


async def execute_probe(config: ProbeConfig | dict[str, Any], container_name: str) -> ProbeResult:
    parsed = config if isinstance(config, ProbeConfig) else ProbeConfig.model_validate(config)
    function = PROBE_REGISTRY.get(parsed.type)
    if function is None or parsed.type not in PROBE_TYPES:
        raise UnsupportedProbeError(f"unsupported Guardian probe: {parsed.type}")
    return await function(parsed, container_name)

