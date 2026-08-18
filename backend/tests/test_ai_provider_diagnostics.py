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


@pytest.mark.parametrize(
    ("fehler", "code", "detail"),
    [
        (
            {"code": 402, "message": "Insufficient credits. Add more using https://openrouter.ai/settings/credits"},
            "AI_PROVIDER_PAYMENT_REQUIRED",
            "Insufficient credits. Add more using https://openrouter.ai/settings/credits",
        ),
        (
            {"code": 404, "message": "No endpoints found for openai/gpt-5.6-luna."},
            "AI_PROVIDER_ENDPOINT_NOT_FOUND",
            "No endpoints found for openai/gpt-5.6-luna.",
        ),
        (
            {"code": 429, "message": "Rate limit exceeded"},
            "AI_PROVIDER_RATE_LIMITED",
            "Rate limit exceeded",
        ),
        (
            {"code": "server_error", "message": "Provider disconnected unexpectedly"},
            "AI_PROVIDER_REQUEST_REJECTED",
            "Provider disconnected unexpectedly",
        ),
    ],
)
@pytest.mark.asyncio
async def test_an_error_in_the_middle_of_the_stream_keeps_its_reason(
    fehler: dict, code: str, detail: str
) -> None:
    """Der teuerste blinde Fleck dieser Schicht — er kostete eine Ferndiagnose.

    Sind die Kopfzeilen erst draussen, steht der Status auf `200` und laesst sich
    nicht mehr aendern. OpenRouter meldet einen Fehler ab da als gewoehnliches
    `data:`-Ereignis mit `error` auf oberster Ebene, dazu ein `choices`-Eintrag
    mit leerem Delta und `finish_reason: "error"`, und beendet die Verbindung
    ohne `[DONE]`.

    MSM las nur `choices`, fand ein leeres Delta, sprang weiter — und meldete am
    Schleifenende „Die Antwort des Anbieters brach vorzeitig ab". Der einzige
    Satz, der die Ursache benannt haette, wurde nie gelesen. Im Betrieb sah das
    aus wie ein kaputtes Panel und war ein leeres Guthaben.
    """
    rahmen = {
        "id": "cmpl-abc123",
        "object": "chat.completion.chunk",
        "model": "openai/gpt-4o",
        "provider": "openai",
        "error": fehler,
        "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}],
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"Hallo"}}]}\n\n'
            f"data: {json.dumps(rahmen)}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    usage = StreamUsage()
    async with _client(handler) as client:
        with pytest.raises(AiProviderRequestError) as excinfo:
            async for _chunk in stream_chat_completion(
                client, provider=_provider(), api_key=None,
                messages=[{"role": "user", "content": "ping"}], usage=usage,
            ):
                pass

    assert excinfo.value.code == code
    assert excinfo.value.detail == detail


@pytest.mark.asyncio
async def test_a_bare_error_frame_without_any_choices_is_read_too() -> None:
    """Ein Fehler vor dem ersten Token bringt manchmal gar kein `choices` mit.

    Die alte Schleife stieg genau hier aus (`choices` fehlt → `continue`) und
    landete wieder bei „brach vorzeitig ab". Der Rahmen ist trotzdem eindeutig.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"error":{"code":401,"message":"No auth credentials found"}}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    usage = StreamUsage()
    async with _client(handler) as client:
        with pytest.raises(AiProviderRequestError) as excinfo:
            async for _chunk in stream_chat_completion(
                client, provider=_provider(), api_key=None,
                messages=[{"role": "user", "content": "ping"}], usage=usage,
            ):
                pass

    assert excinfo.value.code == "AI_PROVIDER_AUTH_FAILED"
    assert excinfo.value.detail == "No auth credentials found"


@pytest.mark.asyncio
async def test_a_provider_message_in_the_stream_is_redacted_like_any_other() -> None:
    """Auch mitten im Strom ist die Anbietermeldung Fremdtext."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"error":{"code":401,"message":"key api_key=sk-abcdefghijklmnopqrstuvwxyz012345 '
            + "x" * 400
            + '"}}\n\n',
            headers={"content-type": "text/event-stream"},
        )

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


async def _abschicken(kind: str, **wuensche) -> dict:
    """Eine Anfrage an einen Anbieter stellen und ansehen, was hinausging.

    Die Antwort deckt **beide** Chatwege ab: den Rahmen von Chat Completions
    (``choices[].delta``) und den von Responses (``response.*``). Welcher
    gelesen wird, entscheidet der Anbieter — ein Zugang mit
    ``protokoll_chat="responses"`` schaut am Chat-Completions-Rahmen vorbei
    und umgekehrt, und ein unbeantworteter Strom endete in
    ``AI_PROVIDER_STREAM_INCOMPLETE`` statt in einer Aussage über den Body.
    """
    gesendet: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        gesendet.update(json.loads(request.content))
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                'data: {"type":"response.completed","response":{"usage":{}}}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    provider = AiProvider(
        id=1, name="Zugang", provider_kind=kind, default_model="m",
        enabled=True, requires_api_key=False,
    )
    usage = StreamUsage()
    async with _client(handler) as client:
        async for _chunk in stream_chat_completion(
            client, provider=provider, api_key=None,
            messages=[{"role": "user", "content": "ping"}], usage=usage,
            **wuensche,
        ):
            pass
    return gesendet


