"""Fehlkonfigurationen muessen unterscheidbar sein, und Denkschritte fliessen.

Anlass ist ein konkreter Ausfall: eine Anfrage im Chat brach mit
`ai.errors.provider` ab. Dieselbe Meldung erschien bei einer Basis-URL, die auf
`/chat/completions` endete, bei einem Tippfehler im Modellnamen und bei einem
abgelaufenen Key. Alle drei sind verschiedene Handlungen fuer den Betreiber, und
keiner davon war aus der Oberflaeche erkennbar.

**Der erste der drei Faelle ist entfallen** — mitsamt seinem Test
(`test_a_pasted_endpoint_path_is_normalized_away`). Er pruefte, dass eine
versehentlich mitkopierte Endpunkt-URL still abgeschnitten wird. Das war noetig,
solange der Betreiber die Adresse tippte; er waehlt jetzt einen Anbieter, und
die Adresse kommt aus `ai_provider_registry`. Ein Bedienfehler, den man nicht
mehr begehen kann, braucht keine Korrektur.
"""

from __future__ import annotations

import json

import httpx
import pytest

from models import AiProvider
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


def _provider() -> AiProvider:
    return AiProvider(
        id=1,
        name="Diagnose",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {"error": {"message": "User not found."}}, "AI_PROVIDER_AUTH_FAILED"),
        (404, {"error": {"message": "Not Found"}}, "AI_PROVIDER_ENDPOINT_NOT_FOUND"),
        (400, {"error": {"message": "xyz is not a valid model ID"}}, "AI_PROVIDER_REQUEST_REJECTED"),
        (429, {"error": {"message": "slow down"}}, "AI_PROVIDER_RATE_LIMITED"),
        (503, {"error": {"message": "upstream down"}}, "AI_PROVIDER_UNAVAILABLE"),
    ],
)
@pytest.mark.asyncio
async def test_each_failure_gets_its_own_code_and_the_provider_reason(
    status: int, body: dict, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    usage = StreamUsage()
    async with _client(handler) as client:
        with pytest.raises(AiProviderRequestError) as excinfo:
            async for _chunk in stream_chat_completion(
                client, provider=_provider(), api_key=None,
                messages=[{"role": "user", "content": "ping"}], usage=usage,
            ):
                pass

    assert excinfo.value.code == code
    # Die Anbietermeldung benennt die Ursache praeziser als jeder Code.
    assert excinfo.value.detail == body["error"]["message"]


@pytest.mark.asyncio
async def test_an_error_body_is_truncated_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Fehler-Body ist Fremdtext und darf weder wachsen noch leaken."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": (
            "api_key=sk-abcdefghijklmnopqrstuvwxyz012345 abgelehnt. " + "x" * 500
        )}})

    usage = StreamUsage()
    async with _client(handler) as client:
        with pytest.raises(AiProviderRequestError) as excinfo:
            async for _chunk in stream_chat_completion(
                client, provider=_provider(), api_key=None,
                messages=[{"role": "user", "content": "ping"}], usage=usage,
            ):
                pass

    detail = excinfo.value.detail or ""
    assert len(detail) <= 200
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in detail


