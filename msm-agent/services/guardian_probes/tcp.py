"""TCP connection Guardian probe."""

from __future__ import annotations

import asyncio
import time
from services.guardian_contract import ProbeConfig
from services.guardian_probes import ProbeResult, _result, _validated_ip

PROBE_TYPE = "tcp"


async def execute(config: ProbeConfig, _container_name: str) -> ProbeResult:
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
