"""Anthropics Messages-API — derselbe Auftrag, der dritte Dialekt.

Der dritte Chatweg neben `openai_compatible_adapter` und
`openai_responses_adapter`. Anders als bei OpenAIs `/responses` ist der Anlass
hier keine Messung, sondern eine schlichte Tatsache: Claude auf Azure **hat**
kein ``/chat/completions``. Microsoft und Anthropic nennen beide denselben
Endpunkt — ``https://{resource}.services.ai.azure.com/anthropic/v1/messages``,
mit ``anthropic-version: 2023-06-01`` als Kopf.

**Was hier anders ist als in den Schwestermodulen:**

* ``system`` steht **neben** ``messages`` und ist keine Rolle. Ein
  ``{"role": "system"}`` in der Liste weist die API ab.
* Inhalte sind Bloecke (``text``, ``thinking``, ``tool_use``, ``tool_result``)
  und nicht Text plus ein Nebenfeld.
* Werkzeugergebnisse gehen als ``tool_result``-Block in einer **Benutzer**-
  nachricht zurueck, nicht als eigene Rolle. Mehrere Ergebnisse derselben Runde
  gehoeren dabei in **eine** Nachricht.
* ``tools`` tragen ``input_schema`` statt eines ``function``-Unterobjekts, und
  ``tool_choice`` ist ein Objekt (``{"type": "auto"}``) statt einer Zeichenkette.
* ``max_tokens`` ist **Pflicht**. Bei den anderen beiden ist es optional.
* Der Strom besteht aus benannten Ereignissen und endet mit ``message_stop`` —
  ein ``data: [DONE]`` gibt es seit ``anthropic-version: 2023-06-01`` nicht.

**Was hier gleich ist** — und das ist der Punkt: die Signatur, die
`StreamChunk`-Stuecke, das Fuellen von `StreamUsage`, die Laengengrenzen und die
Fehlercodes. Der Aufrufer merkt nicht, welcher Dialekt gesprochen wurde;
`ai_stream_service` hat keine einzige Verzweigung dafuer. Die Wahl trifft
`Anbieter.protokoll_chat` in der Anbieterdatei — **nie** der Modellname. Bei
Azure kann dieselbe Ressource GPT- und Claude-Deployments fuehren, und wie ein
Deployment heisst, entscheidet der Betreiber.

**Nachdenken ist ``output_config.effort`` und nicht ``budget_tokens``.** Die
alte Form ``thinking: {"type": "enabled", "budget_tokens": N}`` fuehrt
Microsofts Modelltabelle fuer ``claude-opus-5``, ``claude-sonnet-5``,
``claude-fable-5``, ``claude-opus-4-8`` und ``claude-opus-4-7`` in der Spalte
``enabled`` mit **„No"** — dort wird eine Anfrage damit abgelehnt. Die aktuelle
Form ist ``thinking: {"type": "adaptive"}`` plus ``output_config: {"effort":
…}``, und deren Wortschatz ist wortgleich mit `ai_reasoning.RANGFOLGE`. Damit
braucht es keine Umrechnung von einem Stufenwort in eine Tokenzahl — also auch
keine Zahlentabelle im Code, gegen die `ai_provider_registry` gebaut ist.
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
    schluesselkopf,
)


logger = logging.getLogger(__name__)

#: Die Fassung der Messages-API, gegen die MSM spricht. Es gibt genau zwei
#: (``2023-01-01`` und diese); sie ist seit Jahren die aktuelle und bringt das
#: benannte Ereignisformat mit, das dieser Adapter liest.
#:
#: Steht **hier** und nicht in der Anbieterdatei, weil sie zum Dialekt gehoert
#: und nicht zum Anbieter: jeder Weg, der diese API spricht — Azure, Bedrock,
#: Anthropic direkt —, verlangt genau diesen Kopf mit genau diesem Wert. Ein
#: Feld in der Registry waere dieselbe Zeichenkette in jeder Anbieterdatei.
#: Dieselbe Ueberlegung wie bei ``store: False`` im Responses-Adapter.
ANTHROPIC_VERSION = "2023-06-01"

#: Wieviel die Antwort hoechstens umfassen darf. Die Messages-API verlangt das
#: Feld — es gibt hier keinen Weg, es wegzulassen.
#:
#: Die Zahl ist bewusst dieselbe wie `ai_context_window.RESERVE_AUSGABE_TOKENS`
#: und nicht frei gewaehlt: dort zieht MSM genau diesen Platz vom Kontextfenster
#: ab, wenn der Katalog keine Ausgabegrenze nennt. Eine groessere Zahl hier
#: hiesse, mehr Raum zu verlangen, als die Kontextrechnung eingeplant hat — eine
#: kleinere, den eingeplanten zu verschenken.
#:
#: Der Wert wird **nicht** von dort importiert, aus demselben Grund wie
#: `openai_compatible_adapter.MIKRO_JE_USD`: dieser Adapter kennt keine
#: Kontextrechnung, er liest ein Protokoll. Ein Import in diese Richtung waere
#: der Anfang eines Zyklus. Wer die eine Zahl aendert, aendert die andere mit —
#: dafuer steht der Satz hier.
STANDARD_MAX_TOKENS = 8_192

#: Anthropics Fehlerarten, uebersetzt in MSMs Marken. Dieselbe Aufgabe wie
#: `openai_compatible_adapter._error_code` fuer HTTP-Status, nur dass diese API
#: **im Strom** ein Wort meldet statt einer Zahl: sind die Kopfzeilen erst
#: draussen, steht der Status auf 200 und laesst sich nicht mehr aendern.
#:
#: Eine Tabelle und keine Verzweigung, weil es genau eine Zuordnung ist und die
#: Liste dem Anbieter gehoert. Was nicht darin steht, wird zu
#: ``AI_PROVIDER_REQUEST_REJECTED``; die Einzelheit traegt dann der Text.
_FEHLERARTEN = {
    "authentication_error": "AI_PROVIDER_AUTH_FAILED",
    "permission_error": "AI_PROVIDER_AUTH_FAILED",
    "not_found_error": "AI_PROVIDER_ENDPOINT_NOT_FOUND",
    "rate_limit_error": "AI_PROVIDER_RATE_LIMITED",
    "overloaded_error": "AI_PROVIDER_UNAVAILABLE",
    "api_error": "AI_PROVIDER_UNAVAILABLE",
    "timeout_error": "AI_PROVIDER_UNAVAILABLE",
}


# ── Uebersetzung: MSMs Verlauf in Anthropics `messages` ────────────────


def _text_aus_inhalt(content: Any) -> str:
    """Der Textanteil einer Nachricht, gleich in welcher Form er ankommt.

    Wortgleich zur Fassung im Responses-Adapter und trotzdem eine eigene:
    beide Adapter sind Aussenkanten, und eine gemeinsame Hilfsfunktion
    zwischen zweien von ihnen waere eine dritte Stelle, an der eine Aenderung
    an einem Dialekt versehentlich den anderen trifft.

    MSM baut Inhalte meist als schlichte Zeichenkette, bei Anhaengen aber als
    Liste getippter Bloecke. Beide Formen kommen hier an, und die Liste darf
    nicht als ``str(...)`` im Prompt landen.
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


