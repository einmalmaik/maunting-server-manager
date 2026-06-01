"""Zentraler SSE-Stream fuer Server-Konsolen.

Der Service kombiniert zwei Quellen:
- persistente MSM-Lifecycle-/Install-Logs aus ``backend/logs/<id>/console.log``
- Docker-Stdout/Stderr als Snapshot + Live-Follow

Console-Input wird hier bewusst nicht verarbeitet oder geloggt.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import Request

from services import docker_service


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sse_console_frame(
    *,
    line: str,
    source: str,
    event_id: int | None = None,
    timestamp: str | None = None,
) -> str:
    payload = json.dumps(
        {
            "line": line,
            "source": source,
            "timestamp": timestamp or _utc_iso(),
        },
        ensure_ascii=False,
    )
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}data: {payload}\n\n"


async def console_event_stream(
    request: Request,
    container: str,
    log_path: str,
    *,
    after_bytes: int | None = None,
    docker_tail_lines: int = 200,
) -> AsyncIterator[str]:
    """SSE-Generator fuer Console-Backlog + Docker-Live-Output."""

    initial_bytes = max(after_bytes or 0, 0)
    next_event_id = initial_bytes

    if os.path.exists(log_path):
        try:
            with open(log_path, "rb") as f:
                f.seek(initial_bytes)
                content_bytes = f.read()
            pos = initial_bytes
            for raw_line in content_bytes.splitlines(keepends=True):
                pos += len(raw_line)
                line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                next_event_id = max(next_event_id, pos)
                yield _sse_console_frame(line=line, source="msm", event_id=pos)
            initial_bytes = pos
        except OSError:
            initial_bytes = max(after_bytes or 0, 0)
            next_event_id = initial_bytes

    queue: asyncio.Queue[str] = asyncio.Queue()

    async def _tail_file() -> None:
        pos = initial_bytes
        while True:
            await asyncio.sleep(0.25)
            try:
                size = os.path.getsize(log_path)
            except OSError:
                continue
            if size < pos:
                pos = 0
            if size <= pos:
                continue
            try:
                with open(log_path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
                pos = size
            except OSError:
                continue
            read_pos = pos - len(chunk)
            for raw_line in chunk.splitlines(keepends=True):
                read_pos += len(raw_line)
                line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                await queue.put(_sse_console_frame(line=line, source="msm", event_id=read_pos))

    async def _tail_docker() -> None:
        nonlocal next_event_id
        if not docker_service.is_available():
            next_event_id += 1
            await queue.put(
                _sse_console_frame(
                    line="[MSM] Rootless Docker Daemon not running for user msm - Live-Container-Logs deaktiviert.",
                    source="msm",
                    event_id=next_event_id,
                )
            )
            return

        tail = docker_tail_lines
        while True:
            saw_line = False
            async for line in docker_service.stream_logs(container, tail=tail):
                saw_line = True
                next_event_id += 1
                await queue.put(_sse_console_frame(line=line, source="docker", event_id=next_event_id))
            if saw_line:
                tail = 0
            await asyncio.sleep(1.0)

    tasks = [
        asyncio.create_task(_tail_file()),
        asyncio.create_task(_tail_docker()),
    ]

    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield frame
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
