"""WebSocket console streaming for container logs + stdin."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services import docker_service
from services.console_log_service import read_declared_log_updates
from services.docker_service import ContainerNameError, DockerUnavailableError
from services.agent_operation_coordinator import server_id_from_container_name
from services.guardian_service import console_log_config, redact_log_text
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["console"])


@router.websocket("/console/{container_name}/ws")
async def console_ws(websocket: WebSocket, container_name: str) -> None:
    """Stream docker logs to the client; forward text frames to container stdin.

    Auth is enforced by the HTTP middleware on the upgrade request
    (Authorization: Bearer <token>). /health is the only unauthenticated path.
    """
    try:
        docker_service.assert_msm_container_name(container_name)
    except ContainerNameError:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()
    stop = asyncio.Event()
    try:
        server_id = server_id_from_container_name(
            container_name, settings.container_name_prefix
        )
        log_config = console_log_config(server_id)
    except Exception:
        logger.warning("console log configuration unavailable")
        log_config = None

    def _producer() -> None:
        try:
            for line in docker_service.stream_logs_sync(
                container_name, tail=200, stop_event=stop
            ):
                if stop.is_set():
                    break
                redactors = log_config.redact if log_config is not None else []
                asyncio.run_coroutine_threadsafe(
                    queue.put(redact_log_text(line, redactors)), loop
                )
        except (DockerUnavailableError, FileNotFoundError, ContainerNameError):
            pass
        except Exception:
            logger.warning("console log producer failed")

    def _file_producer() -> None:
        try:
            config = log_config
            if config is None or not any(source != "stdout" for source in config.sources):
                return
            root = docker_service.managed_bind_root(container_name)
            positions: dict[str, int] = {}
            while not stop.is_set():
                for line in read_declared_log_updates(
                    root=root,
                    sources=config.sources,
                    redactors=config.redact,
                    max_tail_bytes=config.max_tail_bytes,
                    positions=positions,
                ):
                    envelope = json.dumps(
                        {"msm_console_source": "file", "text": line},
                        ensure_ascii=False,
                    )
                    asyncio.run_coroutine_threadsafe(queue.put(envelope), loop)
                time.sleep(0.2)
        except (DockerUnavailableError, OSError):
            pass
        except Exception:
            logger.warning("console file log producer failed")

    producer_future = loop.run_in_executor(None, _producer)
    file_producer_future = loop.run_in_executor(None, _file_producer)

    async def _send_logs() -> None:
        while True:
            line = await queue.get()
            try:
                await websocket.send_text(line)
            except Exception:
                break

    send_task = asyncio.create_task(_send_logs())

    try:
        while True:
            message: Any = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None:
                continue
            # Do not log stdin content (may contain secrets / RCON passwords)
            result = await loop.run_in_executor(
                None, docker_service.send_stdin, container_name, text
            )
            if not result.get("ok"):
                try:
                    await websocket.send_text(
                        f"[msm-agent] stdin failed: {result.get('error', 'unknown')}"
                    )
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass
        try:
            await producer_future
        except Exception:
            pass
        try:
            await file_producer_future
        except Exception:
            pass
