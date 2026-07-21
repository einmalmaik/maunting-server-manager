"""HTTP-ping Guardian probe."""

from __future__ import annotations

import asyncio
import httpx
import time
from services.guardian_contract import ProbeConfig
from services.guardian_probes import ProbeResult, _result, _validated_ip

PROBE_TYPE = "http-ping"


async def execute(config: ProbeConfig, _container_name: str) -> ProbeResult:
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
