"""OpenAIs Responses-API — derselbe Auftrag, ein anderer Dialekt.

Diese Datei ist der native Chat- und Tool-Weg für OpenAI direkt (`/v1/responses`
sowie WebSocket `wss://api.openai.com/v1/responses`).

Unterstützt die volle OpenAI Feature-Suite:
- Responses API (`/v1/responses`) und persistenter WebSocket-Modus (`wss://...`)
- Stateful Multi-Turn Chaining via `previous_response_id`
- Stream-Multiplexing via `stream_id`
- OpenAI Background Mode (`background: True`, Status-Polling, Event-Streaming)
- Native File-Inputs (`input_file` für Logs, Konfigurationen, Anhänge)
- Server-seitige Context Compaction (`compaction: True`)
- Transparentes Fallback auf HTTP SSE und bestehende Chat-Completions-Pipelines
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator

import httpx

from services import ai_provider_registry
from services.ai_provider_service import base_url as provider_base_url
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
    _error_code,
    _error_detail,
    _ganzzahl,
    _iter_sse_lines,
    _kurzfassung,
    _teilmenge,
    schluesselkopf,
)
from services.openai_responses_websocket import (
    OpenAiResponsesWsSession,
    stream_responses_ws,
    ws_url_fuer_base_url,
)


logger = logging.getLogger(__name__)

_FEHLERARTEN = {
    "rate_limit_exceeded": "AI_PROVIDER_RATE_LIMITED",
    "insufficient_quota": "AI_PROVIDER_PAYMENT_REQUIRED",
    "billing_hard_limit_reached": "AI_PROVIDER_PAYMENT_REQUIRED",
    "invalid_api_key": "AI_PROVIDER_AUTH_FAILED",
    "authentication_error": "AI_PROVIDER_AUTH_FAILED",
    "model_not_found": "AI_PROVIDER_ENDPOINT_NOT_FOUND",
    "server_error": "AI_PROVIDER_UNAVAILABLE",
}

_TEXT_ATTACHMENT_HEADER = re.compile(
    r"^Unvertrauenswuerdiger Textanhang\s+([^\n:]+):\n(.*)$", re.DOTALL
)


# ── Uebersetzung: MSMs Verlauf in OpenAIs `input` ─────────────────────


def _werkzeuge_uebersetzen(tools: list[dict] | None) -> list[dict] | None:
    """Werkzeugkatalog aus der Chat-Completions-Form in die flache Form."""
    if not tools:
        return None
    flach: list[dict] = []
    for eintrag in tools:
        if not isinstance(eintrag, dict):
            continue
        funktion = eintrag.get("function")
        if not isinstance(funktion, dict):
            flach.append(eintrag)
            continue
        flach.append({
            "type": "function",
            "name": funktion.get("name"),
            "description": funktion.get("description"),
            "parameters": funktion.get("parameters"),
        })
    return flach or None


def _werkzeugwahl_uebersetzen(tool_choice: str | dict | None) -> str | dict:
    """``tool_choice`` in die flache Form."""
    if isinstance(tool_choice, dict):
        funktion = tool_choice.get("function")
        name = (
            funktion.get("name") if isinstance(funktion, dict)
            else tool_choice.get("name")
        )
        if isinstance(name, str) and name:
            return {"type": "function", "name": name}
        return "auto"
    return tool_choice or "auto"


def _text_aus_inhalt(content: Any) -> str:
    """Der Textanteil einer Nachricht, gleich in welcher Form er ankommt."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        stuecke: list[str] = []
        for block in content:
            if isinstance(block, str):
                stuecke.append(block)
            elif isinstance(block, dict):
                if block.get("type") in ("text", "input_text"):
                    wert = block.get("text")
                    if isinstance(wert, str):
                        stuecke.append(wert)
        return "".join(stuecke)
    return ""