@pytest.mark.parametrize(
    ("kind", "erwartet"),
    [("openrouter", True), ("openai", False)],
)
@pytest.mark.asyncio
async def test_only_a_provider_that_knows_the_word_gets_it(
    kind: str, erwartet: bool
) -> None:
    """`reasoning` ist OpenRouters Wortschatz und ging trotzdem an alle hinaus.

    Das hat den OpenAI-Zugang vom ersten Tag an vollstaendig unbrauchbar
    gemacht — OpenAI antwortet auf ein unbekanntes Argument mit
    ``400 Unrecognized request argument supplied: reasoning``, und zwar bei
    **jeder** Anfrage: `{"enabled": false}` ist genauso unbekannt wie `true`.
    Der Betreiber sah davon nichts, weil eine Schicht tiefer der Grund starb.

    Dasselbe gilt fuer `cache_control` — ebenfalls OpenRouters Erweiterung,
    ebenfalls ein 400 anderswo.

    Seit OpenAI ueber ``/responses`` laeuft, kennt **dieser** Weg ein eigenes
    ``reasoning``-Objekt (``{"effort": ..., "summary": "auto"}``) — deshalb
    fragt der Fall unten nach OpenRouters Form und nicht nur nach dem
    Feldnamen. Zwei Anbieter, zwei Bedeutungen fuer dasselbe Wort.
    """
    gesendet = await _abschicken(
        kind, reasoning=True, reasoning_effort="high", cache_marke=True
    )

    if erwartet:
        assert gesendet["reasoning"] == {"enabled": True, "effort": "high"}
    else:
        assert gesendet.get("reasoning") != {"enabled": True, "effort": "high"}
    assert ("cache_control" in gesendet) is erwartet
    # Was alle koennen, geht auch an alle.
    assert gesendet["model"] == "m"
    assert gesendet["stream"] is True


@pytest.mark.asyncio
async def test_each_provider_gets_the_wish_in_its_own_dialect() -> None:
    """Derselbe Wunsch, zwei Formen — und niemals beide in einer Anfrage.

    Uebersetzt wird dabei kein **Wort**: beide Anbieter benutzen denselben
    Wortschatz fuer die Stufen (``minimal|low|medium|high|xhigh|max``, dazu
    ``none``), was am 2026-08-17 an OpenAIs `openapi.json` und an OpenRouters
    Katalog nachgesehen wurde. Verschieden ist nur, wohin die Stufe gehoert.

    Bei OpenAI hing sie bis zum 18.08.2026 als ``reasoning_effort`` am
    Chat-Completions-Body. Der Zugang spricht jetzt ``/responses``, weil jener
    Endpunkt Werkzeuge und Denkstufe nicht zusammen nimmt; dort steht sie in
    ``reasoning.effort``, mit ``summary: "auto"`` daneben — ohne die Zeile
    schweigt der Strom ueber die Denkschritte, obwohl sie abgerechnet werden.
    """
    an_openai = await _abschicken(
        "openai", reasoning=True, reasoning_effort="high", cache_marke=True
    )
    assert an_openai["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "reasoning_effort" not in an_openai
    # Der Verlauf heisst hier `input`, nicht `messages` — der ganze Unterschied
    # zwischen den beiden Wegen an einer Stelle.
    assert "messages" not in an_openai
    assert an_openai["input"] == [{"role": "user", "content": "ping"}]

    an_openrouter = await _abschicken(
        "openrouter", reasoning=True, reasoning_effort="high", cache_marke=True
    )
    assert an_openrouter["reasoning"] == {"enabled": True, "effort": "high"}
    assert "reasoning_effort" not in an_openrouter
    assert an_openrouter["messages"] == [{"role": "user", "content": "ping"}]


@pytest.mark.asyncio
async def test_switching_thinking_off_is_a_level_where_there_is_no_switch() -> None:
    """Beim Anbieter ohne Schalter ist „aus" das Wort ``none`` — sonst nichts.

    Der Fall ist der teure: `reasoning=False` mit einer leeren Stufe saehe hier
    aus wie eine sparsame Anfrage und waere das Gegenteil. OpenAI denkt dann in
    seiner Voreinstellung weiter (bei `gpt-5.5` ``medium``) und rechnet es ab —
    der Betreiber hat abgeschaltet und bezahlt trotzdem. Welche Modelle das Wort
    vertragen, entscheidet `ai_reasoning.klemmen` am Katalog; hier zaehlt nur,
    dass es nicht unterwegs verlorengeht.

    Ohne ``summary``, denn es gibt keine: wer nicht denkt, fasst nichts zusammen.
    """
    an_openai = await _abschicken("openai", reasoning=False, reasoning_effort="none")
    assert an_openai["reasoning"] == {"effort": "none"}

    # OpenRouter sagt dasselbe mit seinem Schalter. Die Stufe daneben waere dort
    # eine zweite, widersprechende Angabe — welche gewinnt, entschiede dann der
    # Anbieter und nicht MSM.
    an_openrouter = await _abschicken(
        "openrouter", reasoning=False, reasoning_effort="none"
    )
    assert an_openrouter["reasoning"] == {"enabled": False}


@pytest.mark.asyncio
async def test_no_known_level_means_no_field_at_all() -> None:
    """Was MSM nicht weiss, spricht es nicht aus.

    Ein Modell, das der fremde Katalog nicht fuehrt, kommt hier ohne Stufe an.
    Dann geht bei OpenAI **nichts** hinaus — auch kein ``null``, auf das beide
    Wege mit einem 400 antworten.
    """
    an_openai = await _abschicken("openai", reasoning=True, reasoning_effort=None)
    assert "reasoning_effort" not in an_openai
    assert "reasoning" not in an_openai
