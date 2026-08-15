"""Die Sprachsitzung: das Panel sitzt zwischen dem Browser und OpenAI.

**Warum das Panel in der Mitte steht und nicht danebensteht.** OpenAI bietet für
Realtime auch WebRTC an, und der Browser könnte direkt mit ``api.openai.com``
sprechen. Dann liefe aber die Werkzeugschleife über den Browser: er sähe jeden
``function_call``, und er könnte welche erfinden. CLAUDE.md § 4 lässt das nicht
zu — *„Keine Ausführung von Remote-Befehlen ohne vorherige Autorisierungsprüfung
im Backend"* und *„Das Frontend darf niemals blind entscheiden."*

Also leitet MSM weiter. Das ist nicht nur sicherer, es ist **weniger** Code:

* Der Betreiberschlüssel verlässt den Prozess nie. Die ganze Ausstellung
  kurzlebiger Client-Geheimnisse (``POST /v1/realtime/client_secrets``) entfällt
  ersatzlos — und mit ihr ein Ärgernis, das in OpenAIs Doku wörtlich steht: so
  ein Geheimnis ist *mehrfach* verwendbar, und eine begonnene Sitzung überlebt
  seinen Ablauf.
* Keine SDP-Aushandlung, kein ICE, keine TURN-Frage hinter dem Reverse-Proxy.
* Der Werkzeugkatalog geht serverseitig in ``session.update`` — unmanipulierbar.

Der Preis ist ein Netzsprung mehr: rund 250 bis 350 Millisekunden statt der
etwa 100, die WebRTC schafft. Gegen die im Chat gemessenen acht Sekunden bis zum
ersten Zeichen ist das nichts.

**Die Leitung zum Browser trägt zweierlei.** Binärrahmen sind Ton (PCM16, 24 kHz,
mono, Little Endian) und nichts sonst. Textrahmen sind JSON und beschreiben, was
gerade passiert — Transkripte, Zustandswechsel, Fehler. Die Trennung erspart das
Base64-Aufblähen auf dem Stück Weg, das MSM gehört; Richtung OpenAI muss der Ton
ohnehin als Base64 in ein JSON-Ereignis.

**Was hier bewusst nicht passiert.** Kein `AiRun`. Der Lauf ist dafür gebaut,
einen Browser zu überleben und nach einer Bestätigung wieder aufzuwachen; eine
Sprachsitzung ist das Gegenteil — es sitzt jemand davor, und wenn er geht, ist
sie vorbei. Die Nachrichten landen trotzdem in derselben Unterhaltung, damit im
Panel steht, was gesagt wurde.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

try:
    import websockets
except ImportError:  # pragma: no cover - im Betrieb immer vorhanden
    # Weich importiert, und das ist keine Vorsicht, sondern eine Regel des
    # Hauses: ein harter Import im Modulkopf entscheidet über den Start des
    # **ganzen** Panels. Am 11.08.2026 stand es in einer Neustartschleife, weil
    # ein QR-Zeichner fehlte. Der Sprachmodus ist eine Zusatzfunktion, die einen
    # eigens eingerichteten Anbieterzugang braucht — ohne die Bibliothek fällt
    # er aus, und sonst nichts.
    websockets = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: Ob überhaupt gesprochen werden kann. Der Router fragt das, bevor er den
#: Sprachmodus anbietet — dieselbe Antwort wie bei einem fehlenden Zugang.
SPRACHE_MOEGLICH = websockets is not None


# ── Grenzen ───────────────────────────────────────────────────────────────

#: Wie lange eine Sprachsitzung höchstens läuft.
#:
#: Die Zahl ist keine Vorsicht, sondern die Lebensdauer des Access-Tokens. Ein
#: WebSocket prüft die Anmeldung **nur beim Handshake** (`dependencies.py`), und
#: eine Verbindung, die Stunden offen bleibt, umginge damit beides: den Ablauf
#: des Tokens und die jti-Sperrliste. Wer abgemeldet wird, spräche weiter.
#:
#: Der Client verbindet nach dem Schließen von selbst neu und authentifiziert
#: sich dabei erneut. Das ist ehrlicher als eine Nachprüfmechanik, die es
#: sonst nirgends im Panel gibt.
MAX_SITZUNGSSEKUNDEN = 15 * 60

#: Wie groß ein einzelner Tonrahmen vom Browser höchstens sein darf. Bei 24 kHz
#: mono sind 128 KiB rund 2,7 Sekunden Ton — großzügig für Rahmen, die alle 20
#: bis 100 Millisekunden kommen sollen, und eng genug, dass niemand über diesen
#: Weg Speicher belegt.
MAX_TONRAHMEN_BYTES = 128 * 1024

#: Wie lang ein Textrahmen vom Browser höchstens sein darf. Der Browser schickt
#: hier nur winzige Steuerbefehle; alles Größere ist keine Steuerung mehr.
MAX_STEUERRAHMEN_ZEICHEN = 4_096

#: Frist für den Verbindungsaufbau zu OpenAI.
VERBINDUNGS_TIMEOUT = 20.0

#: Ton- und Abtastrate. Beides ist Vereinbarung mit dem Browser **und** mit
#: OpenAI: dessen Realtime-API spricht PCM16 bei 24 kHz mono.
ABTASTRATE = 24_000


# ── Was der Browser zu sehen bekommt ──────────────────────────────────────
#
# Bewusst eine kleine, geschlossene Liste. Die Ereignisse von OpenAI sind
# zahlreich und ändern sich mit jeder Version; sie ungefiltert
# durchzureichen hieße, das Frontend an ein fremdes Protokoll zu binden.

ZUSTAND_BEREIT = "bereit"
ZUSTAND_HOERT = "hoert"
ZUSTAND_DENKT = "denkt"
ZUSTAND_SPRICHT = "spricht"


@dataclass
class Lage:
    """Was während einer Sitzung veränderlich ist.

    Ein Objekt statt loser Variablen, damit die beiden Pumpen dieselbe Wahrheit
    lesen — und damit beim Lesen sofort auffällt, was überhaupt veränderlich ist.
    """

    #: Läuft die Sitzung noch? Wird von jeder Abbruchursache gesetzt.
    offen: bool = True
    #: Wieviele Tonrahmen in beide Richtungen gingen. Nur für das Protokoll am
    #: Ende — eine Sitzung ohne einen einzigen Rahmen ist ein Befund.
    rahmen_hin: int = 0
    rahmen_zurueck: int = 0
    #: Ob die Sitzung endete, weil das Kontingent aufgebraucht war. Für das
    #: Protokoll — und damit der Anrufer nicht raten muss, warum Schluss war.
    kontingent_aus: bool = False


# ── Der Protokollteil ─────────────────────────────────────────────────────
#
# ACHTUNG, und das ist keine Floskel: alles unterhalb dieser Linie ist gegen
# OpenAIs Realtime-API geschrieben, aber **nicht gegen sie geprueft** — dafuer
# braucht es einen Betreiberschluessel, und den hat die Testsuite nicht. Die
# Tests darunter pruefen die Form der Ereignisse, nicht ihre Annahme durch den
# Anbieter.
#
# Zwei Dinge sind deshalb absichtlich nachsichtig gelesen:
#
# * Die Namen der Tonereignisse haben sich mit dem Wechsel von der Beta zur
#   allgemeinen Verfuegbarkeit geaendert (`response.audio.delta` ->
#   `response.output_audio.delta`). Ein falscher Name faellt nicht als Fehler
#   auf, sondern als **Stille** — der schlechtesten Art von Fehler. Deshalb
#   werden beide Schreibweisen angenommen.
# * Unbekannte Ereignisse werden uebergangen und nicht als Stoerung behandelt.
#   Ein Anbieter, der ein Feld hinzufuegt, darf keine Sitzung abreissen.


#: Ereignisse, die Tonrahmen tragen. Neu und alt, siehe oben.
_TON_EREIGNISSE = frozenset({
    "response.output_audio.delta",
    "response.audio.delta",
})

#: Ereignisse, die das gesprochene Wort der KI als Text tragen.
_ANTWORTTEXT_EREIGNISSE = frozenset({
    "response.output_audio_transcript.delta",
    "response.audio_transcript.delta",
})

#: Das fertige Transkript dessen, was der Mensch gesagt hat.
_EINGABETEXT_FERTIG = "conversation.item.input_audio_transcription.completed"


def sitzungskonfiguration(
    *,
    modell: str,
    anweisungen: str,
    stimme: str,
    werkzeuge: list[dict] | None = None,
) -> dict:
    """Das ``session.update``, mit dem die Sitzung eingerichtet wird.

    Eine eigene Funktion, weil das der einzige Ort ist, an dem MSM dem Modell
    sagt, wer es ist und was es darf — und weil sich das ohne Netzverbindung
    prüfen lässt.

    ``turn_detection`` steht auf ``semantic_vad``: die Gegenstelle entscheidet
    am Inhalt, ob ein Satz zu Ende ist, statt an einer Pause. Das ist der
    Unterschied zwischen „ich denke kurz nach" und „ich bin fertig", und er ist
    der eigentliche Grund, warum sich ein Gespräch wie eines anfühlt.

    Der Werkzeugkatalog geht **einmal** hier hinein statt in jede Runde. Im Chat
    macht er gemessene 94 Prozent des Prompts aus; hier kostet er einmal.
    """
    sitzung: dict[str, Any] = {
        "type": "realtime",
        "model": modell,
        "instructions": anweisungen,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": ABTASTRATE},
                # Ohne Transkription der Eingabe wüsste MSM nicht, was der
                # Mensch gesagt hat — und könnte es weder anzeigen noch ins
                # Audit schreiben. Für die gesprochene Bestätigung ist das
                # keine Bequemlichkeit, sondern die Beweisgrundlage.
                "transcription": {"model": "gpt-4o-transcribe"},
                "turn_detection": {"type": "semantic_vad"},
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": ABTASTRATE},
                "voice": stimme,
            },
        },
    }
    if werkzeuge:
        sitzung["tools"] = werkzeuge
        sitzung["tool_choice"] = "auto"
    return {"type": "session.update", "session": sitzung}


def verlaufseintrag(rolle: str, text: str) -> dict:
    """Eine vorhandene Nachricht als Element der Sitzungshistorie.

    Damit beginnt die Sprachsitzung nicht bei null, sondern da, wo der getippte
    Chat aufgehört hat. Es ist dieselbe Unterhaltung — nur ein anderer Eingang.
    """
    art = "input_text" if rolle != "assistant" else "output_text"
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": rolle,
            "content": [{"type": art, "text": text}],
        },
    }


# ── Die Pumpen ────────────────────────────────────────────────────────────


async def _browser_nach_openai(
    browser: WebSocket, oben: Any, lage: Lage
) -> None:
    """Ton und Steuerbefehle vom Browser zur Gegenstelle.

    Binär ist Ton, Text ist Steuerung. Alles andere wird verworfen — und zwar
    still: ein Browser, der Unsinn schickt, soll die Sitzung eines Menschen
    nicht abreissen, der gerade spricht.
    """
    while lage.offen:
        try:
            rahmen = await browser.receive()
        except (WebSocketDisconnect, RuntimeError):
            return

        if rahmen.get("type") == "websocket.disconnect":
            return

        ton = rahmen.get("bytes")
        if ton is not None:
            if not ton or len(ton) > MAX_TONRAHMEN_BYTES:
                continue
            lage.rahmen_hin += 1
            await oben.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(ton).decode("ascii"),
            }))
            continue

        text = rahmen.get("text")
        if not text or len(text) > MAX_STEUERRAHMEN_ZEICHEN:
            continue
        await _steuerbefehl(text, oben)


async def _steuerbefehl(rohtext: str, oben: Any) -> None:
    """Die wenigen Dinge, die der Browser sagen darf.

    Eine geschlossene Liste und keine Weiterleitung. Der Browser darf den
    Redefluss steuern — er darf der Gegenstelle nicht diktieren, welche
    Werkzeuge es gibt oder was in den Anweisungen steht.
    """
    try:
        befehl = json.loads(rohtext)
    except ValueError:
        return
    if not isinstance(befehl, dict):
        return

    art = befehl.get("art")
    if art == "unterbrechen":
        # Der Mensch redet dazwischen. Die laufende Antwort wird abgebrochen —
        # sonst spricht die KI gegen ihn an.
        await oben.send(json.dumps({"type": "response.cancel"}))


async def _openai_nach_browser(
    browser: WebSocket,
    oben: Any,
    lage: Lage,
    werkzeuge: Any | None = None,
    kontingent: Any | None = None,
) -> None:
    """Ereignisse der Gegenstelle in Ton und Anzeige übersetzen."""
    async for rohnachricht in oben:
        if not lage.offen:
            return
        try:
            ereignis = json.loads(rohnachricht)
        except ValueError:
            continue
        if not isinstance(ereignis, dict):
            continue
        await _ereignis_verarbeiten(ereignis, browser, oben, lage, werkzeuge, kontingent)


async def _ereignis_verarbeiten(
    ereignis: dict,
    browser: WebSocket,
    oben: Any,
    lage: Lage,
    werkzeuge: Any | None,
    kontingent: Any | None = None,
) -> None:
    art = str(ereignis.get("type") or "")

    if art in _TON_EREIGNISSE:
        roh = ereignis.get("delta")
        if isinstance(roh, str) and roh:
            with contextlib.suppress(ValueError):
                lage.rahmen_zurueck += 1
                await browser.send_bytes(base64.b64decode(roh))
        return

    if art in _ANTWORTTEXT_EREIGNISSE:
        stueck = ereignis.get("delta")
        if isinstance(stueck, str) and stueck:
            await _sag(browser, {"art": "antworttext", "text": stueck})
        return

    if art == _EINGABETEXT_FERTIG:
        gesagt = ereignis.get("transcript")
        if isinstance(gesagt, str) and gesagt.strip():
            await _sag(browser, {"art": "gehoert", "text": gesagt.strip()})
        return

    if art == "input_audio_buffer.speech_started":
        await _sag(browser, {"art": "zustand", "zustand": ZUSTAND_HOERT})
        return

    if art == "input_audio_buffer.speech_stopped":
        await _sag(browser, {"art": "zustand", "zustand": ZUSTAND_DENKT})
        return

    if art == "response.created":
        await _sag(browser, {"art": "zustand", "zustand": ZUSTAND_SPRICHT})
        return

    if art == "response.done":
        antwort = ereignis.get("response")
        weiter = True
        if kontingent is not None and isinstance(antwort, dict):
            verbrauch = antwort.get("usage")
            weiter = kontingent.melden(verbrauch if isinstance(verbrauch, dict) else {})
        await _sag(browser, {"art": "zustand", "zustand": ZUSTAND_BEREIT})
        if not weiter:
            # Das Kontingent ist aufgebraucht. Geschlossen wird **nach** der
            # laufenden Antwort und nicht mittendrin: der Satz ist bezahlt, und
            # ihn abzuschneiden spart nichts, es klingt nur nach einem Absturz.
            lage.kontingent_aus = True
            lage.offen = False
            await _sag(browser, {"art": "kontingent"})
            with contextlib.suppress(Exception):
                await oben.close()
        return

    if art == "error":
        # Der Wortlaut des Anbieters geht **nicht** an den Browser. Er kann
        # Modellnamen, Kontingentstände und im schlechtesten Fall Teile der
        # Anweisungen enthalten. Der Benutzer erfährt, dass es hakte; das
        # Protokoll erfährt, woran.
        logger.warning("Sprachsitzung: Fehler vom Anbieter %s", _fehlerkennung(ereignis))
        await _sag(browser, {"art": "stoerung"})
        return

    # Werkzeugaufrufe bearbeitet `ai_voice_tools` — eingehängt in Schritt 3.
    if werkzeuge is not None:
        await werkzeuge.ereignis(ereignis, oben, browser)


def _fehlerkennung(ereignis: dict) -> str:
    """Nur die Kennung des Fehlers ins Protokoll, nie die ganze Nutzlast."""
    fehler = ereignis.get("error")
    if isinstance(fehler, dict):
        kennung = fehler.get("code") or fehler.get("type")
        if isinstance(kennung, str) and kennung:
            return kennung[:64]
    return "unbekannt"


async def _sag(browser: WebSocket, nutzlast: dict) -> None:
    """Ein Anzeigeereignis an den Browser, ohne die Sitzung zu riskieren."""
    if browser.client_state is not WebSocketState.CONNECTED:
        return
    with contextlib.suppress(Exception):
        await browser.send_text(json.dumps(nutzlast, ensure_ascii=False))


# ── Der Zusammenbau ───────────────────────────────────────────────────────


def verbindungsadresse(basis_url: str, modell: str) -> str:
    """Die WebSocket-Adresse aus der Basis-URL des Anbieters.

    Aus ``https://api.openai.com/v1`` wird ``wss://api.openai.com/v1/realtime``.
    Die Umformung steht hier und nicht als zweite Konstante in der Registry:
    dort steht die Adresse **einmal**, und zwei Schreibweisen derselben Adresse
    wären zwei Stellen, an denen ein Umzug nachgezogen werden müsste.
    """
    ohne_schema = basis_url.removeprefix("https://").removeprefix("http://")
    return f"wss://{ohne_schema.rstrip('/')}/realtime?model={modell}"


async def verbinden(adresse: str, schluessel: str) -> Any:
    """Die ausgehende Verbindung zur Gegenstelle.

    Eine eigene Funktion, damit die Tests sie ersetzen können, ohne dass die
    Sitzungslogik von `websockets` weiß.
    """
    if websockets is None:  # pragma: no cover - der Router prüft vorher
        raise RuntimeError("websockets fehlt; der Sprachmodus steht nicht zur Verfuegung")
    return await asyncio.wait_for(
        websockets.connect(
            adresse,
            additional_headers={"Authorization": f"Bearer {schluessel}"},
            max_size=None,
        ),
        VERBINDUNGS_TIMEOUT,
    )


async def fuehren(
    browser: WebSocket,
    *,
    adresse: str,
    schluessel: str,
    konfiguration: dict,
    verlauf: list[dict],
    werkzeuge: Any | None = None,
    kontingent: Any | None = None,
    hoechstdauer: float = MAX_SITZUNGSSEKUNDEN,
) -> Lage:
    """Eine Sprachsitzung von Anfang bis Ende.

    Die Reihenfolge ist Teil der Zusage: erst verbinden, dann **einrichten**,
    dann den Verlauf setzen, und erst danach den Browser sprechen lassen. Wer
    den Ton früher durchlässt, schickt ihn an eine Sitzung ohne Anweisungen und
    ohne Werkzeuge — das Modell antwortete dann als beliebiger Assistent, nicht
    als das Panel.

    Beide Pumpen laufen nebeneinander, und die erste, die endet, beendet die
    andere. Das ist richtig so: bricht der Browser weg, hat die Gegenstelle
    niemanden mehr; bricht die Gegenstelle weg, hat der Browser nichts mehr zu
    hören. Eine halbe Sitzung ist keine.
    """
    lage = Lage()
    oben = await verbinden(adresse, schluessel)
    try:
        await oben.send(json.dumps(konfiguration, ensure_ascii=False))
        for eintrag in verlauf:
            await oben.send(json.dumps(eintrag, ensure_ascii=False))

        await _sag(browser, {
            "art": "bereit",
            "abtastrate": ABTASTRATE,
            # Damit der Browser weiss, wann er von selbst neu verbinden muss,
            # statt eine Sitzung fuer abgestuerzt zu halten, die planmaessig
            # endet.
            "hoechstdauer": int(hoechstdauer),
        })
        await _sag(browser, {"art": "zustand", "zustand": ZUSTAND_BEREIT})

        pumpen = [
            asyncio.create_task(_browser_nach_openai(browser, oben, lage)),
            asyncio.create_task(
                _openai_nach_browser(browser, oben, lage, werkzeuge, kontingent)
            ),
        ]
        try:
            fertig, offen = await asyncio.wait(
                pumpen, timeout=hoechstdauer, return_when=asyncio.FIRST_COMPLETED
            )
            if not fertig:
                # Nichts ist von selbst zu Ende gegangen — die Hoechstdauer hat
                # zugeschlagen. Der Browser erfaehrt den Grund, damit er neu
                # verbindet statt eine Stoerung zu melden.
                await _sag(browser, {"art": "abgelaufen"})
            for aufgabe in offen:
                aufgabe.cancel()
            await asyncio.gather(*offen, return_exceptions=True)
            # Die Ausnahme der zuerst fertigen Pumpe darf nicht verschwinden:
            # ein Abbruch der Gegenstelle ist ein Befund, kein Normalfall.
            for aufgabe in fertig:
                if not aufgabe.cancelled() and aufgabe.exception() is not None:
                    raise aufgabe.exception()  # type: ignore[misc]
        finally:
            lage.offen = False
    finally:
        with contextlib.suppress(Exception):
            await oben.close()
    return lage
