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

# Cache für aktive Hintergrund-Logger
_ACTIVE_LOGGERS: dict[int, asyncio.Task] = {}


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


async def _log_collector(server_id: int, container_name: str) -> None:
    """Hintergrund-Logger, der den docker logs stream liest und in console.log schreibt."""
    logger.info(f"Hintergrund-Log-Collector gestartet fuer Server {server_id} ({container_name})")
    try:
        while True:
            # Prüfen, ob der Container überhaupt läuft/existiert
            if not docker_service.is_running(container_name):
                break
            
            try:
                # tail=0, da wir nur neue Logzeilen ab jetzt sichern wollen, um Duplikate zu vermeiden
                async for line in docker_service.stream_logs(container_name, tail=0):
                    _append_console_log(server_id, line + "\n")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Log-Stream fuer Server {server_id} unterbrochen: {e}")
            
            # Kurz warten vor dem nächsten Reconnect-Versuch, falls er noch läuft
            await asyncio.sleep(2.0)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Log-Collector fuer Server {server_id} gestoppt wegen Fehler: {e}")
    finally:
        _ACTIVE_LOGGERS.pop(server_id, None)
        logger.info(f"Hintergrund-Log-Collector beendet fuer Server {server_id} ({container_name})")


def ensure_console_logger(server_id: int, container_name: str) -> None:
    """Stellt sicher, dass der Hintergrund-Log-Collector fuer den Server aktiv ist."""
    if not docker_service.is_available() or not docker_service.exists(container_name):
        return
    if server_id in _ACTIVE_LOGGERS:
        # Falls der Task beendet wurde, aber noch im Dict ist
        if not _ACTIVE_LOGGERS[server_id].done():
            return
    
    # Task im Hintergrund starten
    task = asyncio.create_task(_log_collector(server_id, container_name))
    _ACTIVE_LOGGERS[server_id] = task


def stop_console_logger(server_id: int) -> None:
    """Stoppt den Hintergrund-Log-Collector fuer den Server."""
    task = _ACTIVE_LOGGERS.pop(server_id, None)
    if task:
        task.cancel()


async def console_event_stream(
    request: Request,
    container: str,
    log_path: str,
    *,
    after_bytes: int | None = None,
    docker_tail_lines: int = 200,
) -> AsyncIterator[str]:
    """SSE-Generator fuer Console-Backlog + live nachfolgende console.log-Datei."""

    initial_bytes = max(after_bytes or 0, 0)
    queue: asyncio.Queue[str] = asyncio.Queue()

    if os.path.exists(log_path):
        try:
            with open(log_path, "rb") as f:
                f.seek(initial_bytes)
                content_bytes = f.read()
            pos = initial_bytes
            for raw_line in content_bytes.splitlines(keepends=True):
                pos += len(raw_line)
                line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                yield _sse_console_frame(line=line, source="msm", event_id=pos)
            initial_bytes = pos
        except OSError:
            initial_bytes = max(after_bytes or 0, 0)

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

    task = asyncio.create_task(_tail_file())

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
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