def werkzeuge_uebersetzen(tools: list[dict] | None) -> list[dict] | None:
    """Werkzeugkatalog aus der Chat-Completions-Form in Anthropics Form.

    Beide beschreiben dasselbe mit anderen Namen::

        {"type": "function", "function": {"name": …, "parameters": {…}}}
        {"name": …, "description": …, "input_schema": {…}}

    Ein Eintrag ohne ``function``-Unterobjekt geht unveraendert durch — dann hat
    ihn ein Aufrufer bereits in dieser Mundart gebaut, und ihn ein zweites Mal
    auszupacken wuerde ihn zerstoeren. Dieselbe Nachsicht wie im
    Responses-Adapter.

    ``input_schema`` ist **Pflicht**; ein Werkzeug ohne Parameter bekommt das
    leere Objektschema statt ``None``, sonst weist die API den ganzen Katalog
    ab und mit ihm jede Anfrage der Sitzung.
    """
    if not tools:
        return None
    uebersetzt: list[dict] = []
    for eintrag in tools:
        if not isinstance(eintrag, dict):
            continue
        funktion = eintrag.get("function")
        if not isinstance(funktion, dict):
            uebersetzt.append(eintrag)
            continue
        schema = funktion.get("parameters")
        uebersetzt.append({
            "name": funktion.get("name"),
            "description": funktion.get("description") or "",
            "input_schema": (
                schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
            ),
        })
    return uebersetzt or None


