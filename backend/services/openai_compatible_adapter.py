"""Kleiner OpenAI-kompatibler Streaming-Adapter auf Basis von httpx."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

from models import AiProvider
from services.ai_provider_service import base_url as provider_base_url
from services.ai_redaction import redact_sensitive_text


logger = logging.getLogger(__name__)
MAX_STREAM_LINE_CHARS = 1_000_000
MAX_ASSISTANT_CHARS = 64_000
MAX_TOOL_ARGUMENT_CHARS = 128_000
# Harte Obergrenzen fuer einen einzelnen Providerstream. Ohne sie haelt ein
# langsam tropfender Provider die Kontingentreservierung und einen
# Nebenlaeufigkeitsplatz unbegrenzt besetzt: der Lesetimeout von httpx greift
# nur je Chunk, nicht fuer die Gesamtdauer.
MAX_STREAM_SECONDS = 300.0
MAX_STREAM_FRAMES = 20_000
# Fremdtext aus einem Fehler-Body. Bewusst knapp: er soll die Ursache benennen,
# nicht eine fremde Seite in unsere Oberflaeche kopieren.
MAX_PROVIDER_ERROR_BODY_BYTES = 4_096
MAX_PROVIDER_DETAIL_CHARS = 200
# Denkschritte koennen laenger werden als die Antwort selbst. Eigene Grenze,
# damit ein endlos gruebelndes Modell nicht den Nachrichtenspeicher fuellt.
MAX_REASONING_CHARS = 32_000


class AiProviderRequestError(RuntimeError):
    """Stabiler, secret-freier Providerfehler fuer den API-Rand.

    ``detail`` ist eine stark gekuerzte, redigierte Fehlermeldung des Anbieters.
    Ohne sie war jede Fehlkonfiguration im Panel dieselbe Sackgasse: eine falsche
    Basis-URL, ein Tippfehler im Modellnamen und ein abgelaufener Key ergaben
    alle dieselbe Meldung "Der KI-Anbieter hat die Anfrage abgelehnt". Die
    Anbietermeldung sagt dagegen genau, was fehlt ("No endpoints found for
    openrouter-free").

    Der Text stammt von aussen und wird deshalb wie jeder Fremdtext behandelt:
    redigiert, einzeilig und hart auf ``MAX_PROVIDER_DETAIL_CHARS`` gekuerzt.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass
class StreamUsage:
    total_tokens: int | None = None
    output_chars: int = 0
    tool_calls: list["ProviderToolCall"] = field(default_factory=list)
    # Gesammelte Denkschritte des Modells. Getrennt von der Antwort, weil sie
    # etwas anderes sind: eine Nebenausgabe, die der Benutzer aufklappen kann,
    # aber die nie als Aussage des Panels gelesen werden darf.
    reasoning_chars: int = 0


@dataclass(frozen=True)
class StreamChunk:
    """Ein Stueck Providerausgabe — entweder Antwort oder Denkschritt."""

    kind: str  # "content" | "reasoning"
    text: str


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: dict


async def _iter_sse_lines(
    response: httpx.Response, *, deadline: float
) -> AsyncIterator[str]:
    """Zerlegt die Providerantwort in Zeilen mit harter Puffergrenze.

    `response.aiter_lines()` puffert eine Zeile unbegrenzt, bevor sie
    zurueckkommt. Ein Provider, der nie einen Zeilenumbruch sendet, koennte den
    Panel-Prozess damit in den Speicher treiben, ohne dass die nachgelagerte
    Laengenpruefung je erreicht wird. Deshalb wird hier selbst gepuffert und
    sowohl die Puffergroesse als auch die Gesamtlaufzeit begrenzt.
    """
    buffer = ""
    async for chunk in response.aiter_text():
        if time.monotonic() > deadline:
            raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT")
        buffer += chunk
        if len(buffer) > MAX_STREAM_LINE_CHARS:
            raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield line.rstrip("\r")
    if buffer:
        yield buffer.rstrip("\r")


def _error_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "AI_PROVIDER_AUTH_FAILED"
    if status_code == 404:
        # Getrennt von 400: ein 404 heisst so gut wie immer, dass die Basis-URL
        # oder der Modellname nicht existiert. Das ist eine andere Handlung fuer
        # den Betreiber als eine inhaltlich abgelehnte Anfrage.
        return "AI_PROVIDER_ENDPOINT_NOT_FOUND"
    if status_code == 429:
        return "AI_PROVIDER_RATE_LIMITED"
    if status_code >= 500:
        return "AI_PROVIDER_UNAVAILABLE"
    return "AI_PROVIDER_REQUEST_REJECTED"


async def _error_detail(response: httpx.Response) -> str | None:
    """Zieht die Fehlermeldung des Anbieters aus einem Fehler-Body.

    Der Body wird nur bei einem Fehlerstatus gelesen und nie gestreamt. Alles
    daran ist Fremdtext: er wird redigiert, auf eine Zeile gebracht und gekuerzt.
    """
    try:
        raw = await response.aread()
    except (httpx.HTTPError, RuntimeError):
        return None
    text = raw[: MAX_PROVIDER_ERROR_BODY_BYTES].decode("utf-8", "replace")
    message: str | None = None
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        message = text
    else:
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = error["message"]
            elif isinstance(error, str):
                message = error
            elif isinstance(parsed.get("message"), str):
                message = parsed["message"]
        if message is None:
            message = text
    single_line = " ".join(redact_sensitive_text(message).split())
    return single_line[:MAX_PROVIDER_DETAIL_CHARS] or None


async def stream_chat_completion(
    client: httpx.AsyncClient,
    *,
    provider: AiProvider,
    api_key: str | None,
    messages: list[dict[str, Any]],
    usage: StreamUsage,
    tools: list[dict] | None = None,
    reasoning: bool = False,
    reasoning_effort: str | None = None,
    cache_marke: bool = False,
) -> AsyncIterator[StreamChunk]:
    """Normalisiert Provider-SSE zu Antwort- und Denkschritt-Stuecken.

    Providerframes, Response-Bodies und URLs verlassen diese Schicht nie. In
    Tool-Calls werden nur strukturell normalisiert. Ob ein Tool erlaubt ist
    und ob daraus lediglich ein Vorschlag entsteht, entscheidet die interne
    AI-Aktionsschicht; Providerdaten loesen hier niemals Aktionen aus.

    ``reasoning`` steuert das Nachdenken. Der Schalter ist absichtlich
    generisch: gesendet wird ``{"reasoning": {"enabled": ...}}``, gelesen werden
    ``delta.reasoning`` und ``delta.reasoning_content``. Das erste Feld nutzt
    OpenRouter, das zweite die meisten OpenAI-kompatiblen Server (vLLM,
    DeepSeek, Ollama). Ein Anbieter, der beides nicht kennt, ignoriert das
    zusaetzliche Feld.

    **Das Feld geht in beide Richtungen mit, auch bei ``False``.** Vorher wurde
    es nur bei ``True`` gesendet — bei „aus“ ging gar nichts hinaus, und das ist
    nicht dasselbe. Die Mehrheit der aktuellen Modelle denkt von sich aus:
    OpenRouter meldet fuer Claude Opus 5, Sonnet 5 und Gemini 3.5 Flash
    ``default_enabled: true``, und OpenAI setzt ab GPT-5.5 den Default auf
    ``medium``. Ohne ausdrueckliches ``enabled: false`` dachte das Modell also
    weiter und wurde abgerechnet — der Schalter blendete nur die Denkschritte
    aus. Fuer ein Panel mit Kostenlimits je Rolle ist das die falsche
    Voreinstellung; ein Kostenschalter darf sich nicht auf Anbieterdefaults
    verlassen.

    ``cache_marke`` laesst den Anbieter den Prompt zwischenspeichern. Gesendet
    wird das **oberste** ``cache_control`` neben ``model`` und ``messages``, nicht
    eine Marke mitten in einer Nachricht. Der Unterschied ist der ganze Grund,
    warum das hier eine Zeile ist und kein Umbau: die oberste Form setzt die
    Marke selbst an den letzten wiederverwendbaren Block und schiebt sie mit dem
    Gespraech weiter. Marken je Nachricht haetten dagegen verlangt, dass diese
    Schicht weiss, welcher Teil des Kontexts stabil ist — und das weiss sie
    nicht, das weiss `ai_context_service`.

    Gesendet wird sie **nur**, wenn der Katalog dieses Modell als „verlangt eine
    ausdrueckliche Marke“ fuehrt (``Modell.cache_marke_noetig``). Der Rest
    speichert entweder von selbst zwischen oder gar nicht; in beiden Faellen ist
    das Feld ueberfluessig. Anders als bei ``reasoning`` ist Weglassen hier also
    richtig: es gibt keinen Anbieterdefault, der sich unbemerkt einschaltet und
    abgerechnet wird — die Voreinstellung ist ueberall „kein Zwischenspeicher“.

    Ohne ``ttl`` und damit die kurze Frist. Die lange (``"1h"``) kostet das
    Anlegen das Doppelte statt des 1,25-Fachen und traegt sich nur, wenn
    derselbe Prompt eine Stunde spaeter unveraendert wiederkommt. Innerhalb
    eines Laufs liegen die Runden Sekunden auseinander — dort zahlt die kurze
    Frist, und zwischen zwei Fragen eines Menschen ist beides unsicher.

    ``reasoning_effort`` ist die **Tiefe** — "minimal" bis "max", oder ``None``
    fuer Modelle, die keine Stufen kennen (gemessen 145 der 272 denkenden
    Modelle bei OpenRouter). Zwei Felder statt eines, weil die Anbieter selbst
    zwei Dinge kennen; das Wort geht unveraendert hinaus, denn es stammt aus dem
    Katalog desselben Anbieters. Geklemmt wurde vorher in
    `services/ai_reasoning.klemmen` — diese Schicht entscheidet nichts, sie
    sendet.

    **Kein SSRF-Pinning mehr.** Hier stand eine Revalidierung des Ziels vor
    jedem Request, samt Festnageln auf die gepruefte IP und eigenem
    SNI-Hostnamen. Das war noetig, solange die Zieladresse aus einem Formular
    stammte. Sie kommt jetzt aus `ai_provider_registry`, also aus dem Programm —
    es gibt keine Eingabe mehr, die auf ein internes Netz zeigen koennte.
    """
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
    # Immer setzen, nie weglassen: "nichts senden" heisst beim Anbieter nicht
    # "aus", sondern "nimm deinen Default" — und der ist bei den meisten
    # aktuellen Modellen an.
    denken: dict[str, Any] = {"enabled": bool(reasoning)}
    # Die Stufe nur mitgeben, wenn auch gedacht werden soll. Ein `effort` neben
    # `enabled: false` sind zwei widerspruechliche Angaben in einer Anfrage —
    # welche gewinnt, entscheidet dann der Anbieter und nicht MSM.
    if reasoning and reasoning_effort:
        denken["effort"] = reasoning_effort
    request_body["reasoning"] = denken
    if cache_marke:
        request_body["cache_control"] = {"type": "ephemeral"}
    target = httpx.URL(provider_base_url(provider).rstrip("/") + "/chat/completions")
    extensions: dict[str, Any] = {}
    deadline = time.monotonic() + MAX_STREAM_SECONDS
    frames = 0
    try:
        async with client.stream(
            "POST",
            target,
            headers=headers,
            json=request_body,
            extensions=extensions,
        ) as response:
            if response.status_code != 200:
                detail = await _error_detail(response)
                logger.warning(
                    "AI provider request failed provider_id=%s status=%s",
                    provider.id,
                    response.status_code,
                )
                raise AiProviderRequestError(
                    _error_code(response.status_code), detail
                )

            saw_done = False
            tool_buffers: dict[int, dict[str, str]] = {}
            async for line in _iter_sse_lines(response, deadline=deadline):
                if time.monotonic() > deadline:
                    raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT")
                frames += 1
                if frames > MAX_STREAM_FRAMES:
                    raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                if not line:
                    continue
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
                if isinstance(delta, dict):
                    # `reasoning` ist OpenRouter, `reasoning_content` der in
                    # OpenAI-kompatiblen Servern verbreitete Name. Beide sind
                    # reiner Text; die strukturierte Variante
                    # (`reasoning_details`) wird bewusst nicht ausgewertet — sie
                    # ist anbieterspezifisch und der Textstrom reicht fuer die
                    # Anzeige vollstaendig aus.
                    thought = delta.get("reasoning")
                    if not isinstance(thought, str) or not thought:
                        thought = delta.get("reasoning_content")
                    if isinstance(thought, str) and thought:
                        usage.reasoning_chars += len(thought)
                        if usage.reasoning_chars <= MAX_REASONING_CHARS:
                            yield StreamChunk("reasoning", thought)

                content = delta.get("content") if isinstance(delta, dict) else None
                if not isinstance(content, str) or not content:
                    continue
                usage.output_chars += len(content)
                if usage.output_chars > MAX_ASSISTANT_CHARS:
                    raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                yield StreamChunk("content", content)
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
