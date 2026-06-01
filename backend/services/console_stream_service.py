import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import Request

from services import docker_service
from games.base import _append_console_log, _console_log_path

logger = logging.getLogger(__name__)




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
    """SSE-Generator fuer Console-Backlog + live nachfolgende Container-Logs.

    Liest zunaechst die Lifecycle-Logs aus der statischen `log_path`
    und startet dann parallele Tasks fuer Datei-Tailing und Docker-Log-Streaming.
    Behebt OOM-Gefahr beim ersten Auslesen der statischen Datei.
    """

    initial_bytes = max(after_bytes or 0, 0)
    queue: asyncio.Queue[str] = asyncio.Queue()

    # Statische MSM-Lifecycle-Logs iterativ einlesen (OOM-Fix)
    if os.path.exists(log_path):
        try:
            with open(log_path, "rb") as f:
                f.seek(initial_bytes)
                pos = initial_bytes
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    for raw_line in chunk.splitlines(keepends=True):
                        pos += len(raw_line)
                        line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                        yield _sse_console_frame(line=line, source="msm", event_id=pos)
                initial_bytes = pos
        except OSError:
            initial_bytes = max(after_bytes or 0, 0)

    # Task 1: Tail MSM-Lifecycle-Logs
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

    # Task 2: Live Docker Stream (stateless)
    async def _tail_docker() -> None:
        try:
            if not docker_service.is_running(container):
                return
            async for line in docker_service.stream_logs(container, tail=docker_tail_lines):
                await queue.put(_sse_console_frame(line=line, source="game"))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Docker tailing failed for %s: %s", container, e)

    file_task = asyncio.create_task(_tail_file())
    docker_task = asyncio.create_task(_tail_docker())

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
        file_task.cancel()
        docker_task.cancel()
        try:
            await file_task
            await docker_task
        except (asyncio.CancelledError, Exception):
            pass
