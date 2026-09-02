"""Der Messages-Weg: Uebersetzung, Ereignisse, Abrechnung.

Claude auf Azure spricht kein ``/chat/completions`` — es gibt dort keines. Der
Endpunkt ist ``…/anthropic/v1/messages``, und das Protokoll ist Anthropics
eigenes. Diese Datei haelt fest, dass der dritte Dialekt dasselbe leistet wie
die beiden anderen und dass der Aufrufer den Unterschied nicht bemerkt.

Die Rahmen hier folgen den Beispielen aus Anthropics Streaming-Dokumentation.
Sie sind auf das Noetige gekuerzt, aber nicht erfunden — erfundene Rahmen
wuerden Annahmen festschreiben statt Tatsachen.
"""

from __future__ import annotations

import json

import pytest

from models import AiProvider
from services.anthropic_messages_adapter import (
    ANTHROPIC_VERSION,
    STANDARD_MAX_TOKENS,
    _fehler_im_ereignis,
    nachrichten_uebersetzen,
    spricht_messages,
    stream_messages,
    werkzeuge_uebersetzen,
    werkzeugwahl_uebersetzen,
)
from services.openai_compatible_adapter import AiProviderRequestError, StreamUsage


def _provider(kind: str = "azure_anthropic") -> AiProvider:
    return AiProvider(
        id=1, name="P", provider_kind=kind, default_model="claude-sonnet-5",
        enabled=True, requires_api_key=False, azure_resource_name="mein-ai-hub",
    )


def _sse(*rahmen: dict) -> bytes:
    """Anthropics Strom traegt zusaetzlich ``event:``-Zeilen.

    Sie stehen hier bewusst mit drin, obwohl der Adapter sie uebergeht: haette
    er sie versehentlich als Nutzlast gelesen, faenden es nur diese Zeilen.
    """
    stuecke = [
        f"event: {r.get('type')}\ndata: {json.dumps(r)}\n\n" for r in rahmen
    ]
    return "".join(stuecke).encode()


class _FakeAntwort:
    def __init__(self, koerper: bytes, status: int = 200) -> None:
        self._koerper = koerper
        self.status_code = status

    async def aiter_text(self):
        yield self._koerper.decode()

    async def aread(self) -> bytes:
        return self._koerper

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakeClient:
    """Ein Client, der einen vorbereiteten Strom zurueckgibt.

    Haelt zusaetzlich die **Kopfzeilen** fest, anders als der Zwilling im
    Responses-Test: hier haengt an ihnen eine Zusage, die sonst niemand prueft
    (`x-api-key` statt `Authorization`, dazu `anthropic-version`).
    """

    def __init__(self, koerper: bytes, status: int = 200) -> None:
        self._koerper = koerper
        self._status = status
        self.gesendet: dict | None = None
        self.koepfe: dict | None = None
        self.adresse = None

    def stream(self, _methode, url, *, headers=None, json=None):
        self.gesendet = json
        self.koepfe = headers
        self.adresse = url
        return _FakeAntwort(self._koerper, self._status)


# ── Die Protokollwahl ─────────────────────────────────────────────────


def test_only_the_anthropic_kind_speaks_this_dialect() -> None:
    assert spricht_messages(_provider("azure_anthropic")) is True
    assert spricht_messages(_provider("azure_openai")) is False
    assert spricht_messages(_provider("openrouter")) is False
    assert spricht_messages(_provider("openai")) is False


def test_an_unknown_kind_does_not_take_a_run_down() -> None:
    """Ein unbekannter Anbieter erbt den verbreiteten Weg, statt zu werfen."""
    assert spricht_messages(_provider("gibtesnicht")) is False


# ── Werkzeugkatalog und Werkzeugwahl ──────────────────────────────────


def test_tools_get_an_input_schema() -> None:
    """``function.parameters`` heisst hier ``input_schema``."""
    assert werkzeuge_uebersetzen([{
        "type": "function",
        "function": {"name": "read_server_status", "description": "Status",
                     "parameters": {"type": "object", "properties": {}}},
    }]) == [{
        "name": "read_server_status", "description": "Status",
        "input_schema": {"type": "object", "properties": {}},
    }]


