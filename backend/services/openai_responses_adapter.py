"""OpenAIs Responses-API — derselbe Auftrag, ein anderer Dialekt.

Diese Datei ist der zweite Chatweg neben `openai_compatible_adapter`. Sie
existiert aus **einem** gemessenen Grund: OpenAIs ``/chat/completions`` lehnt
eine Anfrage ab, die ``tools`` *und* eine echte Denkstufe traegt::

    Function tools with reasoning_effort are not supported for <modell> in
    /v1/chat/completions. To use function tools, use /v1/responses or set
    reasoning_effort to 'none'.

Gemessen am 2026-08-18 gegen OpenAI direkt, jeweils mit Werkzeugkatalog:
``gpt-5.6-luna`` nimmt nur ``none``, ``gpt-5.2`` und ``gpt-5.1`` jede Stufe,
``gpt-5-mini`` jede **ausser** ``none``. Die Grenze gehoert also dem Endpunkt
und nicht dem Modell.

Der billigere Ausweg — Stufe auf ``none`` senken, sobald Werkzeuge mitfahren —
stand kurzzeitig in `ai_reasoning.klemmen` und ist verworfen. MSM schickt im
Chat immer Werkzeuge mit; ein Zugang, an dem Nachdenken und Werkzeuge einander
ausschliessen, taugt nicht fuer den Hintergrund-Worker, der gerade denken
soll, waehrend er arbeitet.

**Was hier anders ist als im Schwestermodul** — und mehr ist es nicht:

* ``input`` statt ``messages``, mit eigenen Positionsarten statt Rollen.
* ``tools`` flach (``{"type": "function", "name": ..., "parameters": ...}``)
  statt in ein ``function``-Unterobjekt gewickelt.
* Getippte Ereignisse (``response.output_text.delta``) statt ``choices[].delta``.
* Werkzeugergebnisse gehen als ``function_call_output`` zurueck, nicht als
  ``role="tool"``.

**Was hier gleich ist** — und das ist der Punkt: die Signatur, die
`StreamChunk`-Stuecke, das Fuellen von `StreamUsage`, die Laengengrenzen und
die Fehlercodes. Der Aufrufer merkt nicht, welcher Dialekt gesprochen wurde;
`ai_stream_service` hat keine einzige Verzweigung dafuer. Die Wahl trifft
`Anbieter.protokoll_chat` in der Anbieterdatei.

Die Uebersetzung sitzt bewusst **hier** und nicht in `ai_context_service`: der
Verlauf wird einmal gebaut, in der Form, die MSM ueberall verwendet, und erst
an der Aussenkante in die Mundart des Anbieters gebracht. Andersherum haetten
zwei Anbieter zwei Verlaufsformate — und jede spaetere Aenderung am Kontext
muesste an zwei Stellen richtig sein.
"""

from __future__ import annotations

import json
import logging
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
)


logger = logging.getLogger(__name__)


# ── Uebersetzung: MSMs Verlauf in OpenAIs `input` ─────────────────────


def _werkzeuge_uebersetzen(tools: list[dict] | None) -> list[dict] | None:
    """Werkzeugkatalog aus der Chat-Completions-Form in die flache Form.

    Beide beschreiben dasselbe, nur eine Schachtelungsebene auseinander::

        {"type": "function", "function": {"name": ..., "parameters": ...}}
        {"type": "function", "name": ..., "parameters": ...}

    Ein Katalog, der bereits flach ankommt, geht unveraendert durch — dann hat
    ihn ein Aufrufer schon in dieser Mundart gebaut, und ihn ein zweites Mal
    auszupacken wuerde ihn zerstoeren.
    """
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


