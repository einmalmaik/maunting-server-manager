"""Node-level system metrics via psutil."""

from __future__ import annotations

from typing import Any

import psutil
from fastapi import APIRouter

from config import settings

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics() -> dict[str, Any]:
    """Return host CPU/RAM/disk and coarse network counters."""
    cpu_count = psutil.cpu_count(logical=True) or 0
    cpu_percent = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage(str(settings.servers_path()))
    except OSError:
        disk = psutil.disk_usage("/")

    net = psutil.net_io_counters()
    return {
        "cpu_count": cpu_count,
        "cpu_percent": cpu_percent,
        "ram_total_bytes": int(mem.total),
        "ram_used_bytes": int(mem.used),
        "ram_percent": float(mem.percent),
        "disk_total_bytes": int(disk.total),
        "disk_used_bytes": int(disk.used),
        "disk_percent": float(disk.percent),
        "network_bytes_sent": int(getattr(net, "bytes_sent", 0) or 0),
        "network_bytes_recv": int(getattr(net, "bytes_recv", 0) or 0),
    }
