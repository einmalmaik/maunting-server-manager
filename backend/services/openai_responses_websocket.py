"""OpenAI Responses WebSocket Client & Session Management.

Ermöglicht persistente WebSocket-Sitzungen für agentic Tool-Loops, Turn-Chaining
über `previous_response_id` und Stream-Multiplexing (`stream_id`) mit zero-breakage
Fallback auf HTTP SSE.

**Nur als gehaltene Sitzung.** Der Modus spart dort, wo eine Verbindung viele
Züge trägt; eine neue Verbindung für einen einzelnen Zug zahlt nur den Aufbau
und ist langsamer als HTTP mit gehaltener Verbindung (gemessen am 23.09.2026,
siehe `openai_responses_adapter.stream_responses`). Die Hilfe dafür,
``stream_responses_ws``, ist deshalb entfallen.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse, urlunparse

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException
    from websockets.protocol import State
except ImportError:  # websockets ist optional / Fallback greift
    websockets = None  # type: ignore[assignment]
    ConnectionClosed = Exception  # type: ignore[misc,assignment]
    WebSocketException = Exception  # type: ignore[misc,assignment]
    State = None  # type: ignore[assignment,misc]

from services.openai_compatible_adapter import (
    MAX_ASSISTANT_CHARS,
    MAX_REASONING_CHARS,
    MAX_STREAM_FRAMES,
    MAX_STREAM_SECONDS,
    MAX_TOOL_ARGUMENT_CHARS,
    AiProviderRequestError,
    ProviderToolCall,
    StreamChunk,
    StreamUsage,
    _ganzzahl,
    _kurzfassung,
    _teilmenge,
    anbieter_fehlercode,
)

logger = logging.getLogger(__name__)


def ws_url_fuer_base_url(base_url: str) -> str:
    """Wandelt eine HTTP(S)-Base-URL in eine WebSocket-URL für die Responses-API um.

    Beispiel:
    https://api.openai.com/v1 -> wss://api.openai.com/v1/responses
    """
    parsed = urlparse(base_url.rstrip("/"))
    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    path = parsed.path
    if not path.endswith("/responses"):
        path = path.rstrip("/") + "/responses"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def fehler_im_rahmen(rahmen: dict) -> tuple[str, str | None] | None:
    """Ein Fehler, den die Responses-API im Strom meldet statt als Status.

    Derselbe Rahmen, ob er per WebSocket oder per SSE ankommt — deshalb eine
    Funktion fuer beide Wege (`openai_responses_adapter` liest sie mit). Die
    Codes uebersetzt `anbieter_fehlercode`; bis zum 22.09.2026 hatten beide Wege
    je eine eigene Tabelle, und keine kannte die Codes der GPT-6-Familie.
    """
    typ = rahmen.get("type")
    if typ in ("response.failed", "response.incomplete", "error"):
        antwort = rahmen.get("response")
        fehler = None
        if isinstance(antwort, dict):
            fehler = antwort.get("error") or antwort.get("incomplete_details")
        if fehler is None:
            fehler = rahmen.get("error") or rahmen
        marke = "AI_PROVIDER_REQUEST_REJECTED"
        nachricht = ""
        if isinstance(fehler, dict):
            nachricht = str(fehler.get("message") or fehler.get("reason") or "")
            code = fehler.get("code")
            marke = anbieter_fehlercode(code, marke)
        return marke, _kurzfassung(nachricht or str(typ))
    return None


def _ws_usage_uebernehmen(usage: StreamUsage, rohdaten: Any) -> None:
    """Übernimmt Token- und Usage-Statistiken aus response.completed."""
    if not isinstance(rohdaten, dict):
        return
    eingabe = _ganzzahl(rohdaten.get("input_tokens"))
    ausgabe = _ganzzahl(rohdaten.get("output_tokens"))
    gesamt = _ganzzahl(rohdaten.get("total_tokens"))
    if eingabe is not None:
        usage.prompt_tokens = eingabe
    if ausgabe is not None:
        usage.completion_tokens = ausgabe
    if gesamt is not None:
        usage.total_tokens = gesamt
    usage.cached_tokens += _teilmenge(rohdaten.get("input_tokens_details"), "cached_tokens")
    usage.cache_write_tokens += _teilmenge(
        rohdaten.get("input_tokens_details"), "cache_write_tokens"
    )
    usage.reasoning_tokens += _teilmenge(
        rohdaten.get("output_tokens_details"), "reasoning_tokens"
    )
    usage.vom_anbieter = True


class OpenAiResponsesWsSession:
    """Hält eine persistente WebSocket-Verbindung für Multi-Turn Agentic Loops."""

    def __init__(self, ws_url: str, headers: dict[str, str]) -> None:
        self.ws_url = ws_url
        self.headers = headers
        self._ws: Any = None
        self._lock = asyncio.Lock()
        self.last_response_id: str | None = None
        self.last_stream_id: str | None = None

    def _offen(self) -> bool:
        """Ob die gehaltene Verbindung noch steht.

        websockets ab 13 (der asyncio-Client; requirements.txt: 16.0) führt kein
        ``closed`` mehr, nur ``state``. Hier stand ``getattr(self._ws, "closed",
        True)``, und das war dort immer ``True``: jeder Zug öffnete eine neue
        Verbindung, die alte blieb offen liegen, und ein Verweis auf die vorige
        Antwort ging ins Leere — „Previous response with id '…' not found",
        gemessen am 23.09.2026 gegen gpt-6-luna.
        """
        if self._ws is None:
            return False
        zustand = getattr(self._ws, "state", None)
        if zustand is not None:
            return zustand == State.OPEN
        return not getattr(self._ws, "closed", True)

    async def connect(self) -> None:
        if websockets is None:
            raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE", "websockets package missing")
        if not self._offen():
            self._ws = await websockets.connect(
                self.ws_url,
                additional_headers=self.headers,
                max_size=4 * 1024 * 1024,
                open_timeout=10,
            )

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def __aenter__(self) -> OpenAiResponsesWsSession:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def stream_turn(
        self,
        payload: dict[str, Any],
        usage: StreamUsage,
        *,
        deadline: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Führt einen Chat-/Tool-Zug über die offene WebSocket-Verbindung aus."""
        async with self._lock:
            await self.connect()
            deadline = deadline or (time.monotonic() + MAX_STREAM_SECONDS)
            frames = 0
            aufrufe: dict[str, dict[str, str]] = {}
            reihenfolge: list[str] = []
            fertige_aufrufe: set[str] = set()
            abgeschlossen = False

            def fertiger_aufruf(kennung: str) -> ProviderToolCall:
                eintrag = aufrufe.get(kennung)
                if not eintrag or not eintrag["call_id"] or not eintrag["name"]:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                try:
                    argumente = json.loads(eintrag["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
                if not isinstance(argumente, dict):
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                return ProviderToolCall(
                    id=eintrag["call_id"], name=eintrag["name"], arguments=argumente
                )

            # Der Körper steht flach neben ``type``: „This payload uses the same
            # top-level fields as `POST /v1/responses`" (Referenz „Responses
            # WebSocket events", `response.create`). Bis zum 23.09.2026 stand er
            # unter ``"response"``, und OpenAI lehnte jeden Zug mit „Missing
            # required parameter: 'model'" ab.
            create_msg = {"type": "response.create", **payload}
            try:
                await self._ws.send(json.dumps(create_msg))
            except Exception as exc:
                logger.warning("OpenAI Responses WS send failed: %s", exc)
                await self.close()
                raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc

            usage.anfragen += 1

            try:
                while True:
                    if time.monotonic() > deadline:
                        raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT")

                    try:
                        timeout_step = max(0.1, deadline - time.monotonic())
                        raw_frame = await asyncio.wait_for(self._ws.recv(), timeout=timeout_step)
                    except asyncio.TimeoutError:
                        raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT")
                    except (ConnectionClosed, WebSocketException) as exc:
                        logger.warning("OpenAI Responses WS connection closed: %s", exc)
                        await self.close()
                        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc

                    if not raw_frame:
                        continue

                    frames += 1
                    if frames > MAX_STREAM_FRAMES:
                        raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")

                    try:
                        rahmen = json.loads(raw_frame)
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc

                    if not isinstance(rahmen, dict):
                        raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")

                    if (gemeldet := fehler_im_rahmen(rahmen)) is not None:
                        marke, text = gemeldet
                        logger.warning("OpenAI Responses WS stream error: code=%s text=%s", marke, text)
                        raise AiProviderRequestError(marke, text)

                    typ = rahmen.get("type")

                    if typ == "response.created":
                        resp_obj = rahmen.get("response")
                        if isinstance(resp_obj, dict):
                            resp_id = resp_obj.get("id")
                            stream_id = resp_obj.get("stream_id")
                            if isinstance(resp_id, str):
                                self.last_response_id = resp_id
                                usage.response_id = resp_id
                            if isinstance(stream_id, str):
                                self.last_stream_id = stream_id
                                usage.stream_id = stream_id

                    elif typ == "response.output_text.delta":
                        stueck = rahmen.get("delta")
                        if isinstance(stueck, str) and stueck:
                            usage.output_chars += len(stueck)
                            if usage.output_chars > MAX_ASSISTANT_CHARS:
                                raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                            yield StreamChunk("content", stueck)

                    elif typ == "response.reasoning_summary_text.delta":
                        gedanke = rahmen.get("delta")
                        if isinstance(gedanke, str) and gedanke:
                            usage.reasoning_chars += len(gedanke)
                            if usage.reasoning_chars <= MAX_REASONING_CHARS:
                                yield StreamChunk("reasoning", gedanke)

                    elif typ == "response.output_item.added":
                        posten = rahmen.get("item")
                        if isinstance(posten, dict) and posten.get("type") == "function_call":
                            kennung = posten.get("id")
                            if isinstance(kennung, str):
                                if kennung not in aufrufe:
                                    reihenfolge.append(kennung)
                                name = posten.get("name") or ""
                                aufrufe[kennung] = {
                                    "call_id": posten.get("call_id") or "",
                                    "name": name,
                                    "arguments": posten.get("arguments") or "",
                                }
                                if name:
                                    yield StreamChunk("tool_start", name)

                    elif typ == "response.function_call_arguments.delta":
                        kennung = rahmen.get("item_id")
                        stueck = rahmen.get("delta")
                        if isinstance(kennung, str) and isinstance(stueck, str):
                            eintrag = aufrufe.setdefault(
                                kennung, {"call_id": "", "name": "", "arguments": ""}
                            )
                            if kennung not in reihenfolge:
                                reihenfolge.append(kennung)
                            eintrag["arguments"] += stueck
                            if len(eintrag["arguments"]) > MAX_TOOL_ARGUMENT_CHARS:
                                raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")

                    elif typ == "response.output_item.done":
                        posten = rahmen.get("item")
                        if isinstance(posten, dict) and posten.get("type") == "function_call":
                            kennung = posten.get("id")
                            if isinstance(kennung, str):
                                if kennung not in reihenfolge:
                                    reihenfolge.append(kennung)
                                aufrufe[kennung] = {
                                    "call_id": posten.get("call_id") or "",
                                    "name": posten.get("name") or "",
                                    "arguments": posten.get("arguments") or "",
                                }
                                if kennung not in fertige_aufrufe:
                                    call = fertiger_aufruf(kennung)
                                    fertige_aufrufe.add(kennung)
                                    usage.tool_calls.append(call)
                                    yield StreamChunk("tool_ready", tool_call=call)

                    elif typ == "response.completed":
                        abgeschlossen = True
                        antwort = rahmen.get("response")
                        if isinstance(antwort, dict):
                            resp_id = antwort.get("id")
                            if isinstance(resp_id, str):
                                self.last_response_id = resp_id
                                usage.response_id = resp_id
                            _ws_usage_uebernehmen(usage, antwort.get("usage"))
                        break

                if not abgeschlossen:
                    raise AiProviderRequestError("AI_PROVIDER_STREAM_INCOMPLETE")

                for kennung in reihenfolge:
                    if kennung in fertige_aufrufe:
                        continue
                    call = fertiger_aufruf(kennung)
                    fertige_aufrufe.add(kennung)
                    usage.tool_calls.append(call)
                    yield StreamChunk("tool_ready", tool_call=call)

            except AiProviderRequestError:
                raise
            except Exception as exc:
                logger.warning("OpenAI Responses WS unexpected error: %s", exc)
                await self.close()
                raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc
