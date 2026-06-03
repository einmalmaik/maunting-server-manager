"""WebSocket-Service fuer Live-Server-Konsole.

Stellt den einzigen Stream-Pfad fuer die MSM-Server-Konsole bereit: ein
bidirektionaler WebSocket-Endpoint mit In-Memory-Ring-Buffer pro Server.
Reconnects nutzen ``?last_id=`` um nur verpasste Zeilen nachzuliefern (kein
kompletter Backlog-Repetition wie frueher beim SSE-Pfad).

KISS:
- Eine In-Memory-State-Klasse, eine ``connect()``-Coroutine.
- File-Tail- und Docker-Stream-Loops sind hier unabhaengig vom ehemaligen
  SSE-Service implementiert (kein gemeinsamer Code). Geteilt wird nur
  ``docker_service`` fuer ``stream_logs`` / ``is_running`` — alles andere
  hier ist WS-spezifisch.
- Keine externen State-Stores (Redis/DB). Verloren beim Restart = akzeptabel,
  da der File-Backlog dann ohnehin wieder eingelesen wird.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from services import docker_service
from games.base import _console_log_path

logger = logging.getLogger(__name__)

# Groesse des In-Memory-Ring-Buffers pro Server. Reicht fuer ~500 Zeilen
# Reconnect-Resume. Mehr waere Memory-Verschwendung; weniger fuehlt sich
# bei kurzen Netzwerk-Hickups schon „verloren" an.
RING_BUFFER_SIZE = 500

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


_STATES: dict[int, _ServerState] = {}
_STATES_LOCK = asyncio.Lock()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_state(server_id: int) -> _ServerState:
    """Liefert (oder erstellt) den Per-Server State.

    MUSS unter ``_STATES_LOCK`` aufgerufen werden — der get-then-create
    auf ``_STATES`` ist nicht atomar, sodass zwei parallele Erstverbindungen
    sonst jeweils ein eigenes ``_ServerState`` erzeugen und einander
    ueberschreiben wuerden. Alle aktuellen Aufrufer halten den Lock bereits.
    """
    state = _STATES.get(server_id)
    if state is None:
        state = _ServerState()
        _STATES[server_id] = state
    return state


def _serialize(line: _Line) -> str:
    """JSON-Frame fuer eine Zeile. Stellt sicher, dass der Client parsen kann.

    Format: ``{"id": int, "timestamp": iso, "source": "msm"|"docker", "text": str}``.
    Die monotone ``id`` ermoeglicht Reconnect-Resume via ``?last_id=``.
    """
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
    """Liest die MSM-Console-Logdatei in den Ring-Buffer (beim ersten Connect
    pro Server, oder nach Reconnect-Bedarf).
    """
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
                    text = raw_line.decode("utf-8", errors="replace")
                    state.lines.append(
                        _Line(
                            id=state.next_id,
                            text=text,
                            source="msm",
                            timestamp=_utc_iso(),
                        )
                    )
                    state.next_id += 1
    except OSError as exc:
        logger.warning("ws backlog read failed for %s: %s", log_path, exc)


async def _tail_file_loop(log_path: str, state: _ServerState, on_line) -> None:
    """Tail-Loop fuer die MSM-Lifecycle-Logdatei. Push jede neue Zeile via on_line."""
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
            text = raw_line.decode("utf-8", errors="replace")
            await on_line(text, "msm")


async def _tail_docker_loop(container: str, on_line) -> None:
    """Tail-Loop fuer `docker logs --follow` mit Polling auf Container-Readiness.

    Wenn der Container beim WS-Connect noch nicht laeuft (typischer Fall:
    User klickt Start und oeffnet die Konsole sofort), wartet die Loop
    mit Backoff, bis der Container ready ist. Wenn `stream_logs` endet
    (Container gestoppt, rootless-Daemon kurz weg, Recreate), pollt die
    Loop ebenfalls neu, statt stillschweigend zu enden — sonst sieht der
    User nach einem Restart keine Game-Logs mehr, obwohl der WS lebt.

    Backoff: 0.5s -> 1.5s -> 5s (cap), damit ein abwesender Docker-Daemon
    nicht in einer heissen Spin-Loop landet.
    """
    backoff = 0.5
    while True:
        try:
            if not docker_service.is_running(container):
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)
                continue
            backoff = 0.5
            # stream_logs iteriert bis der Subprozess endet (Container stoppt,
            # Daemon weg, Pipe kaputt). Danach zurueck zum Readiness-Check.
            async for text in docker_service.stream_logs(container, tail=200):
                await on_line(text, "docker")
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("ws docker tailing failed for %s: %s", container, exc)
            await asyncio.sleep(2.0)


async def _close_safely(ws: WebSocket, code: int = 1000) -> None:
    """Schliesst den WS falls noch offen. Schluckt Exceptions (idempotent)."""
    try:
        await ws.close(code=code)
    except Exception:
        pass


async def connect(
    ws: WebSocket,
    server_id: int,
    container: str,
    log_path: str,
    last_id: int | None = None,
) -> None:
    """Hauptcoroutine: akzeptiert die WS-Verbindung, spult Replay ab, streamed live.

    Parameter:
    - ws: FastAPI WebSocket
    - server_id, container, log_path: bereits aufgeloeste Identifiers
    - last_id: Wenn gesetzt, werden Zeilen mit id > last_id aus dem Ring-Buffer
      zuerst repliziert, dann live gestreamt. None = voller Backlog + live.
    """
    async with _STATES_LOCK:
        state = _get_state(server_id)
        if state.active_connections >= MAX_CONCURRENT_WS_PER_SERVER:
            await ws.accept()
            await _close_safely(ws, code=1013)  # "try again later"
            return
        state.active_connections += 1

    try:
        await ws.accept()

        # Backlog nur lesen, wenn der Ring-Buffer leer ist (cold start / restart).
        # Ansonsten halten wir die letzten 500 Zeilen im Speicher und koennen
        # bei Reconnect punktuell replayen.
        async with _STATES_LOCK:
            is_cold = len(state.lines) == 0

        if is_cold:
            await _read_initial_backlog(log_path, state)

        # Replay-Phase: alle Zeilen aus dem Buffer senden.
        # - last_id=None (Cold-Connect): voller Backlog.
        # - last_id=N (Reconnect): nur Zeilen mit id > N.
        async with _STATES_LOCK:
            if last_id is None:
                replay = list(state.lines)
            else:
                replay = [l for l in state.lines if l.id > last_id]
        for line in replay:
            await ws.send_text(_serialize(line))

        async def _on_line(text: str, source: str) -> None:
            """Speichert die Zeile im Ring-Buffer (unter Lock) und schickt sie an den Client.

            Wird von zwei parallelen Tasks (_tail_file_loop + _tail_docker_loop)
            aufgerufen. Der Lock schuetzt next_id und das deque.append, damit
            bei reconnect-resume via ?last_id= keine Luecken entstehen.
            """
            async with _STATES_LOCK:
                line = _Line(
                    id=state.next_id,
                    text=text,
                    source=source,
                    timestamp=_utc_iso(),
                )
                state.next_id += 1
                state.lines.append(line)
                payload = _serialize(line)
            await _safe_send(ws, payload)

        async def _safe_send(ws_: WebSocket, payload: str) -> None:
            try:
                await ws_.send_text(payload)
            except Exception as exc:
                logger.debug("ws send failed (client disconnected?): %s", exc)

        # Live-Phasen als parallele Tasks. Beide laufen so lange, bis der
        # Client disconnectet. _tail_docker_loop pollt selbst auf Container-
        # Readiness, falls der Container beim Connect noch nicht laeuft.
        file_task = asyncio.create_task(_tail_file_loop(log_path, state, _on_line))
        docker_task = asyncio.create_task(_tail_docker_loop(container, _on_line))

        try:
            # Lese Client-Frames (vorerst nur Heartbeat-Handling).
            # Bricht automatisch ab, wenn der Client die Verbindung schliesst.
            while True:
                if ws.client_state != WebSocketState.CONNECTED:
                    break
                try:
                    msg = await ws.receive_text()
                except WebSocketDisconnect:
                    # Normaler Disconnect vom Client. Tasks werden im finally gecancelt.
                    break
                try:
                    payload = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                action = payload.get("action") if isinstance(payload, dict) else None
                if action == "ping":
                    await ws.send_text(json.dumps({"action": "pong"}))
        finally:
            file_task.cancel()
            docker_task.cancel()
            for t in (file_task, docker_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
    finally:
        async with _STATES_LOCK:
            state.active_connections -= 1
            if state.active_connections < 0:
                state.active_connections = 0


def reset_state_for_tests() -> None:
    """Loescht allen In-Memory-State. Nur fuer Tests."""
    _STATES.clear()
