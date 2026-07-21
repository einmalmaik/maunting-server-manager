"""UDP port mapping Guardian probe."""

from __future__ import annotations

import asyncio
import time
from services import docker_service
from services.guardian_contract import ProbeConfig
from services.guardian_probes import ProbeResult, _result

PROBE_TYPE = "udp_port_mapping"


async def execute(config: ProbeConfig, container_name: str) -> ProbeResult:
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