def test_a_tool_without_parameters_still_gets_a_schema() -> None:
    """``input_schema`` ist Pflicht — ``None`` toetet den ganzen Katalog."""
    uebersetzt = werkzeuge_uebersetzen([
        {"type": "function", "function": {"name": "ping"}},
    ])
    assert uebersetzt == [{
        "name": "ping", "description": "",
        "input_schema": {"type": "object", "properties": {}},
    }]


def test_no_tools_stays_none() -> None:
    assert werkzeuge_uebersetzen(None) is None
    assert werkzeuge_uebersetzen([]) is None


def test_the_tool_choice_becomes_an_object() -> None:
    """Anthropic kennt keine Zeichenkette an dieser Stelle."""
    assert werkzeugwahl_uebersetzen(None) == {"type": "auto"}
    assert werkzeugwahl_uebersetzen("auto") == {"type": "auto"}
    # Die Schlussrunde eines Laufs will noch einen Satz, aber keinen Aufruf.
    assert werkzeugwahl_uebersetzen("none") == {"type": "none"}
    # `ai_mail_text` zwingt auf genau ein Werkzeug.
    assert werkzeugwahl_uebersetzen(
        {"type": "function", "function": {"name": "mail"}}
    ) == {"type": "tool", "name": "mail"}
    # Unbekanntes wird zu „das Modell entscheidet" statt zu einem 400.
    assert werkzeugwahl_uebersetzen({"quatsch": 1}) == {"type": "auto"}


# ── Der Verlauf ───────────────────────────────────────────────────────


def test_the_system_text_leaves_the_message_list() -> None:
    """``{"role": "system"}`` in ``messages`` weist die API ab."""
    system, verlauf = nachrichten_uebersetzen([
        {"role": "system", "content": "Du bist MSM."},
        {"role": "user", "content": "Hallo"},
    ])
    assert system == "Du bist MSM."
    assert verlauf == [{"role": "user", "content": [{"type": "text", "text": "Hallo"}]}]


def test_a_late_system_message_is_not_lost() -> None:
    """`ai_lage` haengt den Lageblock spaet als zweite Systemnachricht an."""
    system, verlauf = nachrichten_uebersetzen([
        {"role": "system", "content": "Du bist MSM."},
        {"role": "user", "content": "Hallo"},
        {"role": "system", "content": "Es ist 20:00 Uhr."},
    ])
    assert system == "Du bist MSM.\n\nEs ist 20:00 Uhr."
    assert len(verlauf) == 1


def test_a_tool_call_becomes_text_and_tool_use_blocks() -> None:
    """Beides, nicht eines davon — der Text traegt die Ansage der Runde."""
    _, verlauf = nachrichten_uebersetzen([{
        "role": "assistant",
        "content": "Ich sehe nach.",
        "tool_calls": [{
            "id": "toolu_1", "type": "function",
            "function": {"name": "read_server_status",
                         "arguments": '{"server_id":1}'},
        }],
    }])
    assert verlauf == [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Ich sehe nach."},
            {"type": "tool_use", "id": "toolu_1", "name": "read_server_status",
             "input": {"server_id": 1}},
        ],
    }]


def test_all_tool_results_of_one_round_land_in_a_single_message() -> None:
    """**Die Zusage dieser Uebersetzung.** Alles andere ist ein 400.

    MSM fuehrt jedes Werkzeugergebnis als eigene Nachricht; die Messages-API
    verlangt alle Ergebnisse einer Runde in **einer** Benutzernachricht.
    """
    _, verlauf = nachrichten_uebersetzen([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "toolu_1", "function": {"name": "a", "arguments": "{}"}},
            {"id": "toolu_2", "function": {"name": "b", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "online"},
        {"role": "tool", "tool_call_id": "toolu_2", "content": "offline"},
    ])
    assert len(verlauf) == 2
    assert verlauf[1]["role"] == "user"
    assert [block["tool_use_id"] for block in verlauf[1]["content"]] == [
        "toolu_1", "toolu_2",
    ]


