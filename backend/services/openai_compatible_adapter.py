"""Kleiner OpenAI-kompatibler Streaming-Adapter auf Basis von httpx."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

from models import AiProvider
from services import ai_provider_registry
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
# Der Anbieter meldet Kosten als Fliesskommazahl in USD ("cost": 0.0021). Die
# Abrechnung rechnet ganzzahlig, weil sich Betraege sonst ueber tausend Zeilen
# hinweg auseinanderaddieren. Umgerechnet wird deshalb genau einmal, hier, beim
# Lesen — danach ist die Zahl eine ganze.
#
# 1 USD = 100 Cent, 1 Cent = 10.000 Microunits
# (`ai_usage_service.MICROUNITS_PER_CENT`). Die Konstante steht hier und wird
# nicht von dort geholt: dieser Adapter kennt keine Abrechnung, er liest ein
# Protokoll. Ein Import in diese Richtung waere der Anfang eines Zyklus.
MIKRO_JE_USD = 1_000_000


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
    """Was eine Runde beim Anbieter verbraucht hat — soweit er es meldet.

    Frueher stand hier nur ``total_tokens``, und das war der Grund, warum die
    Kostenanzeige nicht stimmen konnte: Eingabe und Ausgabe kosten bei jedem
    Modell unterschiedlich viel, und eine Eingabe aus dem Zwischenspeicher
    kostet rund ein Zehntel der frischen. Eine einzige Summe laesst sich nicht
    im Nachhinein wieder in ihre Teile zerlegen — mit *einem* Preis auf *alle*
    Tokens war jede Rechnung daneben, in beide Richtungen.

    ``cost_micro_usd`` ist der Ausweg aus dem Rechnen: OpenRouter meldet in der
    letzten Zeile jedes Streams den Betrag, der dem Konto tatsaechlich belastet
    wurde. Den zu buchen ist genauer als jede Nachrechnung, weil er dieselbe
    Zahl ist, die im Dashboard des Anbieters steht. Die Einheit ist die der
    Abrechnung (`ai_usage_service.MICROUNITS_PER_CENT`), festgelegt auf
    **US-Cent**: der Anbieter rechnet in USD ab, und eine Umrechnung *vor* der
    Buchung waere eine zweite Fehlerquelle in genau der Zahl, die hier stimmen
    soll. Umgerechnet wird erst in der Anzeige.

    ``vom_anbieter`` trennt Gemessenes von Geschaetztem. Ohne diese Marke sieht
    eine Zeile, deren Zahlen aus `estimate_reserved_tokens` stammen, genauso aus
    wie eine gemessene — und wer seine Rechnung nachpruefen will, kann nicht
    erkennen, welche der beiden er vor sich hat.
    """

    total_tokens: int | None = None
    output_chars: int = 0
    tool_calls: list["ProviderToolCall"] = field(default_factory=list)
    # Gesammelte Denkschritte des Modells. Getrennt von der Antwort, weil sie
    # etwas anderes sind: eine Nebenausgabe, die der Benutzer aufklappen kann,
    # aber die nie als Aussage des Panels gelesen werden darf.
    reasoning_chars: int = 0
    # Die Aufschluesselung. ``None`` heisst "nicht gemeldet" und nie "null":
    # ein Anbieter, der schweigt, hat nicht null Tokens verbraucht.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Teilmengen der beiden obigen, keine zusaetzlichen Tokens. ``cached_tokens``
    # sind bereits gelesene Eingabe, ``reasoning_tokens`` bereits erzeugte
    # Ausgabe — wer sie addiert, zaehlt doppelt.
    cached_tokens: int = 0
    # Was in den Zwischenspeicher **geschrieben** wurde. Warum es beide Zahlen
    # braucht, steht bei der Spalte in `models/ai_usage_event.py`.
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_micro_usd: int | None = None
    vom_anbieter: bool = False
    # Wieviele Anbieteranfragen stecken in dieser Zeile. Eine Chatnachricht ist
    # nicht eine Anfrage: jede Werkzeugrunde ruft den Anbieter erneut und
    # schickt den gewachsenen Verlauf komplett mit. Zwoelf Runden mit 30.000
    # Tokens Prompt sind 360.000 abgerechnete Tokens fuer *eine* Frage. Der
    # Anbieter rechnet genauso ab; ohne diese Zahl sieht die Summe im Panel
    # nur nach einem Fehler aus.
    anfragen: int = 0


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


def _ganzzahl(wert: Any) -> int | None:
    """Nimmt eine Zahl aus Fremddaten nur an, wenn sie eine nichtnegative ganze ist.

    ``bool`` ist in Python eine ``int`` und waere sonst eine 1 oder 0 an einer
    Stelle, an der eine Tokenzahl stehen soll.
    """
    if isinstance(wert, bool) or not isinstance(wert, int):
        return None
    return max(0, wert)


def _teilmenge(rohdaten: Any, feld: str) -> int:
    """Liest eine Zahl aus einem verschachtelten ``*_details``-Objekt.

    Fehlt das Objekt oder das Feld, ist die Antwort 0 und nicht ``None``: anders
    als bei ``prompt_tokens`` ist "nicht gemeldet" hier tatsaechlich dasselbe wie
    "keine". Ein Modell ohne Zwischenspeicher meldet keine `cached_tokens`, und
    es hat auch keine.
    """
    if not isinstance(rohdaten, dict):
        return 0
    return _ganzzahl(rohdaten.get(feld)) or 0


def usage_uebernehmen(usage: StreamUsage, rohdaten: Any) -> None:
    """Liest das ``usage``-Objekt einer Anbieterantwort in ``usage``.

    Gelesen wird alles, was OpenRouter meldet — Tokenzahlen getrennt nach
    Eingabe und Ausgabe, die Teilmengen aus Zwischenspeicher und Denkschritten,
    und vor allem ``cost``: der Betrag, der dem Konto tatsaechlich belastet
    wurde. Frueher wurde hier nur ``total_tokens`` behalten und der Rest
    weggeworfen; die Kosten rechnete das Panel danach selbst nach, mit einem
    einzigen Preis auf alle Tokens. Das konnte nicht stimmen.

    Es braucht dafuer **kein Feld im Request**. Die Schalter ``usage:{include}``
    und ``stream_options:{include_usage}`` sind bei OpenRouter abgekuendigt und
    wirkungslos; die Zahlen kommen von selbst in der letzten Zeile des Streams.

    ``vom_anbieter`` wird nur gesetzt, wenn wenigstens eine Tokenzahl ankam. Ein
    ``usage``-Objekt, das nur aus leeren Feldern besteht, ist keine Messung.
    """
    if not isinstance(rohdaten, dict):
        return
    gesamt = _ganzzahl(rohdaten.get("total_tokens"))
    eingabe = _ganzzahl(rohdaten.get("prompt_tokens"))
    ausgabe = _ganzzahl(rohdaten.get("completion_tokens"))
    if gesamt is None and eingabe is None and ausgabe is None:
        return
    # Meldet der Anbieter die Teile, aber keine Summe, wird sie hier gebildet:
    # die Kontingente haengen an einer Gesamtzahl, und eine fehlende Summe darf
    # nicht dazu fuehren, dass eine gemessene Anfrage als geschaetzt gilt.
    if gesamt is None:
        gesamt = (eingabe or 0) + (ausgabe or 0)
    usage.total_tokens = gesamt
    usage.prompt_tokens = eingabe
    usage.completion_tokens = ausgabe
    usage.cached_tokens = _teilmenge(rohdaten.get("prompt_tokens_details"), "cached_tokens")
    # Beide stehen im selben Objekt und heißen bei OpenRouter genau so
    # (``prompt_tokens_details.cached_tokens`` / ``…cache_write_tokens``).
    usage.cache_write_tokens = _teilmenge(
        rohdaten.get("prompt_tokens_details"), "cache_write_tokens"
    )
    usage.reasoning_tokens = _teilmenge(
        rohdaten.get("completion_tokens_details"), "reasoning_tokens"
    )
    kosten = rohdaten.get("cost")
    if isinstance(kosten, (int, float)) and not isinstance(kosten, bool) and kosten >= 0:
        usage.cost_micro_usd = round(float(kosten) * MIKRO_JE_USD)
    usage.vom_anbieter = True


def _summe(links: int | None, rechts: int | None) -> int | None:
    """Addiert zwei Werte, bei denen ``None`` "nicht gemeldet" heisst.

    ``None + 5`` ergibt hier 5 und nicht ``None``: eine stumme Runde macht die
    gemeldeten Zahlen der anderen nicht wertlos. Dass die Summe dann unvollstaendig
    ist, haelt ``vom_anbieter`` fest — nicht ein weggeworfener Wert.
    """
    if links is None:
        return rechts
    if rechts is None:
        return links
    return links + rechts


def usage_addieren(ziel: StreamUsage, teil: StreamUsage) -> None:
    """Zaehlt eine weitere Anbieterrunde zur Bilanz eines Laufs.

    Ein Lauf ist selten eine Anfrage. Jede Werkzeugrunde ruft den Anbieter
    erneut und schickt den inzwischen gewachsenen Verlauf komplett mit — der
    Anbieter berechnet den Prompt also jedes Mal neu, und genau so muss auch
    das Panel zaehlen. Summiert wird deshalb ueber die Runden und nicht das
    Maximum genommen.

    ``vom_anbieter`` gilt nur, wenn **jede** addierte Runde gemessen wurde. Eine
    einzige stumme Runde macht die ganze Summe zur Schaetzung: eine Zahl, die zur
    Haelfte gemessen und zur Haelfte geraten ist, waere sonst nicht von einer
    vollstaendig gemessenen zu unterscheiden — und wer damit seine Rechnung
    prueft, prueft sie gegen eine Vermutung.

    Nicht addiert werden ``output_chars``, ``reasoning_chars`` und
    ``tool_calls``: die gehoeren der laufenden Runde und werden vom Aufrufer
    verwaltet.
    """
    ziel.total_tokens = _summe(ziel.total_tokens, teil.total_tokens)
    ziel.prompt_tokens = _summe(ziel.prompt_tokens, teil.prompt_tokens)
    ziel.completion_tokens = _summe(ziel.completion_tokens, teil.completion_tokens)
    ziel.cached_tokens += teil.cached_tokens
    ziel.cache_write_tokens += teil.cache_write_tokens
    ziel.reasoning_tokens += teil.reasoning_tokens
    ziel.cost_micro_usd = _summe(ziel.cost_micro_usd, teil.cost_micro_usd)
    ziel.anfragen += teil.anfragen
    ziel.vom_anbieter = ziel.vom_anbieter and teil.vom_anbieter


def _denktext_aus_details(rohdaten: object) -> str:
    """Denkschritte aus ``reasoning_details`` — der zweite Weg, auf dem sie kommen.

    Der Klartextstrom (``delta.reasoning``) ist nicht der einzige. OpenRouter
    dokumentiert fuer den Streamingfall ``choices[].delta.reasoning_details``,
    eine Liste getippter Stuecke, und **welchen** der beiden Wege ein Modell
    nimmt, entscheidet seine Familie: die OpenAI-Modelle geben ihre Ueberlegungen
    ueber die Responses-API als ``reasoning.summary`` heraus und lassen das
    Klartextfeld leer. Wer nur den Textstrom liest, sieht bei ihnen nichts —
    nicht weil nicht gedacht wurde, sondern weil der Text woanders steht. Genau
    das war der Fall, in dem der aufklappbare Block fehlte, obwohl die Stufe an
    war und die Antwort auffaellig lange brauchte.

    Zwei Arten tragen Text: ``reasoning.text`` (die rohe Gedankenkette) und
    ``reasoning.summary`` (die Zusammenfassung, die manche Anbieter statt der
    Kette herausgeben). ``reasoning.encrypted`` traegt keinen — dort haelt der
    Anbieter die Ueberlegungen zurueck und schickt nur einen Blob, den niemand
    lesen kann. Er wird uebergangen statt als ``[REDACTED]`` angezeigt: eine
    Zeile, die nichts sagt, ist kein Denkschritt.

    Unbekannte Arten werden ebenso uebergangen. Die Formate sind
    anbieterspezifisch und kommen laufend dazu; was MSM nicht einordnen kann,
    zeigt es nicht an.
    """
    if not isinstance(rohdaten, list):
        return ""
    stuecke: list[str] = []
    for eintrag in rohdaten:
        if not isinstance(eintrag, dict):
            continue
        art = eintrag.get("type")
        if art == "reasoning.text":
            wert = eintrag.get("text")
        elif art == "reasoning.summary":
            wert = eintrag.get("summary")
        else:
            continue
        if isinstance(wert, str) and wert:
            stuecke.append(wert)
    return "".join(stuecke)


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
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    reasoning: bool = False,
    reasoning_effort: str | None = None,
    cache_marke: bool = False,
) -> AsyncIterator[StreamChunk]:
    """Normalisiert Provider-SSE zu Antwort- und Denkschritt-Stuecken.

    Providerframes, Response-Bodies und URLs verlassen diese Schicht nie. In
    Tool-Calls werden nur strukturell normalisiert. Ob ein Tool erlaubt ist
    und ob daraus lediglich ein Vorschlag entsteht, entscheidet die interne
    AI-Aktionsschicht; Providerdaten loesen hier niemals Aktionen aus.

    ``tool_choice`` bleibt ohne Angabe ``"auto"`` — so, wie es hier fest stand,
    seit es Werkzeuge gibt. Der Parameter existiert für die beiden Fälle, in
    denen „das Modell entscheidet“ die falsche Vorgabe ist: `ai_mail_text` will
    keine Prosa, sondern ein ausgefülltes Formular, und reicht deshalb ein
    einzelnes Werkzeug samt Zwang darauf herein; die Schlussrunde eines Laufs
    (`ai_stream_service`) will umgekehrt gar keinen Aufruf mehr und sendet
    ``"none"`` — den Katalog aber weiterhin mit, weil er bei Anthropic Teil des
    zwischengespeicherten Präfix ist. Diese Schicht prüft den Wert nicht — sie
    sendet ihn, wie sie ``tools`` sendet.

    ``reasoning`` steuert das Nachdenken. Der Schalter ist absichtlich
    generisch: gesendet wird ``{"reasoning": {"enabled": ...}}``, gelesen werden
    ``delta.reasoning``, ``delta.reasoning_content`` und — wenn beide leer
    bleiben — ``delta.reasoning_details``. Das erste Feld nutzt OpenRouter, das
    zweite die meisten OpenAI-kompatiblen Server (vLLM, DeepSeek, Ollama), das
    dritte ist der dokumentierte Streamingweg fuer die Modellfamilien, die ihre
    Ueberlegungen nur zusammengefasst herausgeben (siehe
    `_denktext_aus_details`).

    **Gesendet wird das Feld nur an Anbieter, deren Dialekt es kennt**
    (``Anbieter.reasoning_feld`` in `ai_provider_registry`). Hier stand „ein
    Anbieter, der es nicht kennt, ignoriert es" — das stimmt fuer die milden
    Server, aber nicht fuer OpenAI direkt: dort weist die strenge Validierung
    unbekannte Top-Level-Felder mit einem 400 ab, und jede Anfrage scheiterte,
    bevor das Modell sie je sah. Gelesen wird dagegen weiterhin alles, was
    ankommt — die Lesewege oben haengen nicht am gesendeten Feld.

    **Kein Denktext ist kein Fehler.** Ein Modell kann nachdenken, es abrechnen
    und trotzdem nichts davon herausgeben — die OpenAI-Modelle verschluesseln
    ihre Kette und liefern bestenfalls eine Zusammenfassung, und die faellt bei
    kurzen Ueberlegungen auch mal ganz aus. ``usage.reasoning_tokens`` zaehlt
    dann trotzdem. Die beiden Faelle sind verschieden und sehen von aussen
    gleich aus.

    **Wo das Feld mitgeht, geht es in beide Richtungen mit, auch bei
    ``False``.** Vorher wurde es nur bei ``True`` gesendet — bei „aus“ ging gar
    nichts hinaus, und das ist nicht dasselbe. Die Mehrheit der aktuellen
    Modelle denkt von sich aus: OpenRouter meldet fuer Claude Opus 5, Sonnet 5
    und Gemini 3.5 Flash ``default_enabled: true``. Ohne ausdrueckliches
    ``enabled: false`` dachte das Modell also weiter und wurde abgerechnet —
    der Schalter blendete nur die Denkschritte aus. Fuer ein Panel mit
    Kostenlimits je Rolle ist das die falsche Voreinstellung; ein
    Kostenschalter darf sich nicht auf Anbieterdefaults verlassen. Bei
    Anbietern ohne das Feld bleibt genau dieses Restrisiko bestehen — dort
    kann MSM das Denken schlicht nicht abschalten, und ein Feld zu senden, das
    die Anfrage toetet, schaltet es auch nicht ab.

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

    **Geprüft und bewusst nicht aufgeteilt:** die oberste Form gilt laut
    OpenRouter-Doku für Anthropic, Google Vertex AI, Azure und Bedrock. Gemini
    und Qwen verlangen die Marke im Inhaltsblock, GPT ab 5.6 ein eigenes
    ``prompt_cache_breakpoint``. Folgenlos ist das trotzdem fast überall:
    OpenAI und Gemini speichern bei OpenRouter von selbst zwischen, ein
    wirkungsloses Feld ändert dort nichts. Es bliebe Qwen — und dafür müsste
    diese Schicht wissen, welcher Nachrichtenblock der letzte stabile ist. Das
    weiß sie nicht, das weiß `ai_context_service`, und ein falsch gesetzter
    Breakpoint kostet mehr als ein fehlender.

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
        # ``model`` uebersteuert das Standardmodell des Zugangs. Es gibt genau
        # einen Aufrufer dafuer, und der begruendet den Parameter: das Gehoer
        # (`ai_stt_chat`) schickt Ton an ein **hoerfaehiges** Modell, waehrend
        # derselbe Zugang zum Denken ein anderes benutzt. Zwei Zugaenge auf
        # dieselbe Adresse anzulegen waere die Alternative gewesen — mit zwei
        # Schluesseln, zwei Kontingenten und zwei Stellen zum Vergessen.
        "model": model or provider.default_model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        request_body["tools"] = tools
        # ``"auto"`` bleibt die Vorgabe und damit das Verhalten jedes bisherigen
        # Aufrufers: im Chat entscheidet das Modell, ob es ein Werkzeug braucht.
        # Erzwungen wird nur dort, wo der Aufruf gar keine Antwort in Prosa
        # will, sondern ein ausgefuelltes Formular — siehe `ai_mail_text`.
        request_body["tool_choice"] = tool_choice or "auto"
    # Nur an Anbieter, deren Dialekt das Feld kennt (siehe Docstring) — und
    # dort immer, nie weglassen: "nichts senden" heisst beim Anbieter nicht
    # "aus", sondern "nimm deinen Default" — und der ist bei den meisten
    # aktuellen Modellen an.
    if ai_provider_registry.anbieter(provider.provider_kind).reasoning_feld:
        denken: dict[str, Any] = {"enabled": bool(reasoning)}
        # Die Stufe nur mitgeben, wenn auch gedacht werden soll. Ein `effort`
        # neben `enabled: false` sind zwei widerspruechliche Angaben in einer
        # Anfrage — welche gewinnt, entscheidet dann der Anbieter und nicht MSM.
        if reasoning and reasoning_effort:
            denken["effort"] = reasoning_effort
        request_body["reasoning"] = denken
    if cache_marke:
        request_body["cache_control"] = {"type": "ephemeral"}
    target = httpx.URL(provider_base_url(provider).rstrip("/") + "/chat/completions")
    deadline = time.monotonic() + MAX_STREAM_SECONDS
    frames = 0
    try:
        async with client.stream(
            "POST",
            target,
            headers=headers,
            json=request_body,
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

            # Ab hier ist die Anfrage beim Anbieter angekommen und wird von ihm
            # abgerechnet — auch wenn der Strom gleich abbricht. Gezaehlt wird
            # deshalb hier und nicht am Ende: eine Runde, die auf halber Strecke
            # stirbt, hat trotzdem stattgefunden.
            usage.anfragen += 1
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
                usage_uebernehmen(usage, frame.get("usage"))
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
                    # reiner Text — und beide bleiben bei einem Teil der Modelle
                    # leer, obwohl gedacht (und abgerechnet) wird. Dann steht der
                    # Text in `reasoning_details`; siehe `_denktext_aus_details`.
                    thought = delta.get("reasoning")
                    if not isinstance(thought, str) or not thought:
                        thought = delta.get("reasoning_content")
                    if not isinstance(thought, str) or not thought:
                        thought = _denktext_aus_details(delta.get("reasoning_details"))
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
