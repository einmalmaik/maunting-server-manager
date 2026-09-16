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

    „Secret-frei" heisst dabei **nicht** „fuer jeden Leser". Die Redaktion trifft
    Schluesselmuster; Kontingentstand, Kontoname und Fine-Tune-Bezeichnungen
    sehen aus wie gewoehnlicher Text und bleiben stehen — und ein maskierter
    Schluessel (``sk-pr***…xyZ4``) passt auf keines der Muster. Der Satz gehoert
    darum ins Protokoll des Betreibers, nicht in eine Meldung an den Benutzer;
    wer ihn weiterreicht, reicht den Zugang des Panels mit. `ai_stream_service`
    und `routers.ai_providers._stimmzugang_pruefen` halten sich beide daran.
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
    # Native Session-/Response-Kennungen fuer Turn-Chaining und Stream-Multiplexing.
    response_id: str | None = None
    stream_id: str | None = None



@dataclass(frozen=True)
class StreamChunk:
    """Ein Stueck Providerausgabe oder ein internes Werkzeug-Signal."""

    kind: str  # "content" | "reasoning" | "tool_start" | "tool_ready"
    text: str = ""
    # ``tool_ready`` ist ein reines Adapter-/Engine-Ereignis. Es darf nie
    # unverarbeitet in Broker, Verlauf oder Frontend gelangen.
    tool_call: "ProviderToolCall | None" = None


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: dict


def schluesselkopf(
    spec: ai_provider_registry.Anbieter, api_key: str | None
) -> dict[str, str]:
    """Die Kopfzeilen einer Chatanfrage, samt Schluessel im richtigen Kopf.

    Hier stand ein fest verdrahtetes ``Authorization: Bearer`` — in **beiden**
    Chat-Adaptern, obwohl `Anbieter.schluessel_kopf` und
    `Anbieter.schluessel_praefix` seit ElevenLabs genau dafuer da sind und
    `ai_stt_endpunkt` sie laengst benutzt. Zwei Wahrheiten ueber dieselbe Frage,
    und die falsche haette bei Azure zugeschlagen: dort traegt der
    Ressourcenschluessel den Kopf ``api-key``, waehrend ``Authorization:
    Bearer`` bei Claude auf Azure fuer ein Entra-ID-Token reserviert ist. Ein
    Schluessel im falschen Kopf endet in einem 401, das wie ein falscher
    Schluessel aussieht und keiner ist.

    Eine gemeinsame Funktion statt derselben zwei Zeilen in drei Adaptern: der
    naechste Dialekt soll den Kopf nicht erneut erfinden muessen.

    ``Accept: text/event-stream`` steht mit drin, weil alle drei Wege streamen.
    Ohne Schluessel bleibt der Kopf weg — ob das erlaubt ist, hat der Aufrufer
    ueber ``provider.requires_api_key`` bereits entschieden.
    """
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if api_key:
        headers[spec.schluessel_kopf] = f"{spec.schluessel_praefix}{api_key}"
    return headers


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
    if teil.response_id:
        ziel.response_id = teil.response_id
    if teil.stream_id:
        ziel.stream_id = teil.stream_id
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
    if status_code == 402:
        # Eigener Code, weil die Handlung eine voellig andere ist: hier ist nichts
        # falsch konfiguriert, es ist bezahlt worden. Unter
        # `AI_PROVIDER_REQUEST_REJECTED` las der Betreiber „Meist stimmt der
        # Modellname nicht" und suchte tagelang am falschen Ende.
        return "AI_PROVIDER_PAYMENT_REQUIRED"
    if status_code == 429:
        return "AI_PROVIDER_RATE_LIMITED"
    if status_code >= 500:
        return "AI_PROVIDER_UNAVAILABLE"
    return "AI_PROVIDER_REQUEST_REJECTED"


def _kurzfassung(message: str) -> str | None:
    """Fremdtext zu einer Zeile, die man einem Betreiber zeigen kann.

    Redigiert, einzeilig, hart gekuerzt. Steht als eigene Funktion, weil derselbe
    Text auf zwei Wegen hereinkommt: aus dem Body einer Fehlerantwort
    (`_error_detail`) und aus einem Fehlerrahmen mitten im Strom
    (`_fehler_im_rahmen`). Zweimal dieselbe Behandlung, einmal geschrieben.
    """
    single_line = " ".join(redact_sensitive_text(message).split())
    return single_line[:MAX_PROVIDER_DETAIL_CHARS] or None


