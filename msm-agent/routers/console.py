"""WebSocket console streaming for container logs + stdin."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services import docker_service
from services.docker_service import ContainerNameError, DockerUnavailableError

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
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    stop = asyncio.Event()

    def _producer() -> None:
        try:
            for line in docker_service.stream_logs_sync(container_name, tail=200):
                if stop.is_set():
                    break
                asyncio.run_coroutine_threadsafe(queue.put(line), loop)
        except (DockerUnavailableError, FileNotFoundError, ContainerNameError):
            pass
        except Exception:
            logger.warning("console log producer failed")
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    producer_future = loop.run_in_executor(None, _producer)

    async def _send_logs() -> None:
        while True:
            line = await queue.get()
            if line is None:
                break
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
