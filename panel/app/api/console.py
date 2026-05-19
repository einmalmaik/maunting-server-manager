"""
WebSocket-based live console for the Conan Exiles server log and tmux session.

Auth flow:
  1. GET /api/console/token?source=log|tmux -> { token, expires_in, source }
  2. WS  /api/console/ws ; client sends { type: "auth", token } as first message
"""
from __future__ import annotations

import asyncio
import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..config import get_settings
from ..database import SessionLocal
from ..models import User
from ..permissions import P_CONSOLE_LOG, P_CONSOLE_TMUX, has_permission
from .deps import get_current_user, require_server

router = APIRouter()

_TOKEN_TTL: float = 60.0


@dataclass
class _ConsoleToken:
    user_id: int
    source: str
    server_name: str = "default"


_LEVEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(error|exception|fatal|crash)\b", re.I), "ERROR"),
    (re.compile(r"\b(warning|warn)\b", re.I), "WARN"),
    (re.compile(r"\b(adminlog|admin)\b", re.I), "ADMIN"),
    (re.compile(r"\b(scriptlog|script\s*log)\b", re.I), "SCRIPT"),
    (re.compile(r"\b(debug)\b", re.I), "DEBUG"),
    (re.compile(r"\b(info)\b", re.I), "INFO"),
]

_SERVER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")
_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x1B\x07]*(?:\x07|\x1B\\))"
)
_TMUX_POLL_INTERVAL = 1.0
_TMUX_PING_AFTER_N_EMPTY = 5


def _token_secret() -> bytes:
    return get_settings().secret_key.encode("utf-8", errors="ignore")


