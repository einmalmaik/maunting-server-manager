"""Kleiner OpenAI-kompatibler Streaming-Adapter auf Basis von httpx."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, AsyncIterator

import httpx

from models import AiProvider
from services.ai_provider_service import assert_provider_destination


logger = logging.getLogger(__name__)
MAX_STREAM_LINE_CHARS = 1_000_000
MAX_ASSISTANT_CHARS = 64_000
MAX_TOOL_ARGUMENT_CHARS = 128_000


class AiProviderRequestError(RuntimeError):
    """Stabiler, secret-freier Providerfehler fuer den API-Rand."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class StreamUsage:
    total_tokens: int | None = None
    output_chars: int = 0
    tool_calls: list["ProviderToolCall"] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: dict


def _error_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "AI_PROVIDER_AUTH_FAILED"
    if status_code == 429:
        return "AI_PROVIDER_RATE_LIMITED"
    if status_code >= 500:
        return "AI_PROVIDER_UNAVAILABLE"
    return "AI_PROVIDER_REQUEST_REJECTED"


async def stream_chat_completion(
    client: httpx.AsyncClient,
    *,
    provider: AiProvider,
    api_key: str | None,
    messages: list[dict[str, Any]],
    usage: StreamUsage,
    tools: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Normalisiert Provider-SSE zu reinen Text-Deltas.

    Providerframes, Response-Bodies und URLs verlassen diese Schicht nie. In
    Tool-Calls werden nur strukturell normalisiert. Ob ein Tool erlaubt ist
    und ob daraus lediglich ein Vorschlag entsteht, entscheidet die interne
    AI-Aktionsschicht; Providerdaten loesen hier niemals Aktionen aus.
    """
    assert_provider_destination(provider)
    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request_body = {
        "model": provider.default_model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        request_body["tools"] = tools
        request_body["tool_choice"] = "auto"
    target = provider.base_url.rstrip("/") + "/chat/completions"
    try:
        async with client.stream(
            "POST",
            target,
            headers=headers,
            json=request_body,
        ) as response:
            if response.status_code != 200:
                logger.warning(
                    "AI provider request failed provider_id=%s status=%s",
                    provider.id,
                    response.status_code,
                )
                raise AiProviderRequestError(_error_code(response.status_code))

            saw_done = False
            tool_buffers: dict[int, dict[str, str]] = {}
            async for line in response.aiter_lines():
                if not line:
                    continue
                if len(line) > MAX_STREAM_LINE_CHARS:
                    raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    saw_done = True
                    break
                try:
                    frame = json.loads(payload)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
                frame_usage = frame.get("usage")
                if isinstance(frame_usage, dict) and isinstance(frame_usage.get("total_tokens"), int):
                    usage.total_tokens = max(0, frame_usage["total_tokens"])
                choices = frame.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                tool_deltas = delta.get("tool_calls") if isinstance(delta, dict) else None
                if isinstance(tool_deltas, list):
                    for item in tool_deltas:
                        if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                            raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                        buffer = tool_buffers.setdefault(
                            item["index"], {"id": "", "name": "", "arguments": ""}
                        )
                        if isinstance(item.get("id"), str):
                            buffer["id"] += item["id"]
                        function = item.get("function")
                        if isinstance(function, dict):
                            if isinstance(function.get("name"), str):
                                buffer["name"] += function["name"]
                            if isinstance(function.get("arguments"), str):
                                buffer["arguments"] += function["arguments"]
                                if len(buffer["arguments"]) > MAX_TOOL_ARGUMENT_CHARS:
                                    raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                content = delta.get("content") if isinstance(delta, dict) else None
                if not isinstance(content, str) or not content:
                    continue
                usage.output_chars += len(content)
                if usage.output_chars > MAX_ASSISTANT_CHARS:
                    raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                yield content
            if not saw_done:
                raise AiProviderRequestError("AI_PROVIDER_STREAM_INCOMPLETE")
            for index in sorted(tool_buffers):
                item = tool_buffers[index]
                if not item["id"] or not item["name"]:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                try:
                    arguments = json.loads(item["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
                if not isinstance(arguments, dict):
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                usage.tool_calls.append(ProviderToolCall(
                    id=item["id"], name=item["name"], arguments=arguments
                ))
    except AiProviderRequestError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.warning(
            "AI provider network failure provider_id=%s error=%s",
            provider.id,
            type(exc).__name__,
        )
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "AI provider HTTP failure provider_id=%s error=%s",
            provider.id,
            type(exc).__name__,
        )
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE") from exc
