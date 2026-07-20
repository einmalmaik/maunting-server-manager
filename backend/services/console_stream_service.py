"""WebSocket-Service fuer Live-Server-Konsole.

Stellt den einzigen Stream-Pfad fuer die MSM-Server-Konsole bereit: ein
bidirektionaler WebSocket-Endpoint mit In-Memory-Ring-Buffer pro Server.
Reconnects nutzen ``?last_id=`` um nur verpasste Zeilen nachzuliefern.

KISS:
- Eine In-Memory-State-Klasse, eine ``connect()``-Coroutine.
- File-Tail- und Docker-Stream-Loops sind hier unabhaengig vom ehemaligen
  SSE-Service implementiert (kein gemeinsamer Code). Geteilt wird nur
  ``docker_service`` fuer ``stream_logs`` / ``is_running`` ÔÇö alles andere
  hier ist WS-spezifisch.
- Keine externen State-Stores (Redis/DB). Verloren beim Restart = akzeptabel,
  da der File-Backlog dann ohnehin wieder eingelesen wird.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque  # noqa: F401 ÔÇö Any used by node proxy path

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from services import docker_service
from games.base import _console_log_path

logger = logging.getLogger(__name__)

# Groesse des In-Memory-Ring-Buffers pro Server.
RING_BUFFER_SIZE = 1000

# Maximale Anzahl gleichzeitiger WS-Verbindungen pro Server. Verhindert
# Memory-DoS durch hunderte Tabs.
MAX_CONCURRENT_WS_PER_SERVER = 5


@dataclass
class _Line:
    """Eine einzelne Konsolen-Zeile mit monotoner ID."""

    id: int
    text: str
    source: str  # "msm" | "docker"
    timestamp: str


@dataclass
class _ServerState:
    """Pro-Server State: Ring-Buffer + Counter + Connection-Count."""

    lines: Deque[_Line] = field(default_factory=lambda: deque(maxlen=RING_BUFFER_SIZE))
    next_id: int = 1
    active_connections: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    websockets: set[WebSocket] = field(default_factory=set)
    tasks: list[asyncio.Task] = field(default_factory=list)
    agent_ws: Any = None


_STATES: dict[int, _ServerState] = {}
_STATES_LOCK = asyncio.Lock()
_SYNC_STATES_LOCK = threading.Lock()


def append_msm_log_to_memory(server_id: int, text: str, timestamp: str) -> None:
    """Synchronous, thread-safe function to append an MSM log line to the in-memory
    state of a server, if it is currently loaded.
    """
    # Legacy function - not strictly needed for live updates anymore because file tail
    # handles everything and ingest_line unifies it. But for backwards compatibility
    # and testing, we can keep it as a no-op or just append.
    # To prevent duplicates, we leave it as a no-op because the file tail reads it anyway.
    pass


async def ingest_line(server_id: int, text: str, source: str, timestamp: str | None = None) -> None:
    """Einheitliche Log-Ingestion-Methode, die eingehende Zeilen an alle Clients broadcastet."""
    ts = timestamp or _utc_iso()
    payload = None
    
    async with _STATES_LOCK:
        state = _STATES.get(server_id)
        if not state:
            return
            
        with state.lock:
            line = _Line(
                id=state.next_id,
                text=text.rstrip("\r\n"),
                source=source,
                timestamp=ts,
            )
            state.next_id += 1
            state.lines.append(line)
            payload = _serialize(line)
            
        # Copy websockets set to iterate safely
        targets = list(state.websockets)
        
    for ws in targets:
        try:
            await ws.send_text(payload)
        except Exception as exc:
            logger.debug("Broadcast to ws failed: %s", exc)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_state(server_id: int) -> _ServerState:
    """Liefert (oder erstellt) den Per-Server State."""
    state = _STATES.get(server_id)
    if state is None:
        state = _ServerState()
        _STATES[server_id] = state
    return state


def _serialize(line: _Line) -> str:
    """JSON-Frame fuer eine Zeile. Stellt sicher, dass der Client parsen kann."""
    return json.dumps(
        {
            "id": line.id,
            "timestamp": line.timestamp,
            "source": line.source,
            "text": line.text,
        },
        ensure_ascii=False,
    )


async def _read_initial_backlog(log_path: str, state: _ServerState) -> None:
    """Liest die MSM-Console-Logdatei in den Ring-Buffer."""
    if not os.path.exists(log_path):
        return
    try:
        with open(log_path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                for raw_line in chunk.splitlines(keepends=False):
                    if not raw_line:
                        continue
                    line_str = raw_line.decode("utf-8", errors="replace")
                    ts = _utc_iso()
                    text = line_str
                    if "\t" in line_str:
                        parts = line_str.split("\t", 1)
                        if len(parts) == 2:
                            cand, rest = parts
                            try:
                                cand_norm = cand.replace("Z", "+00:00")
                                datetime.fromisoformat(cand_norm)
                                ts = cand
                                text = rest
                            except Exception:
                                pass
                    with state.lock:
                        state.lines.append(
                            _Line(
                                id=state.next_id,
                                text=text,
                                source="msm",
                                timestamp=ts,
                            )
                        )
                        state.next_id += 1
    except OSError as exc:
        logger.warning("ws backlog read failed for %s: %s", log_path, exc)


async def _tail_file_loop(log_path: str, state: _ServerState, on_line) -> None:
    """Tail-Loop fuer die MSM-Lifecycle-Logdatei."""
    pos = 0
    if os.path.exists(log_path):
        try:
            pos = os.path.getsize(log_path)
        except OSError:
            pos = 0
    while True:
        await asyncio.sleep(0.1)
        try:
            size = os.path.getsize(log_path) if os.path.exists(log_path) else 0
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
        for raw_line in chunk.splitlines(keepends=False):
            if not raw_line:
                continue
            line_str = raw_line.decode("utf-8", errors="replace")
            ts = None
            text = line_str
            if "\t" in line_str:
                parts = line_str.split("\t", 1)
                if len(parts) == 2:
                    cand, rest = parts
                    try:
                        cand_norm = cand.replace("Z", "+00:00")
                        datetime.fromisoformat(cand_norm)
                        ts = cand
                        text = rest
                    except Exception:
                        pass
            await on_line(text, "msm", ts)


async def _tail_docker_loop(container: str, on_line) -> None:
    """Tail-Loop fuer `docker logs --follow` mit Polling auf Container-Readiness."""
    backoff = 0.5
    while True:
        try:
            if not docker_service.is_running(container):
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)
                continue
            backoff = 0.5
            async for raw_line in docker_service.stream_logs(container, tail=200):
                text = raw_line
                ts = None
                if raw_line and " " in raw_line:
                    first, rest = raw_line.split(" ", 1)
                    if first and (first[0].isdigit() or first[0] == "-"):
                        try:
                            first_norm = first.replace("Z", "+00:00")
                            datetime.fromisoformat(first_norm)
                            ts = first
                            text = rest
                        except Exception:
                            pass
                await on_line(text, "docker", ts)
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("ws docker tailing failed for %s: %s", container, exc)
            await asyncio.sleep(2.0)


async def _close_safely(ws: WebSocket, code: int = 1000) -> None:
    """Schliesst den WS falls noch offen."""
    try:
        await ws.close(code=code)
    except Exception:
        pass


async def _agent_proxy_loop(server_id: int, container: str, node: Any) -> None:
    import websockets
    from websockets.exceptions import ConnectionClosed
    from services.node_client import NodeClient, NodeClientError
    
    backoff = 0.5
    while True:
        try:
            client = NodeClient.from_node(node)
            agent_url = client.console_ws_url(container)
            token = client.bearer_token
            
            import ssl as ssl_mod
            ssl_context = client._verify()
            ssl_param = None
            if agent_url.startswith("wss://"):
                if isinstance(ssl_context, ssl_mod.SSLContext):
                    ssl_param = ssl_context
                else:
                    ssl_param = ssl_mod.create_default_context()
                    
            async with websockets.connect(
                agent_url,
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=10,
                max_size=2 * 1024 * 1024,
                ssl=ssl_param,
            ) as agent_ws:
                async with _STATES_LOCK:
                    state = _get_state(server_id)
                    state.agent_ws = agent_ws
                
                backoff = 0.5
                async for message in agent_ws:
                    text = message if isinstance(message, str) else message.decode("utf-8", errors="replace")
                    await ingest_line(server_id, text, "docker")
                    
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)
        except Exception as exc:
            logger.warning("agent proxy loop failed for server_id=%s: %s", server_id, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)


async def connect(
    ws: WebSocket,
    server_id: int,
    container: str,
    log_path: str,
    last_id: int | None = None,
    node: Any | None = None,
) -> None:
    """Hauptcoroutine: akzeptiert die WS-Verbindung, spult Replay ab, streamed live."""
    async with _STATES_LOCK:
        state = _get_state(server_id)
        if len(state.websockets) >= MAX_CONCURRENT_WS_PER_SERVER:
            await ws.accept()
            await _close_safely(ws, code=1013)
            return
            
        state.websockets.add(ws)
        is_first = len(state.websockets) == 1

    try:
        await ws.accept()
        
        use_agent = False
        if node is not None and not getattr(node, "is_local", False):
            use_agent = True
        if node is not None and getattr(node, "is_local", False) and not docker_service.is_available():
            use_agent = True
            
        if is_first:
            if not state.lines:
                try:
                    await _read_initial_backlog(log_path, state)
                except Exception:
                    pass
                    
            state.tasks.append(asyncio.create_task(
                _tail_file_loop(log_path, state, lambda txt, src, ts: ingest_line(server_id, txt, src, ts))
            ))
            
            if use_agent:
                state.tasks.append(asyncio.create_task(
                    _agent_proxy_loop(server_id, container, node)
                ))
            else:
                state.tasks.append(asyncio.create_task(
                    _tail_docker_loop(container, lambda txt, src, ts: ingest_line(server_id, txt, src, ts))
                ))
                
        # Send replay to THIS client only
        async with _STATES_LOCK:
            with state.lock:
                if last_id is None:
                    replay = list(state.lines)
                else:
                    replay = [l for l in state.lines if l.id > last_id]
        
        for line in replay:
            await ws.send_text(_serialize(line))
            
        # Loop for client inputs
        while True:
            if ws.client_state != WebSocketState.CONNECTED:
                break
            try:
                msg = await ws.receive_text()
            except WebSocketDisconnect:
                break
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                async with _STATES_LOCK:
                    agent_ws = state.agent_ws
                if agent_ws:
                    try:
                        await agent_ws.send(msg)
                    except Exception:
                        pass
                continue
                
            action = payload.get("action") if isinstance(payload, dict) else None
            if action == "ping":
                await ws.send_text(json.dumps({"action": "pong"}))
            elif action == "input" and isinstance(payload.get("data"), str):
                async with _STATES_LOCK:
                    agent_ws = state.agent_ws
                if agent_ws:
                    try:
                        await agent_ws.send(payload["data"])
                    except Exception:
                        pass
            elif isinstance(payload.get("line"), str):
                async with _STATES_LOCK:
                    agent_ws = state.agent_ws
                if agent_ws:
                    try:
                        await agent_ws.send(payload["line"])
                    except Exception:
                        pass
    finally:
        async with _STATES_LOCK:
            state.websockets.discard(ws)
            if len(state.websockets) == 0:
                for t in state.tasks:
                    t.cancel()
                state.tasks.clear()
                
                if state.agent_ws:
                    try:
                        asyncio.create_task(state.agent_ws.close())
                    except Exception:
                        pass
                    state.agent_ws = None
                    
                with state.lock:
                    state.lines.clear()
                    state.next_id = 1


def reset_state_for_tests() -> None:
    """Loescht allen In-Memory-State. Nur fuer Tests."""
    _STATES.clear()