def _encode_token_payload(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _decode_token_payload(token: str) -> dict | None:
    body, separator, signature = token.partition(".")
    if not separator or not body or not signature:
        return None

    expected = hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    padding = "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(body + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _issue_token(user_id: int, source: str, server_name: str = "default") -> str:
    payload = {
        "user_id": user_id,
        "source": source,
        "server_name": server_name,
        "expires_at": time.time() + _TOKEN_TTL,
        "nonce": secrets.token_urlsafe(8),
    }
    return _encode_token_payload(payload)


def _consume_token(token: str) -> _ConsoleToken | None:
    payload = _decode_token_payload(token)
    if payload is None:
        return None

    try:
        expires_at = float(payload.get("expires_at", 0))
        user_id = int(payload.get("user_id", 0))
        source = str(payload.get("source", ""))
        server_name = str(payload.get("server_name", ""))
    except (TypeError, ValueError):
        return None

    if time.time() > expires_at:
        return None
    if source not in {"log", "tmux"}:
        return None
    if not _SERVER_NAME_RE.match(server_name):
        return None

    return _ConsoleToken(user_id=user_id, source=source, server_name=server_name)


def _is_console_token_authorized(entry: _ConsoleToken) -> bool:
    required = P_CONSOLE_LOG if entry.source == "log" else P_CONSOLE_TMUX
    try:
        with SessionLocal() as db:
            user = db.get(User, entry.user_id)
            return bool(user is not None and user.is_active and has_permission(user, required))
    except Exception:
        return False


def _detect_level(line: str) -> str:
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.search(line):
            return level
    return "PLAIN"


def _make_line(text: str, seq: int) -> dict:
    return {"id": seq, "text": text, "level": _detect_level(text)}


def _find_active_log(server_name: str = "default") -> Path | None:
    if not _SERVER_NAME_RE.match(server_name):
        return None
    base = Path(os.path.expanduser("~")) / "servers"
    server_dir = (base / server_name).resolve()
    if not server_dir.is_relative_to(base.resolve()):
        return None

    log_dirs = [
        server_dir / "serverfiles" / "ConanSandbox" / "Saved" / "Logs",
        server_dir / "serverprofile",
    ]

    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    candidates: list[Path] = []
    for log_dir in log_dirs:
        if not log_dir.is_dir():
            continue
        candidates.extend(path for path in log_dir.glob("*.log") if path.is_file())
        candidates.extend(path for path in log_dir.glob("*.txt") if path.is_file())
        candidates.extend(path for path in log_dir.glob("*.RPT") if path.is_file())
    candidates.sort(key=_safe_mtime, reverse=True)
    return candidates[0] if candidates else None


def _strip_ansi_sequences(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _normalize_tmux_snapshot(output: str) -> list[str]:
    normalized: list[str] = []
    for raw_line in output.splitlines():
        line = _strip_ansi_sequences(raw_line).replace("\r", "").rstrip()
        normalized.append("" if not line.strip() else line)

    while normalized and not normalized[0]:
        normalized.pop(0)
    while normalized and not normalized[-1]:
        normalized.pop()

    return normalized


def _diff_tmux_snapshots(previous: list[str], current: list[str]) -> list[str]:
    if current == previous or not current:
        return []
    if not previous:
        return current

    anchor_limit = min(8, len(previous), len(current))
    for anchor_size in range(anchor_limit, 0, -1):
        anchor = previous[-anchor_size:]
        for index in range(len(current) - anchor_size, -1, -1):
            if current[index : index + anchor_size] == anchor:
                return current[index + anchor_size :]

    return current


async def _stream_log(ws: WebSocket, rpt_path: Path) -> None:
    seq = 0
    proc = await asyncio.create_subprocess_exec(
        "tail",
        "-F",
        "-n",
        "100",
        str(rpt_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        while True:
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            except asyncio.TimeoutError:
                if ws.client_state != WebSocketState.CONNECTED:
                    break
                await ws.send_text(json.dumps({"type": "ping"}))
                continue

            if not raw:
                break

            text = raw.decode(errors="replace").rstrip("\n\r")
            if not text:
                continue

            seq += 1
            try:
                await ws.send_text(json.dumps({"type": "line", "data": [_make_line(text, seq)]}))
            except (WebSocketDisconnect, Exception):
                break
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def _stream_tmux(ws: WebSocket, server_name: str = "default") -> None:
    if not _SERVER_NAME_RE.match(server_name):
        try:
            await ws.send_text(json.dumps({"type": "error", "data": "Invalid server name."}))
        except Exception:
            pass
        return

    session_name = f"{getpass.getuser()}-{server_name}"
    seq = 0
    last_lines: list[str] = []
    ping_counter = 0
    loop = asyncio.get_running_loop()

    while True:
        if ws.client_state != WebSocketState.CONNECTED:
            break

        def _capture() -> str:
            try:
                result = subprocess.run(
                    ["tmux", "capture-pane", "-J", "-t", session_name, "-p", "-S", "-"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                return result.stdout if result.returncode == 0 else ""
            except Exception:
                return ""

        output = await loop.run_in_executor(None, _capture)
        new_lines = _normalize_tmux_snapshot(output)
        fresh = _diff_tmux_snapshots(last_lines, new_lines)
        last_lines = new_lines

        if fresh:
            ping_counter = 0
            batch: list[dict] = []
            for text in fresh:
                seq += 1
                batch.append(_make_line(text, seq))
            try:
                await ws.send_text(json.dumps({"type": "line", "data": batch}))
            except (WebSocketDisconnect, Exception):
                break
        else:
            ping_counter += 1
            if ping_counter >= _TMUX_PING_AFTER_N_EMPTY:
                ping_counter = 0
                try:
                    await ws.send_text(json.dumps({"type": "ping"}))
                except (WebSocketDisconnect, Exception):
                    break

        await asyncio.sleep(_TMUX_POLL_INTERVAL)


@router.get("/console/token")
def get_console_token(
    source: Literal["log", "tmux"] = Query("log"),
    user: User = Depends(get_current_user),
    server: str = Depends(require_server),
) -> dict:
    required = P_CONSOLE_LOG if source == "log" else P_CONSOLE_TMUX
    if not has_permission(user, required):
        raise HTTPException(status_code=403, detail="Permission denied.")
    token = _issue_token(user.id, source, server_name=server)
    return {"token": token, "expires_in": int(_TOKEN_TTL), "source": source}


@router.websocket("/console/ws")
async def console_ws(ws: WebSocket) -> None:
    await ws.accept()

    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        msg = json.loads(raw)
        token_str = msg.get("token", "") if isinstance(msg, dict) else ""
    except WebSocketDisconnect:
        return
    except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
        try:
            await ws.close(code=4403)
        except Exception:
            pass
        return

    entry = _consume_token(token_str)
    if entry is None or not _is_console_token_authorized(entry):
        try:
            await ws.close(code=4403)
        except Exception:
            pass
        return

    try:
        if entry.source == "tmux":
            await _stream_tmux(ws, server_name=entry.server_name)
        else:
            rpt_path = _find_active_log(server_name=entry.server_name)
            if rpt_path is None:
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "data": (
                                f"No Conan log file found in ~/servers/{entry.server_name}/serverfiles/ConanSandbox/Saved/Logs/. "
                                "Is the Conan Exiles server running?"
                            ),
                        }
                    )
                )
                try:
                    await ws.close()
                except Exception:
                    pass
                return
            await _stream_log(ws, rpt_path)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await ws.send_text(json.dumps({"type": "error", "data": "An unexpected error occurred."}))
        except Exception:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass
