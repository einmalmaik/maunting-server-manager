"""Minecraft status query Guardian probe."""

from __future__ import annotations

import asyncio
import json
import struct
import time
from services.guardian_contract import ProbeConfig
from services.guardian_probes import ProbeResult, _result, _validated_ip

PROBE_TYPE = "minecraft-status"
MAX_PROTOCOL_PACKET_BYTES = 1_048_576


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


async def execute(config: ProbeConfig, _container_name: str) -> ProbeResult:
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
