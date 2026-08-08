"""Fehlkonfigurationen muessen unterscheidbar sein, und Denkschritte fliessen.

Anlass ist ein konkreter Ausfall: eine Anfrage im Chat brach mit
`ai.errors.provider` ab. Dieselbe Meldung erschien bei einer Basis-URL, die auf
`/chat/completions` endete, bei einem Tippfehler im Modellnamen und bei einem
abgelaufenen Key. Alle drei sind verschiedene Handlungen fuer den Betreiber, und
keiner davon war aus der Oberflaeche erkennbar.
"""

from __future__ import annotations

import json

import httpx
import pytest

from models import AiProvider
from services.ai_provider_service import validate_provider_base_url
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


def _provider(base_url: str = "https://provider.invalid/v1") -> AiProvider:
    return AiProvider(
        id=1,
        name="Diagnose",
        base_url=base_url,
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
        allow_private_network=False,
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("entered", "expected"),
    [
        # Der haeufigste Bedienfehler: die Doku vieler Anbieter zeigt die
        # vollstaendige Endpunkt-URL, MSM haengt den Endpunkt aber selbst an.
        ("https://provider.invalid/v1/chat/completions", "https://provider.invalid/v1"),
        ("https://provider.invalid/v1/completions", "https://provider.invalid/v1"),
        ("https://provider.invalid/v1/responses", "https://provider.invalid/v1"),
        ("https://provider.invalid/v1/", "https://provider.invalid/v1"),
        ("https://provider.invalid/v1", "https://provider.invalid/v1"),
    ],
)
def test_a_pasted_endpoint_path_is_normalized_away(
    entered: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ipaddress

    # Bewusst keine Doku-Adresse (203.0.113.0/24): Python zaehlt die zu
    # `is_private`, und der SSRF-Schutz wuerde sie damit korrekt abweisen.
    monkeypatch.setattr(
        "services.ai_provider_service._resolved_addresses",
        lambda host: {ipaddress.ip_address("93.184.216.34")},
    )

    assert validate_provider_base_url(entered, allow_private_network=False) == expected


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
    monkeypatch.setattr(
        "services.openai_compatible_adapter.assert_provider_destination", lambda _p: None
    )

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
    monkeypatch.setattr(
        "services.openai_compatible_adapter.assert_provider_destination", lambda _p: None
    )

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
    monkeypatch.setattr(
        "services.openai_compatible_adapter.assert_provider_destination", lambda _p: None
    )
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
async def test_without_the_switch_no_reasoning_field_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Anbieter, der das Feld nicht kennt, soll es gar nicht erst sehen."""
    monkeypatch.setattr(
        "services.openai_compatible_adapter.assert_provider_destination", lambda _p: None
    )
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

    assert "reasoning" not in sent
