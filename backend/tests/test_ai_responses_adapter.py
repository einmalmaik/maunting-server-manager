"""Der Responses-Weg: Uebersetzung, Ereignisse, Abrechnung.

OpenAI direkt spricht `/responses` statt `/chat/completions`, weil letzteres
Werkzeuge und Denkstufe nicht zusammen nimmt (die Messung steht in
`services/ai_provider_registry/openai.py`). Diese Datei haelt fest, dass der
zweite Dialekt dasselbe leistet wie der erste — und dass der Aufrufer den
Unterschied nicht bemerkt.

Alle Rahmen hier sind **gemessene** Ereignisse aus einem echten Lauf gegen
OpenAI am 2026-08-18, auf das Noetige gekuerzt. Erfundene Rahmen wuerden
Annahmen festschreiben statt Tatsachen.
"""

from __future__ import annotations

import json

import pytest

from models import AiProvider
from services.openai_compatible_adapter import AiProviderRequestError, StreamUsage
from services.openai_responses_adapter import (
    _fehler_im_ereignis,
    _usage_uebernehmen,
    _werkzeuge_uebersetzen,
    _werkzeugwahl_uebersetzen,
    nachrichten_uebersetzen,
    spricht_responses,
    stream_responses,
)


def _provider(kind: str = "openai") -> AiProvider:
    return AiProvider(
        id=1, name="P", provider_kind=kind, default_model="gpt-5.6-luna",
        enabled=True, requires_api_key=False,
    )


# ── Die Protokollwahl ─────────────────────────────────────────────────


def test_only_openai_speaks_the_responses_dialect() -> None:
    assert spricht_responses(_provider("openai")) is True
    assert spricht_responses(_provider("openrouter")) is False


def test_an_unknown_kind_does_not_take_a_run_down() -> None:
    """Ein unbekannter Anbieter erbt den verbreiteten Weg, statt zu werfen."""
    assert spricht_responses(_provider("gibtesnicht")) is False


# ── Werkzeugkatalog ───────────────────────────────────────────────────


def test_tools_lose_their_wrapper() -> None:
    """``{"function": {...}}`` wird flach — sonst antwortet die API mit 400."""
    flach = _werkzeuge_uebersetzen([{
        "type": "function",
        "function": {"name": "read_server_status", "description": "Status",
                     "parameters": {"type": "object", "properties": {}}},
    }])
    assert flach == [{
        "type": "function", "name": "read_server_status", "description": "Status",
        "parameters": {"type": "object", "properties": {}},
    }]


def test_an_already_flat_catalog_is_left_alone() -> None:
    """Zweimal auspacken zerstoerte den Katalog."""
    flach = [{"type": "function", "name": "x", "parameters": {}}]
    assert _werkzeuge_uebersetzen(flach) == flach


def test_no_tools_stays_none() -> None:
    assert _werkzeuge_uebersetzen(None) is None
    assert _werkzeuge_uebersetzen([]) is None


# ── Werkzeugwahl ──────────────────────────────────────────────────────


def test_a_forced_tool_loses_the_same_wrapper_as_the_catalog() -> None:
    """`ai_mail_text` zwingt in OpenAIs verschachtelter Form auf ein Werkzeug.

    Unuebersetzt ist das ein 400, und weil `ai_mail_text` jeden Fehler
    abfaengt und auf seinen festen Text zurueckfaellt, waere der KI-Mailtext an
    einem OpenAI-Zugang still tot.
    """
    assert _werkzeugwahl_uebersetzen(
        {"type": "function", "function": {"name": "msm_mailtext"}}
    ) == {"type": "function", "name": "msm_mailtext"}


def test_plain_words_go_through_untouched() -> None:
    """``auto`` und ``none`` kennt die Responses-API wortgleich."""
    assert _werkzeugwahl_uebersetzen(None) == "auto"
    assert _werkzeugwahl_uebersetzen("auto") == "auto"
    # Die Schlussrunde eines Laufs will noch einen Satz, aber keinen Aufruf.
    assert _werkzeugwahl_uebersetzen("none") == "none"


def test_an_already_flat_choice_survives_and_nonsense_becomes_auto() -> None:
    assert _werkzeugwahl_uebersetzen(
        {"type": "function", "name": "msm_mailtext"}
    ) == {"type": "function", "name": "msm_mailtext"}
    assert _werkzeugwahl_uebersetzen({"quatsch": 1}) == "auto"


# ── Der Verlauf ───────────────────────────────────────────────────────


def test_plain_roles_survive_unchanged() -> None:
    assert nachrichten_uebersetzen([
        {"role": "system", "content": "Du bist MSM."},
        {"role": "user", "content": "Hallo"},
    ]) == [
        {"role": "system", "content": "Du bist MSM."},
        {"role": "user", "content": "Hallo"},
    ]