def test_unreadable_arguments_do_not_take_the_history_down() -> None:
    """Der Aufruf ist gelaufen; sein Ergebnis steht in der naechsten Nachricht."""
    _, verlauf = nachrichten_uebersetzen([{
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "toolu_1",
                        "function": {"name": "a", "arguments": "kein json"}}],
    }])
    assert verlauf[0]["content"][0]["input"] == {}


def test_an_empty_assistant_message_disappears() -> None:
    """Eine Nachricht mit leerem ``content`` ist ein 400."""
    _, verlauf = nachrichten_uebersetzen([
        {"role": "user", "content": "Hallo"},
        {"role": "assistant", "content": ""},
    ])
    assert len(verlauf) == 1


def test_attachment_blocks_do_not_become_python_syntax() -> None:
    """Listeninhalte werden ausgelesen und nicht ``str(...)``-t."""
    _, verlauf = nachrichten_uebersetzen([
        {"role": "user", "content": [{"type": "text", "text": "Teil eins."},
                                     {"type": "text", "text": " Teil zwei."}]},
    ])
    assert verlauf[0]["content"] == [
        {"type": "text", "text": "Teil eins. Teil zwei."},
    ]


def test_an_image_reaches_the_model_instead_of_being_dropped() -> None:
    """**Auge ohne Sehnerv.** Der Text sagt „liegt bei" — dann muss es beiliegen.

    MSM baut Bilder ueberall in OpenAIs Form (`_desktopmeldung`,
    `ai_attachment_service`); Anthropic will die Data-URL zerlegt. Ohne die
    Uebersetzung bekam Claude nur die Behauptung und meldete, es habe kein
    auswertbares Bildschirmergebnis.
    """
    _, verlauf = nachrichten_uebersetzen([{
        "role": "user",
        "content": [
            {"type": "text", "text": "Ergebnis: bild liegt bei"},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ"}},
        ],
    }])
    assert verlauf == [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Ergebnis: bild liegt bei"},
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg",
                                         "data": "/9j/4AAQ"}},
        ],
    }]


def test_a_message_that_is_only_an_image_survives() -> None:
    """Sie ist nicht leer, sie ist ein Bild — und ein PNG bleibt ein PNG."""
    _, verlauf = nachrichten_uebersetzen([{
        "role": "user",
        "content": [{"type": "image_url",
                     "image_url": {"url": "data:image/png;base64,iVBORw0KGgo"}}],
    }])
    assert verlauf == [{
        "role": "user",
        "content": [{"type": "image", "source": {"type": "base64",
                                                 "media_type": "image/png",
                                                 "data": "iVBORw0KGgo"}}],
    }]


def test_an_address_that_is_no_data_url_is_not_invented() -> None:
    """MSM baut nur Data-URLs; fuer alles andere gibt es hier keine Wahrheit."""
    _, verlauf = nachrichten_uebersetzen([{
        "role": "user",
        "content": [{"type": "text", "text": "Da."},
                    {"type": "image_url",
                     "image_url": {"url": "https://example.invalid/bild.png"}}],
    }])
    assert verlauf[0]["content"] == [{"type": "text", "text": "Da."}]


# ── Der Strom ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_thinking_and_a_tool_call_in_one_round() -> None:
    """Die drei Ereignisarten, die MSM ueberhaupt unterscheidet."""
    koerper = _sse(
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 100, "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 10, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "Ich pruefe den Status"}},
        # Traegt keinen lesbaren Text und darf nicht als Denkschritt erscheinen.
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "EqQBCgIYAhIM"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "text_delta", "text": "Ich sehe nach."}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "toolu_1",
                           "name": "read_server_status", "input": {}}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": '{"server_id"'}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": ":1}"}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 54}},
        {"type": "message_stop"},
    )
    usage = StreamUsage()
    client = _FakeClient(koerper)
    text, gedanken, fertige_aufrufe = [], [], []
    async for stueck in stream_messages(
        client, provider=_provider(), api_key="azure-key",
        messages=[{"role": "user", "content": "Status von Server 1?"}],
        usage=usage,
        tools=[{"type": "function",
                "function": {"name": "read_server_status", "parameters": {}}}],
        reasoning=True, reasoning_effort="high",
    ):
        if stueck.kind == "reasoning":
            gedanken.append(stueck.text)
        elif stueck.kind == "content":
            text.append(stueck.text)
        elif stueck.kind == "tool_ready":
            fertige_aufrufe.append(stueck.tool_call)

    assert text == ["Ich sehe nach."]
    assert gedanken == ["Ich pruefe den Status"]
    assert len(usage.tool_calls) == 1
    assert usage.tool_calls[0].id == "toolu_1"
    assert usage.tool_calls[0].name == "read_server_status"
    assert usage.tool_calls[0].arguments == {"server_id": 1}
    assert fertige_aufrufe == [usage.tool_calls[0]]
    assert usage.anfragen == 1


