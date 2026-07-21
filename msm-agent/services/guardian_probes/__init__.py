"""Dynamic, file-based Guardian probe registry with runtime hot-reloading."""

from __future__ import annotations

import importlib
import ipaddress
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from services.guardian_contract import ProbeConfig

logger = logging.getLogger("msm-agent.guardian.probes")


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


def discover_probes() -> dict[str, Any]:
    """Scan the guardian_probes directory, import/reload all *.py files, and return available probes."""
    probes_dir = Path(__file__).parent
    registry = {}
    
    for item in probes_dir.iterdir():
        if item.is_file() and item.name.endswith(".py") and item.name != "__init__.py":
            module_name = item.stem
            full_module_name = f"services.guardian_probes.{module_name}"
            try:
                if full_module_name in sys.modules:
                    module = importlib.reload(sys.modules[full_module_name])
                else:
                    module = importlib.import_module(full_module_name)
                
                probe_type = getattr(module, "PROBE_TYPE", None)
                if probe_type:
                    registry[probe_type] = module
            except Exception as exc:
                logger.warning("Failed to load/reload probe driver %s: %s", module_name, exc)
                
    return registry


def get_supported_probe_types() -> list[str]:
    return sorted(discover_probes().keys())


async def execute_probe(config: ProbeConfig | dict[str, Any], container_name: str) -> ProbeResult:
    started = time.monotonic()
    parsed = config if isinstance(config, ProbeConfig) else ProbeConfig.model_validate(config)
    registry = discover_probes()
    module = registry.get(parsed.type)
    if module is None:
        raise UnsupportedProbeError(f"unsupported Guardian probe: {parsed.type}")
    
    execute_fn = getattr(module, "execute", None)
    if execute_fn is None:
        raise UnsupportedProbeError(f"probe driver {parsed.type} does not implement execute()")
        
    try:
        return await execute_fn(parsed, container_name)
    except Exception as exc:
        logger.error("Driver execution failed for probe type %s: %s", parsed.type, exc, exc_info=True)
        return _result(
            started,
            False,
            "driver_execution_error",
            error_class=type(exc).__name__,
            error_message=str(exc)
        )