def test_a_tool_call_becomes_its_own_position() -> None:
    """``assistant`` mit Aufrufen wird zu Text **und** ``function_call``.

    Beides, nicht eines davon: der Text traegt die Ansagen, auf die sich das
    Modell in der Folgerunde bezieht.
    """
    eingabe = nachrichten_uebersetzen([{
        "role": "assistant",
        "content": "Ich sehe nach.",
        "tool_calls": [{
            "id": "call_abc", "type": "function",
            "function": {"name": "read_server_status",
                         "arguments": '{"server_id":1}'},
        }],
    }])
    assert eingabe == [
        {"role": "assistant", "content": [{"type": "output_text",
                                           "text": "Ich sehe nach."}]},
        {"type": "function_call", "call_id": "call_abc",
         "name": "read_server_status", "arguments": '{"server_id":1}'},
    ]


def test_an_assistant_without_text_yields_only_the_call() -> None:
    """``content: None`` darf keine leere Nachricht erzeugen."""
    eingabe = nachrichten_uebersetzen([{
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "t", "arguments": "{}"}}],
    }])
    assert eingabe == [{"type": "function_call", "call_id": "c1",
                        "name": "t", "arguments": "{}"}]


def test_a_tool_result_becomes_function_call_output() -> None:
    """Der Rueckkanal. ``role="tool"`` wuerde die API mit einem 400 abweisen."""
    assert nachrichten_uebersetzen([{
        "role": "tool", "tool_call_id": "call_abc",
        "content": '{"status":"running"}',
    }]) == [{
        "type": "function_call_output", "call_id": "call_abc",
        "output": '{"status":"running"}',
    }]


def test_block_content_is_flattened_to_text() -> None:
    """Anhaenge kommen als Liste getippter Bloecke — nie als ``str(...)``.

    Ohne diese Zusammenfuehrung stuende Python-Syntax im Prompt des Benutzers.
    """
    assert nachrichten_uebersetzen([{
        "role": "user",
        "content": [{"type": "text", "text": "Teil A"},
                    {"type": "text", "text": " Teil B"}],
    }]) == [{"role": "user", "content": "Teil A Teil B"}]


def test_an_image_reaches_the_model_instead_of_being_dropped() -> None:
    """**Auge ohne Sehnerv.** Der Text sagt „liegt bei" — dann muss es beiliegen.

    So baut `ai_stream_service._desktopmeldung` ein Bildschirmfoto und so
    `ai_attachment_service` einen Bildanhang. Wurde der Bildblock hier
    stillschweigend verworfen, bekam das Modell nur die Behauptung und
    antwortete mit einer erfundenen Beschreibung.
    """
    eingabe = nachrichten_uebersetzen([{
        "role": "user",
        "content": [
            {"type": "text", "text": "Ergebnis: bild liegt bei"},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ"}},
        ],
    }])
    assert eingabe == [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Ergebnis: bild liegt bei"},
            {"type": "input_image", "image_url": "data:image/jpeg;base64,/9j/4AAQ",
             "detail": "auto"},
        ],
    }]


def test_a_message_without_images_keeps_its_plain_string() -> None:
    """Listenform ohne Not waere eine Aenderung am Praefix — und kostete Cache."""
    assert nachrichten_uebersetzen([
        {"role": "user", "content": "Hallo"},
    ]) == [{"role": "user", "content": "Hallo"}]


# ── Abrechnung ────────────────────────────────────────────────────────


def test_usage_maps_openais_own_field_names() -> None:
    """Andere Namen, dieselbe Bedeutung — gemessener Rahmen."""
    usage = StreamUsage()
    _usage_uebernehmen(usage, {
        "input_tokens": 38,
        "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 12},
        "output_tokens": 1645,
        "output_tokens_details": {"reasoning_tokens": 904},
        "total_tokens": 1683,
    })
    assert usage.prompt_tokens == 38
    assert usage.completion_tokens == 1645
    assert usage.total_tokens == 1683
    assert usage.cached_tokens == 12
    assert usage.reasoning_tokens == 904
    assert usage.vom_anbieter is True


def test_reasoning_tokens_are_a_subset_not_an_extra() -> None:
    """Wer sie zur Ausgabe addiert, zaehlt doppelt und rechnet zu teuer ab."""
    usage = StreamUsage()
    _usage_uebernehmen(usage, {
        "input_tokens": 10, "output_tokens": 100,
        "output_tokens_details": {"reasoning_tokens": 60},
    })
    assert usage.completion_tokens == 100
    assert usage.reasoning_tokens == 60