def werkzeugwahl_uebersetzen(tool_choice: str | dict | None) -> dict:
    """``tool_choice`` in Anthropics Objektform.

    Drei Formen kommen aus MSM herein, und jede hat genau eine Entsprechung:

    * ``None`` und ``"auto"`` — ``{"type": "auto"}``, das Modell entscheidet.
      Der Normalfall im Chat.
    * ``"none"`` — ``{"type": "none"}``. Die Schlussrunde eines Laufs
      (`ai_stream_service`) will noch einen Satz, aber keinen Aufruf mehr.
    * ``{"type": "function", "function": {"name": X}}`` — OpenAIs Form fuer
      „genau dieses Werkzeug", bei Anthropic ``{"type": "tool", "name": X}``.
      Der einzige Aufrufer ist `ai_mail_text`, der kein Prosa-Ergebnis will,
      sondern ein ausgefuelltes Formular.

    Was sich keiner dieser Formen zuordnen laesst, wird zu ``auto``. Ein
    unbekannter Zwang waere sonst eine Anfrage, die der Anbieter mit einem 400
    beantwortet — und „das Modell entscheidet" ist die harmlose Auslegung.
    """
    if isinstance(tool_choice, dict):
        funktion = tool_choice.get("function")
        name = funktion.get("name") if isinstance(funktion, dict) else tool_choice.get("name")
        if isinstance(name, str) and name:
            return {"type": "tool", "name": name}
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "none"}
    if tool_choice == "any":
        return {"type": "any"}
    return {"type": "auto"}


def _anhaengen(ziel: list[dict[str, Any]], rolle: str, bloecke: list[dict]) -> None:
    """Bloecke an die Liste haengen — an die letzte Nachricht, wenn die Rolle passt.

    **Zusammenfassen ist hier keine Kosmetik, sondern eine Vorgabe der API.**
    Alle ``tool_result``-Bloecke, die zu einer Werkzeugrunde gehoeren, muessen in
    **einer** Benutzernachricht stehen. MSM fuehrt jedes Ergebnis als eigene
    Nachricht mit ``role="tool"``; drei parallele Aufrufe ergaeben also drei
    Nachrichten hintereinander, und die API antwortet darauf mit einem 400.

    Denselben Dienst tut es nebenbei fuer aufeinanderfolgende Nachrichten
    derselben Rolle aus anderer Ursache — eine Systemnachricht mitten im
    Verlauf zieht ihre Nachbarn sonst auseinander.
    """
    if not bloecke:
        return
    if ziel and ziel[-1]["role"] == rolle:
        ziel[-1]["content"].extend(bloecke)
        return
    ziel.append({"role": rolle, "content": bloecke})