def _text_aus_inhalt(content: Any) -> str:
    """Der Textanteil einer Nachricht, gleich in welcher Form er ankommt.

    MSM baut Inhalte meist als schlichte Zeichenkette, bei Anhaengen aber als
    Liste getippter Bloecke. Beide Formen kommen hier an, und die Liste darf
    nicht als ``str(...)`` im Prompt landen — das waere Python-Syntax im Text
    des Benutzers.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        stuecke: list[str] = []
        for block in content:
            if isinstance(block, str):
                stuecke.append(block)
            elif isinstance(block, dict):
                wert = block.get("text")
                if isinstance(wert, str):
                    stuecke.append(wert)
        return "".join(stuecke)
    return ""


def nachrichten_uebersetzen(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MSMs Verlauf in OpenAIs ``input``-Liste.

    Vier Formen kommen herein, und jede hat genau eine Entsprechung:

    * ``system`` — bleibt eine Position mit derselben Rolle. (Nicht als
      ``instructions`` herausgezogen: MSM setzt den Systemtext an den Anfang
      des Verlaufs, und ein zweiter Weg fuer dieselbe Sache waere eine Stelle
      mehr, an der eine Faltung ihn verlieren kann.)
    * ``user`` — ebenso.
    * ``assistant`` **mit** ``tool_calls`` — wird zu *mehreren* Positionen: der
      gesagte Text als Nachricht, dazu je Aufruf eine ``function_call``-Position.
      Der Text darf nicht wegfallen; er traegt die Ansagen, auf die sich das
      Modell in der Folgerunde bezieht.
    * ``tool`` — wird zu ``function_call_output`` mit der ``call_id`` des
      Aufrufs. Das ist der Rueckkanal, den die Responses-API kennt; ein
      ``role="tool"`` wuerde sie mit einem 400 abweisen.

    ``assistant`` ohne Werkzeuge bekommt ``output_text`` statt ``input_text``
    als Inhaltsart — die API unterscheidet, wer gesprochen hat, nicht nur
    ueber die Rolle.
    """
    eingabe: list[dict[str, Any]] = []
    for nachricht in messages:
        if not isinstance(nachricht, dict):
            continue
        rolle = nachricht.get("role")
        inhalt = _text_aus_inhalt(nachricht.get("content"))

        if rolle == "tool":
            # Der Rueckkanal. Ohne `call_id` findet die API den Aufruf nicht,
            # zu dem dieses Ergebnis gehoert — und antwortet mit einem 400,
            # das wie ein kaputtes Werkzeug aussieht.
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
            eingabe.append({"role": rolle, "content": inhalt})
    return eingabe


# ── Der Strom ─────────────────────────────────────────────────────────


def _fehler_im_ereignis(rahmen: dict) -> tuple[str, str | None] | None:
    """Ein Fehler, der **im** Strom gemeldet wird statt als Status.

    Zwei Formen: ein eigenes ``response.failed``-Ereignis, und ein ``error``
    neben den uebrigen Feldern. Beide werden hier zu derselben Marke wie im
    Schwestermodul, damit der Aufrufer nicht zwei Fehlersprachen kennen muss.
    """
    typ = rahmen.get("type")
    if typ in ("response.failed", "response.incomplete", "error"):
        antwort = rahmen.get("response")
        fehler = None
        if isinstance(antwort, dict):
            fehler = antwort.get("error") or antwort.get("incomplete_details")
        if fehler is None:
            fehler = rahmen.get("error") or rahmen
        nachricht = ""
        if isinstance(fehler, dict):
            nachricht = str(fehler.get("message") or fehler.get("reason") or "")
        return "AI_PROVIDER_REQUEST_REJECTED", _kurzfassung(nachricht or str(typ))
    return None


def _usage_uebernehmen(usage: StreamUsage, rohdaten: Any) -> None:
    """Die Abrechnung aus ``response.completed``.

    Andere Feldnamen als bei Chat Completions (``input_tokens`` statt
    ``prompt_tokens``), dieselbe Bedeutung. ``reasoning_tokens`` und
    ``cached_tokens`` sind Teilmengen und werden nicht addiert — wer sie
    aufschlaegt, zaehlt doppelt.
    """
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