def _bilder_aus_inhalt(content: Any) -> list[str]:
    """Die Bild-Adressen einer Nachricht, in der Reihenfolge, in der sie stehen."""
    if not isinstance(content, list):
        return []
    adressen: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in ("image_url", "input_image"):
            continue
        quelle = block.get("image_url")
        adresse = quelle.get("url") if isinstance(quelle, dict) else quelle
        if isinstance(adresse, str) and adresse:
            adressen.append(adresse)
    return adressen


def _dateien_aus_inhalt(content: Any) -> list[dict[str, Any]]:
    """Native Datei-Eingaben (`input_file`) aus der Nachricht extrahieren."""
    if not isinstance(content, list):
        return []
    dateien: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        typ = block.get("type")
        if typ in ("input_file", "file"):
            eintrag: dict[str, Any] = {
                "type": "input_file",
                "filename": block.get("filename") or block.get("name") or "attachment.txt",
                "media_type": block.get("media_type") or block.get("mime_type") or "text/plain",
            }
            if block.get("file_id"):
                eintrag["file_id"] = block["file_id"]
            if block.get("content") is not None:
                eintrag["content"] = str(block.get("content"))
            elif block.get("text") is not None:
                eintrag["content"] = str(block.get("text"))
            dateien.append(eintrag)
    return dateien