@pytest.mark.asyncio
async def test_reasoning_is_requested_and_arrives_as_its_own_chunk_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Denkschritte sind eine Nebenausgabe, keine Antwort.

    Kaemen sie als `content` an, stuenden sie mitten im Antworttext und flossen
    ausserdem in jede Folgeanfrage zurueck.
    """
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        frames = (
            'data: {"choices":[{"delta":{"reasoning":"Ich pruefe die Ports."}}]}\n\n'
            'data: {"choices":[{"delta":{"reasoning_content":" Noch kurz."}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"Port 25565 ist offen."}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=frames)

    usage = StreamUsage()
    collected: list[tuple[str, str]] = []
    async with _client(handler) as client:
        async for chunk in stream_chat_completion(
            client, provider=_provider(), api_key=None,
            messages=[{"role": "user", "content": "Ports?"}], usage=usage,
            reasoning=True,
        ):
            collected.append((chunk.kind, chunk.text))

    assert sent["reasoning"] == {"enabled": True}
    assert collected == [
        ("reasoning", "Ich pruefe die Ports."),
        ("reasoning", " Noch kurz."),
        ("content", "Port 25565 ist offen."),
    ]
    assert usage.reasoning_chars == len("Ich pruefe die Ports.") + len(" Noch kurz.")


@pytest.mark.asyncio
async def test_reasoning_arrives_when_only_the_structured_field_is_filled() -> None:
    """Der Denkblock fehlte bei GPT-5-Modellen — der Text kam auf dem anderen Weg.

    Beobachtet mit `gpt-5.6-luna` auf Stufe „mittel": die Antwort brauchte
    auffaellig lange, und trotzdem stand kein aufklappbarer Block da. Der Grund
    liegt hier: diese Familie laesst `delta.reasoning` leer und legt ihre
    Ueberlegungen als `reasoning.summary` in `delta.reasoning_details`. MSM las
    nur das Klartextfeld und sah deshalb nichts.

    Der verschluesselte Eintrag geht bewusst nicht mit: er traegt keinen
    lesbaren Text, und eine Zeile ohne Aussage ist kein Denkschritt.
    """
    frames = (
        'data: {"choices":[{"delta":{"reasoning_details":['
        '{"type":"reasoning.summary","summary":"Ich sehe zuerst in die Logs.",'
        '"format":"openai-responses-v1","id":"rs_1"}]}}]}\n\n'
        'data: {"choices":[{"delta":{"reasoning_details":['
        '{"type":"reasoning.encrypted","data":"AAAA","format":"openai-responses-v1","id":"rs_2"}]}}]}\n\n'
        'data: {"choices":[{"delta":{"reasoning_details":['
        '{"type":"reasoning.text","text":" Dann pruefe ich den Port.","id":"rs_3"}]}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"Der Server laeuft."}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=frames)

    usage = StreamUsage()
    collected: list[tuple[str, str]] = []
    async with _client(handler) as client:
        async for chunk in stream_chat_completion(
            client, provider=_provider(), api_key=None,
            messages=[{"role": "user", "content": "Laeuft der Server?"}], usage=usage,
            reasoning=True, reasoning_effort="medium",
        ):
            collected.append((chunk.kind, chunk.text))

    assert collected == [
        ("reasoning", "Ich sehe zuerst in die Logs."),
        ("reasoning", " Dann pruefe ich den Port."),
        ("content", "Der Server laeuft."),
    ]


@pytest.mark.asyncio
async def test_the_plain_text_field_wins_over_the_structured_one() -> None:
    """Beide Felder in einem Frame heissen nicht zwei Denkschritte.

    OpenRouter fuellt bei manchen Routen beides mit **demselben** Text. Wer
    nacheinander liest statt zu waehlen, zeigt jeden Gedanken doppelt an.
    """
    frames = (
        'data: {"choices":[{"delta":{"reasoning":"Ich pruefe die Ports.",'
        '"reasoning_details":[{"type":"reasoning.text","text":"Ich pruefe die Ports."}]}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"Offen."}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=frames)

    usage = StreamUsage()
    collected: list[tuple[str, str]] = []
    async with _client(handler) as client:
        async for chunk in stream_chat_completion(
            client, provider=_provider(), api_key=None,
            messages=[{"role": "user", "content": "Ports?"}], usage=usage,
            reasoning=True,
        ):
            collected.append((chunk.kind, chunk.text))

    assert collected == [
        ("reasoning", "Ich pruefe die Ports."),
        ("content", "Offen."),
    ]
    assert usage.reasoning_chars == len("Ich pruefe die Ports.")


@pytest.mark.asyncio
async def test_switching_off_says_so_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """„Aus“ muss hinausgehen. Nichts zu senden heisst beim Anbieter nicht aus.

    Hier stand vorher die umgekehrte Zusicherung — ohne Schalter kein Feld —
    mit der Begruendung, ein Anbieter solle nicht mit Unbekanntem behelligt
    werden. Die Begruendung traegt nicht: wer das Feld nicht kennt, ignoriert es
    ohnehin, und wer es kennt, nimmt ohne Angabe **seinen** Default.

    Und der ist bei den aktuellen Modellen an. OpenRouter meldet fuer Claude
    Opus 5, Sonnet 5 und Gemini 3.5 Flash `default_enabled: true`, OpenAI setzt
    ab GPT-5.5 auf `medium`. Der ausgeschaltete Schalter hat also nicht das
    Nachdenken abgestellt, sondern nur seine Anzeige — bezahlt wurde es weiter.
    Fuer ein Panel, dessen Rollen ein `monthly_cost_limit_cents` tragen, ist das
    kein Schoenheitsfehler.
    """
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    usage = StreamUsage()
    async with _client(handler) as client:
        async for _chunk in stream_chat_completion(
            client, provider=_provider(), api_key=None,
            messages=[{"role": "user", "content": "ping"}], usage=usage,
        ):
            pass

    assert sent["reasoning"] == {"enabled": False}