def _fehler_im_rahmen(frame: dict) -> tuple[str, str | None] | None:
    """Der Fehler, den ein Anbieter **mitten im Strom** meldet — oder ``None``.

    Hier lag der teuerste blinde Fleck dieser Schicht. OpenRouter dokumentiert es
    ausdruecklich: sind die Kopfzeilen erst einmal draussen, steht der Status auf
    ``200`` und laesst sich nicht mehr aendern. Ein Fehler kommt dann als ganz
    gewoehnliches ``data:``-Ereignis mit einem ``error``-Feld **auf oberster
    Ebene**, daneben ein ``choices``-Eintrag mit ``finish_reason: "error"``, und
    danach endet die Verbindung — ohne ``[DONE]``.

    Was diese Schicht daraus machte, war die Meldung „Die Antwort des Anbieters
    brach vorzeitig ab": ``choices`` war vorhanden, ``delta.content`` leer, also
    lief die Schleife durch, ``saw_done`` blieb ``False``, und der einzige Satz,
    der die Ursache genannt haette — „Insufficient credits", „No endpoints found
    for X", „Provider disconnected unexpectedly" — wurde nie gelesen. Der
    Betreiber sah einen Abbruch ohne Grund und musste raten, welcher seiner
    Zugaenge, welches Modell oder welches Guthaben gemeint war.

    ``code`` kommt in beiden Formen vor, und beide sind gemeint: OpenRouter
    schickt bei einem durchgereichten HTTP-Fehler die **Zahl** (``402``), bei
    einem eigenen Zustand ein **Wort** (``"server_error"``). Eine Zahl geht
    deshalb durch dieselbe Uebersetzung wie ein Status — ein 402 mitten im Strom
    ist derselbe Sachverhalt wie ein 402 in der Kopfzeile und verdient dieselbe
    Handlungsanweisung. Ein Wort taugt dafuer nicht und endet als
    ``AI_PROVIDER_REQUEST_REJECTED``; die Einzelheit traegt dann der Text.
    """
    fehler = frame.get("error")
    if isinstance(fehler, str):
        return "AI_PROVIDER_REQUEST_REJECTED", _kurzfassung(fehler)
    if not isinstance(fehler, dict):
        return None
    code = fehler.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        marke = "AI_PROVIDER_REQUEST_REJECTED"
    else:
        marke = _error_code(code)
    nachricht = fehler.get("message")
    if not isinstance(nachricht, str) or not nachricht:
        # Ohne Text bleibt wenigstens die Marke. Sie ist immer noch mehr als
        # „brach vorzeitig ab", weil sie sagt, auf welcher Seite gesucht wird.
        nachricht = str(code) if code is not None else ""
    return marke, _kurzfassung(nachricht)


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
    return _kurzfassung(message)


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
    previous_response_id: str | None = None,
    use_websocket: bool = True,
    compaction: bool = False,
    background: bool = False,
    **kwargs: Any,
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
    (Marke ``"reasoning"`` in ``Anbieter.anfrage_erweiterungen``). Hier stand „ein
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
    Anbietern ohne eine Denk-Marke in ``anfrage_erweiterungen`` bleibt genau
    dieses Restrisiko bestehen — dort kann MSM das Denken schlicht nicht
    abschalten, und ein Feld zu senden, das die Anfrage toetet, schaltet es
    auch nicht ab.

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
    SNI-Hostnamen. Das war noetig, solange die **ganze** Zieladresse aus einem
    Formular stammte. Sie kommt jetzt aus `ai_provider_registry`, also aus dem
    Programm.

    Seit Azure gilt der Satz „es gibt gar keine Eingabe mehr" nicht mehr
    uneingeschraenkt, und er steht deshalb hier nicht mehr: ein Anbieter mit
    ``ressource_noetig`` traegt in seiner Adresse eine Luecke, die der
    Betreiber fuellt. Was er beitraegt, ist ein einzelnes DNS-Label — Schema,
    Suffix und Pfad bleiben im Programm, und `ai_provider_service.base_url`
    prueft das Label mit ``re.fullmatch``, bevor es hier ankommt. Der
    verbleibende Fall — Azure Private Link loest einen gueltigen Namen im VNet
    auf eine private Adresse auf — ist an
    `ai_provider_registry.basis.Anbieter.ressource_noetig` benannt.

    """
    if provider.requires_api_key and not api_key:
        raise AiProviderRequestError("AI_PROVIDER_KEY_MISSING")

    # **Die Weiche zwischen den Chatwegen.** Sie steht hier und nicht bei den
    # fuenf Aufrufern: die wollen einen Chatzug, nicht einen Dialekt. Welchen
    # ein Zugang spricht, sagt seine Anbieterdatei (`Anbieter.protokoll_chat`)
    # — OpenAI direkt spricht `responses`, weil sein `/chat/completions`
    # Werkzeuge und Denkstufe nicht zusammen nimmt; Claude auf Azure spricht
    # `anthropic_messages`, weil es gar kein `/chat/completions` hat.
    #
    # Gefragt wird **die Anbieterdatei und nie das Modell**. Bei Azure kann
    # dieselbe Ressource GPT- und Claude-Deployments fuehren, und wie ein
    # Deployment heisst, entscheidet der Betreiber: aus `model` laesst sich der
    # Dialekt grundsaetzlich nicht ablesen. Ein `"claude" in model` waere
    # geraten — und stuende ausgerechnet in der Datei, die keinen einzelnen
    # Anbieter kennen darf.
    #
    # Spaeter Import, kein Zyklus: beide Schwestermodule holen sich von hier
    # die Grenzwerte, die Fehlerklasse und die Stueck-Datentypen. Ein Import am
    # Dateikopf zeigte damit im Kreis.
    from services.openai_responses_adapter import spricht_responses, stream_responses

    if spricht_responses(provider):
        async for stueck in stream_responses(
            client,
            provider=provider,
            api_key=api_key,
            messages=messages,
            usage=usage,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
            cache_marke=cache_marke,
            previous_response_id=previous_response_id,
            use_websocket=use_websocket,
            compaction=compaction,
            background=background,
            **kwargs,
        ):
            yield stueck
        return

    from services.anthropic_messages_adapter import spricht_messages, stream_messages

    if spricht_messages(provider):
        async for stueck in stream_messages(
            client,
            provider=provider,
            api_key=api_key,
            messages=messages,
            usage=usage,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
            cache_marke=cache_marke,
            **kwargs,
        ):
            yield stueck
        return

    spec = ai_provider_registry.anbieter(provider.provider_kind)
    headers = schluesselkopf(spec, api_key)
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
    # Welche Zusatzfelder dieser Anbieter vertraegt, sagt sein Eintrag — nicht
    # diese Datei. Der Adapter bedient alle; sobald er einen einzelnen beim Namen
    # kennt, sammelt er mit jedem weiteren eine Verzweigung an.
    erweiterungen = spec.anfrage_erweiterungen
    if "reasoning" in erweiterungen:
        # Immer setzen, nie weglassen: "nichts senden" heisst bei einem Anbieter,
        # der das Feld kennt, nicht "aus", sondern "nimm deinen Default" — und
        # der ist bei den meisten aktuellen Modellen an.
        denken: dict[str, Any] = {"enabled": bool(reasoning)}
        # Die Stufe nur mitgeben, wenn auch gedacht werden soll. Ein `effort`
        # neben `enabled: false` sind zwei widerspruechliche Angaben in einer
        # Anfrage — welche gewinnt, entschiede dann der Anbieter und nicht MSM.
        if reasoning and reasoning_effort:
            denken["effort"] = reasoning_effort
        request_body["reasoning"] = denken
    if "reasoning_effort" in erweiterungen:
        if reasoning_effort:
            request_body["reasoning_effort"] = reasoning_effort
    # Wer das Feld nicht kennt, bekommt **nichts** — keine Ersatzform, keine
    # Uebersetzung in einen anderen Dialekt. Ein stiller Rueckfall waere hier
    # besonders teuer: er saehe aus wie ein erfuellter Wunsch, waehrend der
    # Anbieter nach seinem eigenen Default denkt und abrechnet.
    if cache_marke and "cache_control" in erweiterungen:
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
                    # Das Modell gehoert dazu: dieselbe Anlage bedient mehrere,
                    # und „abgelehnt" ohne den Namen zwingt zum Raten, welches.
                    "AI provider request failed provider_id=%s model=%s status=%s",
                    provider.id,
                    model or provider.default_model,
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
            seen_tool_starts: set[int] = set()
            emitted_tool_calls: set[int] = set()

            def fertiger_aufruf(index: int) -> ProviderToolCall:
                item = tool_buffers[index]
                if not item["id"] or not item["name"]:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                try:
                    arguments = json.loads(item["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
                if not isinstance(arguments, dict):
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                return ProviderToolCall(
                    id=item["id"], name=item["name"], arguments=arguments
                )
            async for line in _iter_sse_lines(response, deadline=deadline):
                if time.monotonic() > deadline:
                    raise AiProviderRequestError("AI_PROVIDER_STREAM_TIMEOUT")
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                # Gezaehlt wird erst hier, und das ist eine Korrektur: vorher
                # zaehlte jede gelesene Zeile mit. In SSE folgt auf jedes
                # Ereignis eine Leerzeile, OpenRouter schiebt ausserdem
                # `: OPENROUTER PROCESSING` als Lebenszeichen dazwischen — die
                # Grenze war damit in Wahrheit weniger als halb so hoch wie die
                # Zahl behauptet, und eine lange Antwort konnte an
                # `AI_PROVIDER_RESPONSE_TOO_LARGE` sterben, obwohl sie keine
                # war. Gegen eine Flut aus reinen Leerzeilen schuetzt die Frist
                # oben, nicht diese Zahl.
                frames += 1
                if frames > MAX_STREAM_FRAMES:
                    raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                payload = line[5:].strip()
                if payload == "[DONE]":
                    saw_done = True
                    break
                try:
                    frame = json.loads(payload)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR") from exc
                usage_uebernehmen(usage, frame.get("usage"))
                # Vor `choices`, denn ein Fehlerrahmen bringt beides mit: das
                # `error`-Feld und ein leeres Delta mit `finish_reason: "error"`.
                # Wer zuerst auf `choices` schaut, sieht nur das leere Delta,
                # springt weiter und verliert den Grund. Siehe `_fehler_im_rahmen`.
                if (gemeldet := _fehler_im_rahmen(frame)) is not None:
                    marke, text = gemeldet
                    logger.warning(
                        "AI provider stream error provider_id=%s model=%s code=%s",
                        provider.id,
                        model or provider.default_model,
                        marke,
                    )
                    raise AiProviderRequestError(marke, text)
                choices = frame.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                tool_deltas = delta.get("tool_calls") if isinstance(delta, dict) else None
                if isinstance(tool_deltas, list):
                    for item in tool_deltas:
                        if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                            raise AiProviderRequestError("AI_PROVIDER_PROTOCOL_ERROR")
                        idx = item["index"]
                        buffer = tool_buffers.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
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
                        if idx not in seen_tool_starts and buffer["name"]:
                            seen_tool_starts.add(idx)
                            yield StreamChunk("tool_start", buffer["name"])
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
                if isinstance(content, str) and content:
                    usage.output_chars += len(content)
                    if usage.output_chars > MAX_ASSISTANT_CHARS:
                        raise AiProviderRequestError("AI_PROVIDER_RESPONSE_TOO_LARGE")
                    yield StreamChunk("content", content)

                finish_reason = (
                    choices[0].get("finish_reason")
                    if isinstance(choices[0], dict)
                    else None
                )
                if finish_reason == "tool_calls":
                    for index in sorted(tool_buffers):
                        if index in emitted_tool_calls:
                            continue
                        call = fertiger_aufruf(index)
                        emitted_tool_calls.add(index)
                        usage.tool_calls.append(call)
                        yield StreamChunk("tool_ready", tool_call=call)
            if not saw_done:
                raise AiProviderRequestError("AI_PROVIDER_STREAM_INCOMPLETE")
            for index in sorted(tool_buffers):
                if index in emitted_tool_calls:
                    continue
                call = fertiger_aufruf(index)
                emitted_tool_calls.add(index)
                usage.tool_calls.append(call)
                yield StreamChunk("tool_ready", tool_call=call)
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