def test_a_silent_usage_block_changes_nothing() -> None:
    usage = StreamUsage()
    _usage_uebernehmen(usage, None)
    assert usage.prompt_tokens is None
    assert usage.vom_anbieter is False


# ── Fehler im Strom ───────────────────────────────────────────────────


def test_a_failed_response_is_recognised() -> None:
    marke, text = _fehler_im_ereignis({
        "type": "response.failed",
        "response": {"error": {"message": "something broke"}},
    })
    assert marke == "AI_PROVIDER_REQUEST_REJECTED"
    assert text == "something broke"


def test_an_ordinary_event_is_not_an_error() -> None:
    assert _fehler_im_ereignis({"type": "response.output_text.delta",
                                "delta": "Hallo"}) is None


def test_the_error_keeps_its_class_and_not_only_its_words() -> None:
    """Ein leeres Kontingent ist keine abgelehnte Anfrage.

    Unter derselben Marke las der Betreiber „Meist stimmt der Modellname
    nicht" und suchte am falschen Ende — genau die Irrefuehrung, die bei 402
    in `openai_compatible_adapter._error_code` beschrieben ist.
    """
    for code, marke in (
        ("insufficient_quota", "AI_PROVIDER_PAYMENT_REQUIRED"),
        ("rate_limit_exceeded", "AI_PROVIDER_RATE_LIMITED"),
        ("invalid_api_key", "AI_PROVIDER_AUTH_FAILED"),
        ("server_error", "AI_PROVIDER_UNAVAILABLE"),
        ("voellig_neue_art", "AI_PROVIDER_REQUEST_REJECTED"),
    ):
        assert _fehler_im_ereignis({
            "type": "response.failed",
            "response": {"error": {"code": code, "message": "x"}},
        }) == (marke, "x")


def test_an_unusable_code_does_not_become_the_error_itself() -> None:
    """``code`` muss kein Wort sein — dann bleibt es bei der allgemeinen Marke."""
    marke, _text = _fehler_im_ereignis({
        "type": "response.failed",
        "response": {"error": {"code": {"unerwartet": 1}, "message": "kaputt"}},
    })
    assert marke == "AI_PROVIDER_REQUEST_REJECTED"


# ── Der Strom als Ganzes ──────────────────────────────────────────────


def _sse(*rahmen: dict) -> bytes:
    zeilen = [f"data: {json.dumps(r)}\n\n" for r in rahmen]
    return "".join(zeilen).encode()


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
    """Ein Client, der einen vorbereiteten Strom zurueckgibt."""

    def __init__(self, koerper: bytes, status: int = 200) -> None:
        self._koerper = koerper
        self._status = status
        self.gesendet: dict | None = None

    def stream(self, _methode, _url, *, headers=None, json=None):
        self.gesendet = json
        return _FakeAntwort(self._koerper, self._status)


@pytest.mark.asyncio
async def test_thinking_and_a_tool_call_arrive_in_the_same_round() -> None:
    """**Die Zusage dieser Datei.** Genau das kann `/chat/completions` nicht.

    Der Rahmen stammt aus einem echten Lauf mit ``effort: high``: eine
    Denkzusammenfassung und ein ``function_call`` in derselben Runde.
    """
    koerper = _sse(
        {"type": "response.created"},
        {"type": "response.reasoning_summary_text.delta",
         "delta": "**Checking server status**"},
        {"type": "response.output_item.added",
         "item": {"id": "fc_1", "type": "function_call", "arguments": "",
                  "call_id": "call_xyz", "name": "read_server_status"}},
        {"type": "response.function_call_arguments.delta",
         "item_id": "fc_1", "delta": '{"server_id"'},
        {"type": "response.function_call_arguments.delta",
         "item_id": "fc_1", "delta": ":1}"},
        {"type": "response.output_item.done",
         "item": {"id": "fc_1", "type": "function_call",
                  "arguments": '{"server_id":1}', "call_id": "call_xyz",
                  "name": "read_server_status"}},
        {"type": "response.completed",
         "response": {"usage": {"input_tokens": 67, "output_tokens": 54,
                                "output_tokens_details": {"reasoning_tokens": 32}}}},
    )
    usage = StreamUsage()
    client = _FakeClient(koerper)
    gedanken = []
    async for stueck in stream_responses(
        client, provider=_provider(), api_key="sk-test",
        messages=[{"role": "user", "content": "Status von Server 1?"}],
        usage=usage,
        tools=[{"type": "function",
                "function": {"name": "read_server_status", "parameters": {}}}],
        reasoning=True, reasoning_effort="high",
    ):
        if stueck.kind == "reasoning":
            gedanken.append(stueck.text)

    assert gedanken == ["**Checking server status**"]
    assert len(usage.tool_calls) == 1
    assert usage.tool_calls[0].name == "read_server_status"
    # `call_id` und nicht `id`: nur damit findet die Folgerunde den Aufruf.
    assert usage.tool_calls[0].id == "call_xyz"
    assert usage.tool_calls[0].arguments == {"server_id": 1}
    assert usage.reasoning_tokens == 32
    assert usage.anfragen == 1