def nachrichten_uebersetzen(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """MSMs Verlauf in ``(system, messages)``.

    Vier Formen kommen herein:

    * ``system`` — wandert **aus** der Liste heraus in das oberste
      ``system``-Feld. Mehrere werden mit Leerzeilen verbunden; MSM setzt zwar
      genau einen Systemtext an den Anfang, aber `ai_lage` haengt spaet eine
      zweite Systemnachricht an, und die darf nicht verlorengehen.
    * ``user`` — ein ``text``-Block.
    * ``assistant`` **mit** ``tool_calls`` — ein ``text``-Block (falls etwas
      gesagt wurde) und je Aufruf ein ``tool_use``-Block. Der Text darf nicht
      wegfallen; er traegt die Ansagen, auf die sich das Modell in der
      Folgerunde bezieht.
    * ``tool`` — ein ``tool_result``-Block in einer **Benutzer**nachricht.

    ``arguments`` kommen bei MSM als JSON-**Zeichenkette** (so schickt es
    OpenAI), Anthropic will an dieser Stelle ein Objekt. Laesst sich die Kette
    nicht lesen, wird das leere Objekt gesetzt statt die Runde abzubrechen: der
    Aufruf ist bereits gelaufen, sein Ergebnis steht in der naechsten Nachricht,
    und ein Abbruch hier verloere einen Verlauf wegen eines Arguments, das
    niemand mehr braucht.

    Leere Textbloecke werden ausgelassen — die API weist sie ab. Eine
    Assistentennachricht ohne Text und ohne Aufrufe faellt damit ganz weg, und
    das ist richtig: eine Nachricht mit leerem ``content`` ist ebenfalls ein 400.
    """
    systemtexte: list[str] = []
    ausgabe: list[dict[str, Any]] = []

    for nachricht in messages:
        if not isinstance(nachricht, dict):
            continue
        rolle = nachricht.get("role")
        inhalt = _text_aus_inhalt(nachricht.get("content"))

        if rolle == "system":
            if inhalt:
                systemtexte.append(inhalt)
            continue

        if rolle == "tool":
            _anhaengen(ausgabe, "user", [{
                "type": "tool_result",
                "tool_use_id": nachricht.get("tool_call_id"),
                # Auch ein leeres Ergebnis braucht einen Inhalt; ein Werkzeug,
                # das nichts zurueckgibt, ist kein Fehler.
                "content": inhalt or "",
            }])
            continue

        if rolle == "assistant":
            bloecke: list[dict] = []
            if inhalt:
                bloecke.append({"type": "text", "text": inhalt})
            aufrufe = nachricht.get("tool_calls")
            if isinstance(aufrufe, list):
                for aufruf in aufrufe:
                    if not isinstance(aufruf, dict):
                        continue
                    funktion = aufruf.get("function")
                    if not isinstance(funktion, dict):
                        continue
                    roh = funktion.get("arguments") or "{}"
                    try:
                        argumente = json.loads(roh)
                    except (TypeError, json.JSONDecodeError):
                        argumente = {}
                    bloecke.append({
                        "type": "tool_use",
                        "id": aufruf.get("id"),
                        "name": funktion.get("name"),
                        "input": argumente if isinstance(argumente, dict) else {},
                    })
            _anhaengen(ausgabe, "assistant", bloecke)
            continue

        # ``user`` und alles Uebrige (``developer`` gibt es hier nicht) landen
        # als Benutzertext. Ein unbekannter Rollenname als Rolle
        # weiterzureichen waere ein 400; als Benutzertext ist er wenigstens
        # lesbar.
        if inhalt:
            _anhaengen(ausgabe, "user", [{"type": "text", "text": inhalt}])

    return ("\n\n".join(systemtexte) or None), ausgabe


# ── Der Strom ─────────────────────────────────────────────────────────


def _fehler_im_ereignis(rahmen: dict) -> tuple[str, str | None] | None:
    """Ein Fehler, den der Anbieter **mitten im Strom** meldet — oder ``None``.

    Sind die Kopfzeilen erst draussen, steht der Status auf 200 und laesst sich
    nicht mehr aendern; ein Fehler kommt dann als ``error``-Ereignis. Ohne diese
    Auswertung endete er als „Die Antwort des Anbieters brach vorzeitig ab" —
    derselbe blinde Fleck, den `openai_compatible_adapter._fehler_im_rahmen`
    fuer OpenRouter beschreibt.
    """
    if rahmen.get("type") != "error":
        return None
    fehler = rahmen.get("error")
    if not isinstance(fehler, dict):
        return "AI_PROVIDER_REQUEST_REJECTED", None
    marke = _FEHLERARTEN.get(fehler.get("type"), "AI_PROVIDER_REQUEST_REJECTED")
    nachricht = fehler.get("message")
    if not isinstance(nachricht, str) or not nachricht:
        nachricht = str(fehler.get("type") or "")
    return marke, _kurzfassung(nachricht)


def _usage_aus_start(usage: StreamUsage, rohdaten: Any) -> None:
    """Die Eingabezahlen aus ``message_start``.

    Anthropic meldet die Eingabe **dreigeteilt**: ``input_tokens`` sind die
    frisch gelesenen, ``cache_read_input_tokens`` die aus dem Zwischenspeicher,
    ``cache_creation_input_tokens`` die dort hineingeschriebenen. Alle drei
    kosten, und zusammen sind sie der Prompt.

    MSM fuehrt das anders herum: ``prompt_tokens`` ist die **Summe**, und
    ``cached_tokens``/``cache_write_tokens`` sind Teilmengen davon (siehe
    `StreamUsage`). Deshalb wird hier addiert statt durchgereicht — wer
    Anthropics ``input_tokens`` unbesehen als ``prompt_tokens`` uebernaehme,
    unterschluege bei einem gut gefuellten Zwischenspeicher den groessten Teil
    des Prompts, und die Kostenschaetzung waere um ein Vielfaches zu niedrig.
    """
    if not isinstance(rohdaten, dict):
        return
    frisch = _ganzzahl(rohdaten.get("input_tokens")) or 0
    gelesen = _ganzzahl(rohdaten.get("cache_read_input_tokens")) or 0
    geschrieben = _ganzzahl(rohdaten.get("cache_creation_input_tokens")) or 0
    usage.prompt_tokens = frisch + gelesen + geschrieben
    usage.cached_tokens += gelesen
    usage.cache_write_tokens += geschrieben
    usage.vom_anbieter = True


def _usage_aus_delta(usage: StreamUsage, rohdaten: Any) -> None:
    """Die Ausgabezahl aus ``message_delta`` — **gesetzt**, nicht addiert.

    Anthropic dokumentiert es ausdruecklich: „The token counts shown in the
    ``usage`` field of the ``message_delta`` event are *cumulative*." Ein
    ``+=`` ueber mehrere ``message_delta``-Ereignisse zaehlte dieselben Tokens
    mehrfach, und zwar in genau der Zahl, mit der abgerechnet wird.

    ``reasoning_tokens`` bleibt bei null: Anthropic weist Denkschritte nicht
    getrennt aus, sie stecken in ``output_tokens``. Null heisst hier „nicht
    gemeldet" — die Denkzeichen zaehlt `StreamUsage.reasoning_chars` daneben
    ohnehin mit.
    """
    if not isinstance(rohdaten, dict):
        return
    ausgabe = _ganzzahl(rohdaten.get("output_tokens"))
    if ausgabe is None:
        return
    usage.completion_tokens = ausgabe
    usage.total_tokens = (usage.prompt_tokens or 0) + ausgabe
    usage.vom_anbieter = True


async def stream_messages(
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
    """Ein Chatzug ueber ``POST /messages`` — Stuecke wie ueberall sonst.

    Dieselbe Signatur wie `openai_compatible_adapter.stream_chat_completion`,
    einschliesslich der Parameter, die dieser Weg nicht auswertet.

    ``cache_marke`` ist einer davon, und das ist eine Entscheidung und kein
    Versehen: Anthropic kennt Prompt-Caching sehr wohl, verlangt die Marke aber
    **am Inhaltsblock** (``cache_control`` am letzten stabilen Block). Welcher
    Block das ist, weiss diese Schicht nicht — das weiss `ai_context_service`.
    Ein falsch gesetzter Haltepunkt kostet mehr als ein fehlender; dieselbe
    Ueberlegung steht bei ``cache_marke`` im Schwestermodul.

    ``reasoning`` und ``reasoning_effort`` werden zu ``thinking`` und
    ``output_config.effort``. Ohne feststehende Stufe geht **gar nichts**
    hinaus: bei Azure hat MSM keinen Katalog, aus dem sich die zulaessigen
    Stufen dieses Deployments ergaeben (`ai_provider_registry.azure_anthropic`),
    und eine geratene Stufe waere ein 400 statt einer Denktiefe.

    ``model`` ist der **Deployment-Name** des Betreibers, nicht die Modell-ID
    von Anthropic. Beides kann gleich lauten, muss aber nicht.
    """
    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    headers = schluesselkopf(
        ai_provider_registry.anbieter(provider.provider_kind), api_key
    )
    headers["anthropic-version"] = ANTHROPIC_VERSION

    system, verlauf = nachrichten_uebersetzen(messages)
    request_body: dict[str, Any] = {
        "model": model or provider.default_model,
        "max_tokens": STANDARD_MAX_TOKENS,
        "messages": verlauf,
        "stream": True,
    }
    if system:
        request_body["system"] = system
    uebersetzte_werkzeuge = werkzeuge_uebersetzen(tools)
    if uebersetzte_werkzeuge:
        request_body["tools"] = uebersetzte_werkzeuge
        request_body["tool_choice"] = werkzeugwahl_uebersetzen(tool_choice)
    if reasoning and reasoning_effort:
        # ``adaptive`` und nicht ``enabled``: die aeltere Form verlangt
        # ``budget_tokens``, und die aktuellen Modelle lehnen sie ab. Die Tiefe
        # steht daneben, im Wortschatz von `ai_reasoning.RANGFOLGE`.
        request_body["thinking"] = {"type": "adaptive"}
        request_body["output_config"] = {"effort": reasoning_effort}

    # ``/v1/messages`` und nicht nur ``/messages``: die Adresse des Anbieters
    # endet bei Anthropic **vor** der Version. Azure zeigt sie im Portal genau
    # so an (``…/anthropic``), Anthropics eigenes SDK haengt ebenfalls
    # ``/v1/messages`` an eine ``base_url`` — ein ``/v1`` in der Registry
    # ergaebe mit beiden zusammen ``…/anthropic/v1/v1/messages``.
    target = httpx.URL(provider_base_url(provider).rstrip("/") + "/v1/messages")
    deadline = time.monotonic() + MAX_STREAM_SECONDS
    frames = 0
    # Aufrufe werden ueber den ``index`` ihres Inhaltsblocks gesammelt: die
    # Argumente kommen in Stuecken, und mehrere Aufrufe einer Runde laufen
    # verschraenkt ein.
    aufrufe: dict[int, dict[str, str]] = {}
    reihenfolge: list[int] = []
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
                # ``event:``-Zeilen werden uebergangen: der Ereignisname steht
                # zusaetzlich als ``type`` in der Nutzlast, und eine zweite
                # Quelle fuer dieselbe Angabe waere eine Stelle mehr, an der
                # beide auseinanderlaufen koennen.
                if not line or not line.startswith("data:"):
                    continue
                frames += 1
                if frames > MAX_STREAM_FRAMES:
                    raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                nutzlast = line[5:].strip()
                if not nutzlast:
                    continue
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

                if typ == "message_start":
                    nachricht = rahmen.get("message")
                    if isinstance(nachricht, dict):
                        _usage_aus_start(usage, nachricht.get("usage"))

                elif typ == "content_block_start":
                    block = rahmen.get("content_block")
                    index = rahmen.get("index")
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and isinstance(index, int)
                    ):
                        if index not in aufrufe:
                            reihenfolge.append(index)
                        aufrufe[index] = {
                            "id": block.get("id") or "",
                            "name": block.get("name") or "",
                            "arguments": "",
                        }

                elif typ == "content_block_delta":
                    delta = rahmen.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    art = delta.get("type")
                    if art == "text_delta":
                        stueck = delta.get("text")
                        if not isinstance(stueck, str) or not stueck:
                            continue
                        usage.output_chars += len(stueck)
                        if usage.output_chars > MAX_ASSISTANT_CHARS:
                            raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                        yield StreamChunk("content", stueck)
                    elif art == "thinking_delta":
                        gedanke = delta.get("thinking")
                        if not isinstance(gedanke, str) or not gedanke:
                            continue
                        usage.reasoning_chars += len(gedanke)
                        if usage.reasoning_chars <= MAX_REASONING_CHARS:
                            yield StreamChunk("reasoning", gedanke)
                    elif art == "input_json_delta":
                        index = rahmen.get("index")
                        stueck = delta.get("partial_json")
                        if not isinstance(index, int) or not isinstance(stueck, str):
                            continue
                        eintrag = aufrufe.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        if index not in reihenfolge:
                            reihenfolge.append(index)
                        eintrag["arguments"] += stueck
                        if len(eintrag["arguments"]) > MAX_TOOL_ARGUMENT_CHARS:
                            raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                    # ``signature_delta`` traegt keinen lesbaren Text, sondern
                    # die Signatur eines Denkblocks. Uebergangen, wie jede
                    # unbekannte Art: die Doku verlangt es ausdruecklich
                    # („your code should handle unknown event types gracefully").

                elif typ == "message_delta":
                    _usage_aus_delta(usage, rahmen.get("usage"))

                elif typ == "message_stop":
                    abgeschlossen = True
                    break

            if not abgeschlossen:
                raise AiProviderRequestError("AI_PROVIDER_STREAM_INCOMPLETE")

            for index in reihenfolge:
                eintrag = aufrufe.get(index)
                if not eintrag or not eintrag["id"] or not eintrag["name"]:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                try:
                    # Ein Werkzeug ohne Parameter bekommt gar kein
                    # ``input_json_delta``; die leere Kette ist dort das
                    # leere Objekt und kein Protokollfehler.
                    argumente = json.loads(eintrag["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
                if not isinstance(argumente, dict):
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                usage.tool_calls.append(ProviderToolCall(
                    id=eintrag["id"], name=eintrag["name"], arguments=argumente
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


def spricht_messages(provider) -> bool:
    """Ob dieser Zugang die Messages-API spricht.

    Gefragt wird die Anbieterdatei, nicht der Name des Modells. Ein unbekannter
    ``provider_kind`` ergibt ``False`` — also den verbreiteten Weg, wie vor
    diesem Modul.
    """
    try:
        return (
            ai_provider_registry.anbieter(provider.provider_kind).protokoll_chat
            == "anthropic_messages"
        )
    except KeyError:
        return False
