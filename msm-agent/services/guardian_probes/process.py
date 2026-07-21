"""Process-level Guardian probe."""

from __future__ import annotations

import asyncio
from services import docker_service
from services.guardian_contract import ProbeConfig
from services.guardian_probes import ProbeResult, _result

PROBE_TYPE = "process"


async def execute(config: ProbeConfig, container_name: str) -> ProbeResult:
    import time
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
