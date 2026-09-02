from __future__ import annotations

import logging
import time

logger = logging.getLogger("ai_voice_debug")

try:
    from services import metrics as _metrics
except Exception:
    _metrics = None


def emit(code: str, hint: str = "", **fields: object) -> None:
    safe_fields = {k: v for k, v in fields.items() if k not in {"api_key", "sdp", "call_id", "arguments", "output", "raw_event"}}
    payload = " ".join(f"{k}={v}" for k, v in safe_fields.items())
    suffix = f" {payload}" if payload else ""
    msg = f"[voice] code={code} hint={hint}{suffix}" if hint else f"[voice] code={code}{suffix}"
    if code.startswith("REALTIME_") or code in {"TOOL_TIMEOUT", "LEERE_ANTWORT", "HANDSHAKE_FAILED"}:
        logger.warning(msg)
    else:
        logger.info(msg)
    if _metrics is not None:
        try:
            _metrics.record("ai_voice_debug", code)
        except Exception:
            pass


def timed(code: str):
    start = time.monotonic()
    def done(hint: str = "", **fields: object) -> None:
        ms = int((time.monotonic() - start) * 1000)
        emit(code, hint=hint, elapsed_ms=ms, **fields)
    return done