async def stream_responses(
    client: httpx.AsyncClient,
    *,
    provider,
    api_key: str | None,
    messages: list[dict[str, Any]],
    usage: StreamUsage,
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    reasoning: bool = False,
    reasoning_effort: str | None = None,
    cache_marke: bool = False,
) -> AsyncIterator[StreamChunk]:
    """Ein Chatzug ueber ``POST /responses`` — Stuecke wie ueberall sonst.

    Dieselbe Signatur wie `openai_compatible_adapter.stream_chat_completion`,
    einschliesslich der Parameter, die dieser Weg nicht kennt. ``cache_marke``
    ist einer davon: OpenAI speichert von selbst zwischen und meldet es in
    ``input_tokens_details.cached_tokens``; eine Marke waere hier wirkungslos
    und ihr Fehlen ist kein Verlust. Der Parameter bleibt trotzdem stehen,
    damit beide Wege dieselbe Form haben und der Aufrufer keinen Unterschied
    kennen muss.

    ``tool_choice="none"`` wird uebersetzt: die Responses-API kennt dafuer
    dieselbe Zeichenkette. Die Schlussrunde eines Laufs schickt sie, um noch
    einen Satz zu bekommen, aber keinen Aufruf mehr.

    **Denkschritte kommen als Zusammenfassung**, nicht als rohe Kette —
    ``reasoning.summary: "auto"``. Ohne diese Zeile schweigt der Strom dazu,
    obwohl gedacht und abgerechnet wird: gemessen 904 ``reasoning_tokens`` bei
    ``effort: high``, und der Benutzer haette einen leeren Denkkasten gesehen
    und eine auffaellig lange Pause.
    """
    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request_body: dict[str, Any] = {
        "model": model or provider.default_model,
        "input": nachrichten_uebersetzen(messages),
        "stream": True,
        # Ohne das speichert OpenAI den Lauf 30 Tage auf seinen Servern. Ein
        # Panel, das Serverkennungen und Logauszuege durch dieses Feld schickt,
        # hat dafuer keinen Grund: MSM fuehrt den Verlauf selbst und schickt
        # ihn bei jeder Runde vollstaendig mit.
        "store": False,
    }
    flache_werkzeuge = _werkzeuge_uebersetzen(tools)
    if flache_werkzeuge:
        request_body["tools"] = flache_werkzeuge
        request_body["tool_choice"] = tool_choice or "auto"
    if reasoning and reasoning_effort:
        request_body["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    elif reasoning_effort:
        # „Aus" ist bei OpenAI die Stufe `none` und kein Schalter. Ohne
        # Zusammenfassung: es gibt keine.
        request_body["reasoning"] = {"effort": reasoning_effort}

    target = httpx.URL(provider_base_url(provider).rstrip("/") + "/responses")
    deadline = time.monotonic() + MAX_STREAM_SECONDS
    frames = 0
    # Aufrufe werden ueber ihre `item_id` gesammelt: die Argumente kommen in
    # Stuecken, und mehrere Aufrufe einer Runde laufen verschraenkt ein.
    aufrufe: dict[str, dict[str, str]] = {}
    reihenfolge: list[str] = []
    abgeschlossen = False
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

            # Ab hier ist die Anfrage angekommen und wird abgerechnet, auch
            # wenn der Strom gleich abbricht — gezaehlt wird deshalb hier.
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

                if typ == "response.output_text.delta":
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
                            aufrufe[kennung] = {
                                "call_id": posten.get("call_id") or "",
                                "name": posten.get("name") or "",
                                "arguments": posten.get("arguments") or "",
                            }

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
                    # Der vollstaendige Posten. Er ueberschreibt das Gesammelte:
                    # hier stehen die Argumente am Stueck, und ein verlorenes
                    # Delta faellt damit nicht ins Gewicht.
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

                elif typ == "response.completed":
                    abgeschlossen = True
                    antwort = rahmen.get("response")
                    if isinstance(antwort, dict):
                        _usage_uebernehmen(usage, antwort.get("usage"))

            if not abgeschlossen:
                raise AiProviderRequestError("AI_PROVIDER_STREAM_INCOMPLETE")

            for kennung in reihenfolge:
                eintrag = aufrufe.get(kennung)
                if not eintrag or not eintrag["call_id"] or not eintrag["name"]:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                try:
                    argumente = json.loads(eintrag["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
                if not isinstance(argumente, dict):
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                # `call_id` und nicht `id`: das ist die Kennung, die beim
                # Rueckkanal wieder gebraucht wird (`function_call_output`).
                usage.tool_calls.append(ProviderToolCall(
                    id=eintrag["call_id"], name=eintrag["name"], arguments=argumente
                ))
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


def spricht_responses(provider) -> bool:
    """Ob dieser Zugang den Responses-Weg spricht.

    Gefragt wird die Anbieterdatei, nicht der Name. Ein unbekannter
    ``provider_kind`` ergibt ``False`` — also den verbreiteten Weg, wie vor
    diesem Modul.
    """
    try:
        return (
            ai_provider_registry.anbieter(provider.provider_kind).protokoll_chat
            == "responses"
        )
    except KeyError:
        return False