@pytest.mark.asyncio
async def test_the_prompt_is_the_sum_and_the_cache_is_a_subset() -> None:
    """Anthropic meldet die Eingabe dreigeteilt, MSM fuehrt eine Summe.

    Wer ``input_tokens`` unbesehen uebernimmt, unterschlaegt bei gefuelltem
    Zwischenspeicher den groessten Teil des Prompts — hier 910 von 1010.
    """
    usage = StreamUsage()
    async for _ in stream_messages(
        _FakeClient(_sse(
            {"type": "message_start", "message": {"usage": {
                "input_tokens": 100, "cache_read_input_tokens": 900,
                "cache_creation_input_tokens": 10}}},
            {"type": "message_delta", "usage": {"output_tokens": 54}},
            {"type": "message_stop"},
        )),
        provider=_provider(), api_key="azure-key",
        messages=[{"role": "user", "content": "Hallo"}], usage=usage,
    ):
        pass
    assert usage.prompt_tokens == 1010
    assert usage.cached_tokens == 900
    assert usage.cache_write_tokens == 10
    assert usage.completion_tokens == 54
    assert usage.total_tokens == 1064
    assert usage.vom_anbieter is True


@pytest.mark.asyncio
async def test_the_output_count_is_cumulative_and_never_added_up() -> None:
    """Anthropic dokumentiert es ausdruecklich — ein ``+=`` zaehlte doppelt."""
    usage = StreamUsage()
    async for _ in stream_messages(
        _FakeClient(_sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
            {"type": "message_delta", "usage": {"output_tokens": 20}},
            {"type": "message_delta", "usage": {"output_tokens": 50}},
            {"type": "message_stop"},
        )),
        provider=_provider(), api_key="azure-key",
        messages=[{"role": "user", "content": "Hallo"}], usage=usage,
    ):
        pass
    assert usage.completion_tokens == 50


@pytest.mark.asyncio
async def test_the_request_carries_the_right_headers_address_and_fields() -> None:
    """Der Schluessel im falschen Kopf ist ein 401, das keines ist."""
    client = _FakeClient(_sse(
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "ok"}},
        {"type": "message_stop"},
    ))
    async for _ in stream_messages(
        client, provider=_provider(), api_key="azure-key",
        messages=[{"role": "system", "content": "Du bist MSM."},
                  {"role": "user", "content": "Hallo"}],
        usage=StreamUsage(), reasoning=True, reasoning_effort="high",
    ):
        pass

    # `x-api-key` und nicht `api-key`: Microsofts eigenes cURL-Beispiel nimmt
    # diesen Kopf, und mit dem anderen wies das Azure-Gateway denselben
    # Schluessel ab, der an `/openai/v1` durchging.
    assert client.koepfe["x-api-key"] == "azure-key"
    assert "api-key" not in client.koepfe
    assert "Authorization" not in client.koepfe
    assert client.koepfe["anthropic-version"] == ANTHROPIC_VERSION
    assert str(client.adresse) == (
        "https://mein-ai-hub.services.ai.azure.com/anthropic/v1/messages"
    )
    # ``max_tokens`` ist Pflicht; ``system`` steht neben ``messages``.
    assert client.gesendet["max_tokens"] == STANDARD_MAX_TOKENS
    assert client.gesendet["system"] == "Du bist MSM."
    assert client.gesendet["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Hallo"}]},
    ]
    # Die aktuelle Denkform — nie ``budget_tokens``, das lehnen die aktuellen
    # Modelle laut Microsofts Modelltabelle ab.
    assert client.gesendet["thinking"] == {"type": "adaptive"}
    assert client.gesendet["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in json.dumps(client.gesendet)


@pytest.mark.asyncio
async def test_without_a_level_nothing_about_thinking_goes_out() -> None:
    """Ohne Katalog kennt MSM die zulaessigen Stufen nicht — also schweigt es."""
    client = _FakeClient(_sse({"type": "message_stop"}))
    async for _ in stream_messages(
        client, provider=_provider(), api_key="azure-key",
        messages=[{"role": "user", "content": "Hallo"}],
        usage=StreamUsage(), reasoning=True, reasoning_effort=None,
    ):
        pass
    assert "thinking" not in client.gesendet
    assert "output_config" not in client.gesendet


@pytest.mark.asyncio
async def test_a_stream_that_never_stops_is_an_error() -> None:
    """Ohne ``message_stop`` ist die Antwort unvollstaendig, nicht fertig."""
    with pytest.raises(AiProviderRequestError) as fehler:
        async for _ in stream_messages(
            _FakeClient(_sse(
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "halb"}},
            )),
            provider=_provider(), api_key="azure-key",
            messages=[{"role": "user", "content": "Hallo"}], usage=StreamUsage(),
        ):
            pass
    assert fehler.value.code == "AI_PROVIDER_STREAM_INCOMPLETE"