def nachrichten_uebersetzen(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MSMs Verlauf in OpenAIs ``input``-Liste, inkl. nativem File-Input Support."""
    eingabe: list[dict[str, Any]] = []
    for nachricht in messages:
        if not isinstance(nachricht, dict):
            continue
        rolle = nachricht.get("role")
        roh_inhalt = nachricht.get("content")
        inhalt = _text_aus_inhalt(roh_inhalt)

        if rolle == "tool":
            eingabe.append({
                "type": "function_call_output",
                "call_id": nachricht.get("tool_call_id"),
                "output": inhalt,
            })
            continue

        if rolle == "assistant":
            aufrufe = nachricht.get("tool_calls")
            if inhalt:
                eingabe.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": inhalt}],
                })
            if isinstance(aufrufe, list):
                for aufruf in aufrufe:
                    if not isinstance(aufruf, dict):
                        continue
                    funktion = aufruf.get("function")
                    if not isinstance(funktion, dict):
                        continue
                    eingabe.append({
                        "type": "function_call",
                        "call_id": aufruf.get("id"),
                        "name": funktion.get("name"),
                        "arguments": funktion.get("arguments") or "{}",
                    })
            continue

        if rolle in ("system", "user", "developer"):
            bilder = _bilder_aus_inhalt(roh_inhalt)
            dateien = _dateien_aus_inhalt(roh_inhalt)

            # Prüfe, ob es sich um einen formatierten Textanhang handelt
            match = _TEXT_ATTACHMENT_HEADER.match(inhalt) if isinstance(roh_inhalt, str) else None
            if match and not dateien:
                dateiname = match.group(1).strip()
                dateitext = match.group(2)
                teile: list[dict[str, Any]] = [
                    {
                        "type": "input_file",
                        "filename": dateiname,
                        "content": dateitext,
                        "media_type": "text/plain",
                    }
                ]
                eingabe.append({"role": rolle, "content": teile})
                continue

            if not bilder and not dateien:
                eingabe.append({"role": rolle, "content": inhalt})
                continue

            teile = []
            if inhalt and not (dateien and not any(b.get("type") in ("text", "input_text") for b in (roh_inhalt if isinstance(roh_inhalt, list) else []))):
                teile.append({"type": "input_text", "text": inhalt})
            for adresse in bilder:
                teile.append({
                    "type": "input_image", "image_url": adresse, "detail": "auto",
                })
            for datei in dateien:
                teile.append(datei)
            eingabe.append({"role": rolle, "content": teile})
    return eingabe


def nachrichten_fuer_fortsetzung(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extrahiert nur die neuen Deltas für Turn-Chaining mit `previous_response_id`.

    Bei Werkzeug-Folgerunden enthält die Fortsetzung nur die neuen `tool`
    (function_call_output) bzw. `user` Nachrichten nach der letzten Assistenten-
    Antwort, sodass historische Tokens auf dem OpenAI-Server verbleiben.
    """
    if not messages:
        return []

    # Finde die Positionen ab der letzten Assistenten-Nachricht
    letzte_assistent_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            letzte_assistent_idx = idx
            break

    if letzte_assistent_idx >= 0 and letzte_assistent_idx < len(messages) - 1:
        neue_nachrichten = messages[letzte_assistent_idx + 1:]
        uebersetzt = nachrichten_uebersetzen(neue_nachrichten)
        if uebersetzt:
            return uebersetzt

    return nachrichten_uebersetzen(messages)


# ── Der Strom ─────────────────────────────────────────────────────────


def _fehler_im_ereignis(rahmen: dict) -> tuple[str, str | None] | None:
    """Ein Fehler, der im Strom gemeldet wird statt als Status."""
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
            if isinstance(code, str):
                marke = _FEHLERARTEN.get(code, marke)
        return marke, _kurzfassung(nachricht or str(typ))
    return None


def _usage_uebernehmen(usage: StreamUsage, rohdaten: Any) -> None:
    """Die Abrechnung aus ``response.completed``."""
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


async def stream_responses_http(
    client: httpx.AsyncClient,
    *,
    provider: Any,
    api_key: str | None,
    messages: list[dict[str, Any]],
    usage: StreamUsage,
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    reasoning: bool = False,
    reasoning_effort: str | None = None,
    cache_marke: bool = False,
    previous_response_id: str | None = None,
    compaction: bool = False,
) -> AsyncIterator[StreamChunk]:
    """Ein Chatzug über HTTP SSE ``POST /v1/responses``."""
    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    headers = schluesselkopf(
        ai_provider_registry.anbieter(provider.provider_kind), api_key
    )
    headers["OpenAI-Beta"] = "responses=v1"

    # Bei Vorhandensein von previous_response_id nur Deltas senden
    input_items = (
        nachrichten_fuer_fortsetzung(messages)
        if previous_response_id
        else nachrichten_uebersetzen(messages)
    )

    request_body: dict[str, Any] = {
        "model": model or provider.default_model,
        "input": input_items,
        "stream": True,
        "store": False,
    }
    if previous_response_id:
        request_body["previous_response_id"] = previous_response_id
    if compaction:
        request_body["compaction"] = True

    flache_werkzeuge = _werkzeuge_uebersetzen(tools)
    if flache_werkzeuge:
        request_body["tools"] = flache_werkzeuge
        request_body["tool_choice"] = _werkzeugwahl_uebersetzen(tool_choice)
    if reasoning and reasoning_effort:
        request_body["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    elif reasoning_effort:
        request_body["reasoning"] = {"effort": reasoning_effort}

    target = httpx.URL(provider_base_url(provider).rstrip("/") + "/responses")
    deadline = time.monotonic() + MAX_STREAM_SECONDS
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

    try:
        async with client.stream(
            "POST", target, headers=headers, json=request_body
        ) as response:
            if response.status_code != 200:
                detail = await _error_detail(response)
                logger.warning(
                    "AI provider request failed provider_id=%s model=%s status=%s",
                    provider.id,
                    model or provider.default_model,
                    response.status_code,
                )
                raise AiProviderRequestError(_error_code(response.status_code), detail)

            usage.anfragen += 1

            async for line in _iter_sse_lines(response, deadline=deadline):
                if time.monotonic() > deadline:
                    raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT")
                if not line or not line.startswith("data:"):
                    continue
                frames += 1
                if frames > MAX_STREAM_FRAMES:
                    raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                nutzlast = line[5:].strip()
                if nutzlast == "[DONE]":
                    break
                try:
                    rahmen = json.loads(nutzlast)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
                if not isinstance(rahmen, dict):
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")

                if (gemeldet := _fehler_im_ereignis(rahmen)) is not None:
                    marke, text = gemeldet
                    logger.warning(
                        "AI provider stream error provider_id=%s model=%s code=%s",
                        provider.id, model or provider.default_model, marke,
                    )
                    raise AiProviderRequestError(marke, text)

                typ = rahmen.get("type")

                if typ == "response.created":
                    resp_obj = rahmen.get("response")
                    if isinstance(resp_obj, dict):
                        resp_id = resp_obj.get("id")
                        stream_id = resp_obj.get("stream_id")
                        if isinstance(resp_id, str):
                            usage.response_id = resp_id
                        if isinstance(stream_id, str):
                            usage.stream_id = stream_id

                elif typ == "response.output_text.delta":
                    stueck = rahmen.get("delta")
                    if not isinstance(stueck, str) or not stueck:
                        continue
                    usage.output_chars += len(stueck)
                    if usage.output_chars > MAX_ASSISTANT_CHARS:
                        raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                    yield StreamChunk("content", stueck)

                elif typ == "response.reasoning_summary_text.delta":
                    gedanke = rahmen.get("delta")
                    if not isinstance(gedanke, str) or not gedanke:
                        continue
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
                            usage.response_id = resp_id
                        _usage_uebernehmen(usage, antwort.get("usage"))

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
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.warning(
            "AI provider network failure provider_id=%s error=%s",
            provider.id, type(exc).__name__,
        )
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "AI provider HTTP failure provider_id=%s error=%s",
            provider.id, type(exc).__name__,
        )
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc


async def stream_responses(
    client: httpx.AsyncClient,
    *,
    provider: Any,
    api_key: str | None,
    messages: list[dict[str, Any]],
    usage: StreamUsage,
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    reasoning: bool = False,
    reasoning_effort: str | None = None,
    cache_marke: bool = False,
    previous_response_id: str | None = None,
    use_websocket: bool = True,
    compaction: bool = False,
    background: bool = False,
    ws_session: OpenAiResponsesWsSession | None = None,
    **kwargs: Any,
) -> AsyncIterator[StreamChunk]:
    """Zentraler Einstiegspunkt für den OpenAI Responses-Weg (WebSocket / HTTP / Background)."""
    if background:
        async for chunk in stream_background_response(
            client,
            provider=provider,
            api_key=api_key,
            messages=messages,
            usage=usage,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
            previous_response_id=previous_response_id,
            compaction=compaction,
        ):
            yield chunk
        return

    # WebSocket-Pfad versuchen, wenn aktiviert und passend
    if use_websocket and provider.provider_kind == "openai":
        try:
            ws_url = ws_url_fuer_base_url(provider_base_url(provider))
            headers = schluesselkopf(
                ai_provider_registry.anbieter(provider.provider_kind), api_key
            )
            headers["OpenAI-Beta"] = "responses=v1"

            input_items = (
                nachrichten_fuer_fortsetzung(messages)
                if previous_response_id
                else nachrichten_uebersetzen(messages)
            )

            payload: dict[str, Any] = {
                "model": model or provider.default_model,
                "input": input_items,
                "stream": True,
                "store": False,
            }
            if previous_response_id:
                payload["previous_response_id"] = previous_response_id
            if compaction:
                payload["compaction"] = True

            flache_werkzeuge = _werkzeuge_uebersetzen(tools)
            if flache_werkzeuge:
                payload["tools"] = flache_werkzeuge
                payload["tool_choice"] = _werkzeugwahl_uebersetzen(tool_choice)
            if reasoning and reasoning_effort:
                payload["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
            elif reasoning_effort:
                payload["reasoning"] = {"effort": reasoning_effort}

            if ws_session is not None:
                async for chunk in ws_session.stream_turn(payload, usage):
                    yield chunk
                return

            async for chunk in stream_responses_ws(
                ws_url, headers=headers, payload=payload, usage=usage
            ):
                yield chunk
            return
        except Exception as exc:
            logger.info(
                "WebSocket stream unavailable/interrupted (%s), transparently falling back to HTTP SSE",
                exc,
            )

    # Fallback auf HTTP SSE
    try:
        async for chunk in stream_responses_http(
            client,
            provider=provider,
            api_key=api_key,
            messages=messages,
            usage=usage,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
            cache_marke=cache_marke,
            previous_response_id=previous_response_id,
            compaction=compaction,
        ):
            yield chunk
    except AiProviderRequestError as exc:
        # Falls Chaining wegen abgelaufener previous_response_id fehlschlägt: Retry mit vollem Kontext
        if previous_response_id and exc.code in ("AI_PROVIDER_REQUEST_REJECTED", "AI_PROVIDER_ENDPOINT_NOT_FOUND"):
            logger.info("Retrying turn without previous_response_id after chaining rejection")
            async for chunk in stream_responses_http(
                client,
                provider=provider,
                api_key=api_key,
                messages=messages,
                usage=usage,
                model=model,
                tools=tools,
                tool_choice=tool_choice,
                reasoning=reasoning,
                reasoning_effort=reasoning_effort,
                cache_marke=cache_marke,
                previous_response_id=None,
                compaction=compaction,
            ):
                yield chunk
        else:
            raise


# ── OpenAI Background Mode ───────────────────────────────────────────


async def create_background_response(
    client: httpx.AsyncClient,
    *,
    provider: Any,
    api_key: str | None,
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    reasoning: bool = False,
    reasoning_effort: str | None = None,
    previous_response_id: str | None = None,
    compaction: bool = False,
) -> dict[str, Any]:
    """Startet eine asynchrone Hintergrund-Operation (`background: True`)."""
    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    headers = schluesselkopf(
        ai_provider_registry.anbieter(provider.provider_kind), api_key
    )
    headers["OpenAI-Beta"] = "responses=v1"

    request_body: dict[str, Any] = {
        "model": model or provider.default_model,
        "input": (
            nachrichten_fuer_fortsetzung(messages)
            if previous_response_id
            else nachrichten_uebersetzen(messages)
        ),
        "background": True,
        "stream": False,
        "store": False,
    }
    if previous_response_id:
        request_body["previous_response_id"] = previous_response_id
    if compaction:
        request_body["compaction"] = True

    flache_werkzeuge = _werkzeuge_uebersetzen(tools)
    if flache_werkzeuge:
        request_body["tools"] = flache_werkzeuge
        request_body["tool_choice"] = _werkzeugwahl_uebersetzen(tool_choice)
    if reasoning and reasoning_effort:
        request_body["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    elif reasoning_effort:
        request_body["reasoning"] = {"effort": reasoning_effort}

    target = httpx.URL(provider_base_url(provider).rstrip("/") + "/responses")
    try:
        response = await client.post(target, headers=headers, json=request_body, timeout=30.0)
        if response.status_code not in (200, 202):
            detail = await _error_detail(response)
            raise AiProviderRequestError(_error_code(response.status_code), detail)
        return response.json()
    except AiProviderRequestError:
        raise
    except Exception as exc:
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc


async def get_background_response(
    client: httpx.AsyncClient,
    *,
    provider: Any,
    api_key: str | None,
    response_id: str,
) -> dict[str, Any]:
    """Fragt den Status eines Hintergrund-Auftrags ab."""
    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    headers = schluesselkopf(
        ai_provider_registry.anbieter(provider.provider_kind), api_key
    )
    headers["OpenAI-Beta"] = "responses=v1"
    target = httpx.URL(f"{provider_base_url(provider).rstrip('/')}/responses/{response_id}")
    try:
        response = await client.get(target, headers=headers, timeout=30.0)
        if response.status_code != 200:
            detail = await _error_detail(response)
            raise AiProviderRequestError(_error_code(response.status_code), detail)
        return response.json()
    except AiProviderRequestError:
        raise
    except Exception as exc:
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc


async def cancel_background_response(
    client: httpx.AsyncClient,
    *,
    provider: Any,
    api_key: str | None,
    response_id: str,
) -> dict[str, Any]:
    """Bricht einen laufenden Hintergrund-Auftrag ab."""
    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    headers = schluesselkopf(
        ai_provider_registry.anbieter(provider.provider_kind), api_key
    )
    headers["OpenAI-Beta"] = "responses=v1"
    target = httpx.URL(f"{provider_base_url(provider).rstrip('/')}/responses/{response_id}/cancel")
    try:
        response = await client.post(target, headers=headers, timeout=30.0)
        if response.status_code != 200:
            detail = await _error_detail(response)
            raise AiProviderRequestError(_error_code(response.status_code), detail)
        return response.json()
    except AiProviderRequestError:
        raise
    except Exception as exc:
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc


async def poll_background_response(
    client: httpx.AsyncClient,
    *,
    provider: Any,
    api_key: str | None,
    response_id: str,
    poll_interval: float = 0.5,
    timeout: float = MAX_STREAM_SECONDS,
) -> dict[str, Any]:
    """Pollt den Hintergrundauftrag bis zur Fertigstellung."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        daten = await get_background_response(
            client, provider=provider, api_key=api_key, response_id=response_id
        )
        status = daten.get("status")
        if status == "completed":
            return daten
        if status in ("failed", "cancelled", "incomplete"):
            fehler = daten.get("error") or {}
            nachricht = fehler.get("message") or f"Background response {status}"
            code = fehler.get("code")
            marke = _FEHLERARTEN.get(code, "AI_PROVIDER_REQUEST_REJECTED") if isinstance(code, str) else "AI_PROVIDER_REQUEST_REJECTED"
            raise AiProviderRequestError(marke, _kurzfassung(nachricht))

        await asyncio.sleep(poll_interval)

    raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT")


async def stream_background_response(
    client: httpx.AsyncClient,
    *,
    provider: Any,
    api_key: str | None,
    messages: list[dict[str, Any]],
    usage: StreamUsage,
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    reasoning: bool = False,
    reasoning_effort: str | None = None,
    previous_response_id: str | None = None,
    compaction: bool = False,
    poll_interval: float = 0.5,
    timeout: float = MAX_STREAM_SECONDS,
) -> AsyncIterator[StreamChunk]:
    """Startet und pollt einen Background-Auftrag und liefert die Resultate als StreamChunk."""
    init_res = await create_background_response(
        client,
        provider=provider,
        api_key=api_key,
        messages=messages,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
        reasoning=reasoning,
        reasoning_effort=reasoning_effort,
        previous_response_id=previous_response_id,
        compaction=compaction,
    )
    usage.anfragen += 1
    resp_id = init_res.get("id")
    if not isinstance(resp_id, str):
        raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")

    usage.response_id = resp_id

    final_res = await poll_background_response(
        client,
        provider=provider,
        api_key=api_key,
        response_id=resp_id,
        poll_interval=poll_interval,
        timeout=timeout,
    )

    _usage_uebernehmen(usage, final_res.get("usage"))

    # Output Items extrahieren
    output_items = final_res.get("output") or []
    if isinstance(output_items, list):
        for item in output_items:
            if not isinstance(item, dict):
                continue
            typ = item.get("type")
            if typ == "reasoning_summary_text":
                text = item.get("text") or ""
                if text:
                    usage.reasoning_chars += len(text)
                    yield StreamChunk("reasoning", text)
            elif typ == "output_text":
                text = item.get("text") or ""
                if text:
                    usage.output_chars += len(text)
                    yield StreamChunk("content", text)
            elif typ == "function_call":
                name = item.get("name") or ""
                call_id = item.get("call_id") or item.get("id") or ""
                raw_args = item.get("arguments") or "{}"
                args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                call = ProviderToolCall(id=call_id, name=name, arguments=args_dict)
                usage.tool_calls.append(call)
                yield StreamChunk("tool_ready", tool_call=call)


def spricht_responses(provider) -> bool:
    """Ob dieser Zugang den Responses-Weg spricht."""
    try:
        return (
            ai_provider_registry.anbieter(provider.provider_kind).protokoll_chat
            == "responses"
        )
    except KeyError:
        return False