@pytest.mark.asyncio
async def test_the_request_carries_effort_and_asks_for_a_summary() -> None:
    """Ohne ``summary`` schweigt der Strom, obwohl gedacht und abgerechnet wird.

    Und ``store: False``: ein Panel, das Serverkennungen und Logauszuege
    schickt, hat keinen Grund, den Lauf 30 Tage beim Anbieter liegen zu lassen.
    """
    client = _FakeClient(_sse(
        {"type": "response.output_text.delta", "delta": "ok"},
        {"type": "response.completed", "response": {"usage": {}}},
    ))
    async for _ in stream_responses(
        client, provider=_provider(), api_key="sk-test",
        messages=[{"role": "user", "content": "Hallo"}],
        usage=StreamUsage(), reasoning=True, reasoning_effort="high",
    ):
        pass
    assert client.gesendet is not None
    assert client.gesendet["reasoning"] == {"effort": "high", "summary": "auto"}
    assert client.gesendet["store"] is False
    assert "messages" not in client.gesendet
    assert client.gesendet["input"] == [{"role": "user", "content": "Hallo"}]


@pytest.mark.asyncio
async def test_the_mail_texts_forced_choice_leaves_flat() -> None:
    """Genau die Eingabe, die `ai_mail_text` schickt — bis in den Koerper.

    Ein ``function``-Unterobjekt im ``tool_choice`` ist hier ein 400, und der
    Fehlschlag waere unsichtbar: `ai_mail_text` faengt ihn ab und schickt
    seinen festen Text.
    """
    client = _FakeClient(_sse(
        {"type": "response.output_text.delta", "delta": "ok"},
        {"type": "response.completed", "response": {"usage": {}}},
    ))
    async for _ in stream_responses(
        client, provider=_provider(), api_key="sk-test",
        messages=[{"role": "user", "content": "Hallo"}], usage=StreamUsage(),
        tools=[{"type": "function",
                "function": {"name": "msm_mailtext", "parameters": {}}}],
        tool_choice={"type": "function", "function": {"name": "msm_mailtext"}},
    ):
        pass
    assert client.gesendet["tool_choice"] == {
        "type": "function", "name": "msm_mailtext",
    }


@pytest.mark.asyncio
async def test_a_stream_that_never_completes_is_an_error() -> None:
    """Ohne ``response.completed`` fehlt die Abrechnung — das ist kein Erfolg."""
    client = _FakeClient(_sse({"type": "response.output_text.delta", "delta": "halb"}))
    with pytest.raises(AiProviderRequestError) as fehler:
        async for _ in stream_responses(
            client, provider=_provider(), api_key="sk-test",
            messages=[{"role": "user", "content": "Hallo"}],
            usage=StreamUsage(),
        ):
            pass
    assert fehler.value.code == "AI_PROVIDER_STREAM_INCOMPLETE"


@pytest.mark.asyncio
async def test_broken_tool_arguments_do_not_reach_the_action_layer() -> None:
    """Kaputtes JSON wird hier abgewiesen, nicht erst beim Ausfuehren."""
    client = _FakeClient(_sse(
        {"type": "response.output_item.done",
         "item": {"id": "fc_1", "type": "function_call", "arguments": "{kaputt",
                  "call_id": "c1", "name": "t"}},
        {"type": "response.completed", "response": {"usage": {}}},
    ))
    with pytest.raises(AiProviderRequestError) as fehler:
        async for _ in stream_responses(
            client, provider=_provider(), api_key="sk-test",
            messages=[{"role": "user", "content": "x"}], usage=StreamUsage(),
        ):
            pass
    assert fehler.value.code == "AI_PROVIDER_PROTOCOL_ERROR"


@pytest.mark.asyncio
async def test_a_missing_key_never_reaches_the_network() -> None:
    provider = _provider()
    provider.requires_api_key = True
    with pytest.raises(AiProviderRequestError) as fehler:
        async for _ in stream_responses(
            _FakeClient(b""), provider=provider, api_key=None,
            messages=[], usage=StreamUsage(),
        ):
            pass
    assert fehler.value.code == "AI_PROVIDER_KEY_MISSING"