@pytest.mark.asyncio
async def test_an_error_event_names_its_cause() -> None:
    """Ein Fehler bei Status 200 — der teuerste blinde Fleck dieser Schicht."""
    with pytest.raises(AiProviderRequestError) as fehler:
        async for _ in stream_messages(
            _FakeClient(_sse({"type": "error", "error": {
                "type": "overloaded_error", "message": "Overloaded"}})),
            provider=_provider(), api_key="azure-key",
            messages=[{"role": "user", "content": "Hallo"}], usage=StreamUsage(),
        ):
            pass
    assert fehler.value.code == "AI_PROVIDER_UNAVAILABLE"
    assert fehler.value.detail == "Overloaded"


def test_error_types_map_to_the_same_marks_as_http_status() -> None:
    """Der Aufrufer soll nicht zwei Fehlersprachen kennen muessen."""
    assert _fehler_im_ereignis({"type": "message_stop"}) is None
    for art, marke in (
        ("authentication_error", "AI_PROVIDER_AUTH_FAILED"),
        ("rate_limit_error", "AI_PROVIDER_RATE_LIMITED"),
        ("not_found_error", "AI_PROVIDER_ENDPOINT_NOT_FOUND"),
        ("invalid_request_error", "AI_PROVIDER_REQUEST_REJECTED"),
        ("voellig_neue_art", "AI_PROVIDER_REQUEST_REJECTED"),
    ):
        assert _fehler_im_ereignis(
            {"type": "error", "error": {"type": art, "message": "x"}}
        ) == (marke, "x")


@pytest.mark.asyncio
async def test_a_failing_status_keeps_the_providers_own_words() -> None:
    """Ein uebersetzter Code allein sagt nicht, *was* fehlt."""
    koerper = json.dumps({
        "type": "error",
        "error": {"type": "not_found_error", "message": "deployment not found"},
    }).encode()
    with pytest.raises(AiProviderRequestError) as fehler:
        async for _ in stream_messages(
            _FakeClient(koerper, status=404),
            provider=_provider(), api_key="azure-key",
            messages=[{"role": "user", "content": "Hallo"}], usage=StreamUsage(),
        ):
            pass
    assert fehler.value.code == "AI_PROVIDER_ENDPOINT_NOT_FOUND"
    assert "deployment not found" in (fehler.value.detail or "")


@pytest.mark.asyncio
async def test_a_missing_key_never_reaches_the_network() -> None:
    client = _FakeClient(_sse({"type": "message_stop"}))
    provider = _provider()
    provider.requires_api_key = True
    with pytest.raises(AiProviderRequestError) as fehler:
        async for _ in stream_messages(
            client, provider=provider, api_key=None,
            messages=[{"role": "user", "content": "Hallo"}], usage=StreamUsage(),
        ):
            pass
    assert fehler.value.code == "AI_PROVIDER_KEY_MISSING"
    assert client.gesendet is None
