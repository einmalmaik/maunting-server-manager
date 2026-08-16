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
sie vorbei.

**Was gesagt wurde, bleibt trotzdem.** Das fertige Transkript des Menschen und
der zusammengesetzte Antworttext der KI gehen als gewöhnliche `AiMessage` in
dieselbe Unterhaltung wie der getippte Chat — je Zug eine kurzlebige
Datenbanksitzung in einem eigenen Thread, damit die Tonpumpe dafür nicht
stehenbleibt. Hier stand das schon einmal als Zusage, ohne dass es jemand
umgesetzt hätte: die Sitzung schrieb nichts, und nach dem Auflegen war das
Gespräch weg. Scheitert das Schreiben, läuft das Gespräch weiter — ein
verlorener Satz im Verlauf ist ärgerlich, ein abgerissenes Gespräch ist
schlimmer.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from database import SessionLocal
from models import AiConversation, AiMessage
from services.ai_redaction import redact_sensitive_text

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

#: Wie lang eine einzelne mitgeschriebene Nachricht höchstens wird.
#:
#: Die Gegenstelle kann am Stück reden, und der getippte Chat zeigt hinterher
#: jedes Zeichen davon. Achttausend sind bei normalem Sprechtempo gut sieben
#: Minuten ununterbrochene Rede — was länger ist, ist kein Zug in einem Gespräch
#: mehr, sondern ein Ausreisser, und der soll den Verlauf nicht sprengen.
MAX_NACHRICHT_ZEICHEN = 8_000

#: Ton- und Abtastrate. Beides ist Vereinbarung mit dem Browser **und** mit
#: OpenAI: dessen Realtime-API spricht PCM16 bei 24 kHz mono.
ABTASTRATE = 24_000

#: Die Stimmen, die das Realtime-Modell sprechen kann.
#:
#: Eine Protokolltatsache wie die Abtastrate und deshalb hier: die Gegenstelle
#: weist ein `session.update` mit einem unbekannten Namen ab, und danach läuft
#: das Gespräch ohne Anweisungen und ohne Werkzeuge weiter — als beliebiger
#: Assistent, nicht als das Panel. Router und Schema holen sie hier ab; im
#: Backend gibt es sie also genau einmal.
#:
#: **Die Oberfläche hat eine Abschrift**, und zwar nach derselben Abmachung wie
#: bei `AI_LAUFZUSTAENDE`: `frontend/src/api/ai.ts::AI_STIMMEN`. Eine neunte
#: Stimme braucht deshalb drei Schritte und nicht einen — hier, dort, und
#: `ai.providers.voices.*` in beiden Sprachdateien. Der letzte ist der, den man
#: vergisst; ohne ihn steht im Auswahlfeld der rohe Schlüssel, und ein Test in
#: `frontend/src/locales/actionTexts.test.ts` bricht genau dann.
STIMMEN = ("alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse")

#: Womit gesprochen wird, solange der Betreiber nichts hinterlegt hat.
#:
#: `ai_providers.default_voice` bleibt dafür NULL und wird **nicht** mit "alloy"
#: befüllt. „Nichts hinterlegt" und „ausdrücklich alloy gewählt" sind zwei
#: verschiedene Aussagen: stünde der Standard in der Spalte, bliebe ein späterer
#: Wechsel für jeden bestehenden Zugang wirkungslos.
STANDARDSTIMME = "alloy"


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
    #: Was die KI in der **laufenden** Antwort bisher gesagt hat, Stück für
    #: Stück zusammengesetzt. Die Gegenstelle liefert das Transkript in Deltas;
    #: erst bei `response.done` ist ein Satz ein Satz und darf in den Verlauf.
    #: Ein Text statt einer Liste von Stücken, weil der Deckel dann schon beim
    #: Sammeln greift und nicht erst beim Schreiben.
    antworttext: str = ""
    #: Wann der Mensch aufgehört hat zu reden — der Zeitstempel seiner
    #: Nachricht im Verlauf.
    #:
    #: Nötig, weil die Eingabetranskription bei OpenAI **nebenher** läuft und
    #: nicht mit den `response.*`-Ereignissen synchronisiert ist. Bei einer
    #: kurzen Frage und einer schnellen Antwort trifft `response.done` vor dem
    #: fertigen Transkript ein; würde die Reihenfolge des Schreibens über
    #: `created_at` entscheiden, stünde im Verlauf die Antwort über der Frage —
    #: und `build_provider_messages` reichte sie dem Modell auch so weiter.
    #:
    #: `speech_stopped` liegt dagegen immer vor `response.created`. Der
    #: Zeitstempel wird dort genommen und wartet hier auf das Transkript.
    gesprochen_bis: datetime | None = None


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

#: Fehler des Anbieters, die der Normalbetrieb selbst ausloest.
#:
#: Der Anlass ist gemeldet und war unangenehm konkret: das Panel zeigte
#: "Sprachverbindung zum Anbieter verloren", waehrend die Verbindung stand und
#: das Gespraech ungestoert weiterlief. Ausgeloest hat es das Dazwischenreden.
#: Der Browser schickt dabei ein `response.cancel`, und er schickt es auch dann,
#: wenn gerade gar keine Antwort laeuft — er kann es nicht wissen, zwischen dem
#: letzten Tonrahmen und seiner Anzeige liegt ein Netzsprung. Die Gegenstelle
#: antwortet mit `response_cancel_not_allowed`, und daraus wurde eine Stoerung.
#:
#: Dieselbe Lage haben die beiden anderen: zwei Ausloeser fuer dieselbe Antwort
#: (`conversation_already_has_active_response`) und ein Absenden ohne Ton
#: (`input_audio_buffer_commit_empty`). Alle drei sind Rennen um Millisekunden
#: und keine Nachricht an den Benutzer. Sie stehen im Protokoll, weil eine
#: Haeufung sehr wohl ein Befund waere; alles andere bleibt Warnung **und**
#: Stoerung, denn ein stiller Fehler ist der schlechteste.
_UNKRITISCHE_FEHLER = frozenset({
    "response_cancel_not_allowed",
    "conversation_already_has_active_response",
    "input_audio_buffer_commit_empty",
})

#: Nicht tödlich, aber auch nicht in Ordnung — die Untermenge, die im Protokoll
#: laut sein soll.
#:
#: `conversation_already_has_active_response` bedeutet, dass MSM um eine Antwort
#: gebeten hat, während schon eine lief. Seit `Bruecke._antwort_anfordern` die
#: Bitte zurückhält, darf das nicht mehr vorkommen. Käme es doch, wäre die Folge
#: für den Sprechenden **Stille** — und Stille ist der Fehler, den man am
#: schwersten findet. Er kostet keine Störungsmeldung, aber eine Zeile, nach der
#: sich suchen lässt.
_VERDAECHTIGE_FEHLER = frozenset({"conversation_already_has_active_response"})


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
    gespraech_id: str | None = None,
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
        await _ereignis_verarbeiten(
            ereignis, browser, oben, lage, werkzeuge, kontingent, gespraech_id
        )


async def _ereignis_verarbeiten(
    ereignis: dict,
    browser: WebSocket,
    oben: Any,
    lage: Lage,
    werkzeuge: Any | None,
    kontingent: Any | None = None,
    gespraech_id: str | None = None,
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
            # Und dasselbe Stück in den Puffer, aus dem bei `response.done` die
            # Nachricht im Verlauf wird. Der Deckel greift hier und nicht erst
            # dort: ein Modell, das nicht aufhört zu reden, soll Speicher der
            # Sitzung nicht in dem Tempo belegen, in dem es spricht.
            if len(lage.antworttext) < MAX_NACHRICHT_ZEICHEN:
                lage.antworttext += stueck
        return

    if art == _EINGABETEXT_FERTIG:
        gesagt = ereignis.get("transcript")
        if isinstance(gesagt, str) and gesagt.strip():
            await _sag(browser, {"art": "gehoert", "text": gesagt.strip()})
            # Der Zeitstempel stammt vom Ende des Sprechens und nicht von
            # jetzt: bis hierher kann die Antwort längst geschrieben sein.
            gesagt_um, lage.gesprochen_bis = lage.gesprochen_bis, None
            await _mitschreiben(gespraech_id, "user", gesagt, zeitpunkt=gesagt_um)
        return

    if art == "input_audio_buffer.speech_started":
        await _sag(browser, {"art": "zustand", "zustand": ZUSTAND_HOERT})
        return

    if art == "input_audio_buffer.speech_stopped":
        lage.gesprochen_bis = datetime.now(timezone.utc)
        await _sag(browser, {"art": "zustand", "zustand": ZUSTAND_DENKT})
        return

    if art == "response.created":
        # Die Brücke muss das wissen, bevor der erste Werkzeugaufruf kommt:
        # solange eine Antwort läuft, darf sie um keine zweite bitten. Siehe
        # `Bruecke._antwort_laeuft` — dort steht, warum das der Unterschied
        # zwischen einer Auskunft und einer Gesprächspause ist.
        if werkzeuge is not None:
            werkzeuge.antwort_begonnen()
        await _sag(browser, {"art": "zustand", "zustand": ZUSTAND_SPRICHT})
        return

    if art == "response.done":
        # Der Puffer wird **immer** geleert, auch wenn das Schreiben gleich
        # scheitert. Bliebe der Satz stehen, klebte er vorn an der nächsten
        # Antwort und stünde damit ein zweites Mal im Verlauf — an einer Stelle,
        # an der ihn niemand gesagt hat.
        gesprochenes, lage.antworttext = lage.antworttext, ""
        await _mitschreiben(gespraech_id, "assistant", gesprochenes)

        antwort = ereignis.get("response")

        # Und jetzt darf die Brücke reden, falls ein Werkzeugergebnis wartete.
        # **Vor** der Kontingentprüfung: wartet eines und ist gleichzeitig das
        # Kontingent zu Ende, wird gleich zugemacht — dann soll die Bitte gar
        # nicht erst hinausgehen.
        if werkzeuge is not None:
            zustand = antwort.get("status") if isinstance(antwort, dict) else None
            await werkzeuge.antwort_beendet(oben, abgebrochen=zustand == "cancelled")

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
        kennung = _fehlerkennung(ereignis)
        if kennung in _VERDAECHTIGE_FEHLER:
            # Keine Störungsmeldung — die Leitung steht. Aber laut im
            # Protokoll: siehe `_VERDAECHTIGE_FEHLER`.
            logger.warning(
                "Sprachsitzung: Antwort trotz laufender Antwort angefordert (%s) — "
                "der Sprechende hoert an dieser Stelle nichts",
                kennung,
            )
            return
        if kennung in _UNKRITISCHE_FEHLER:
            # Siehe `_UNKRITISCHE_FEHLER`: es ist der eigene Normalbetrieb, der
            # diesen Fehler auslöst. Eine Störungsmeldung dafür wäre falsch —
            # und sie war es, sichtbar als „Sprachverbindung zum Anbieter
            # verloren" mitten in einem Gespräch, das weiterlief.
            logger.info("Sprachsitzung: unkritischer Anbieterfehler %s", kennung)
            return
        logger.warning("Sprachsitzung: Fehler vom Anbieter %s", kennung)
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


# ── Der Verlauf ───────────────────────────────────────────────────────────


async def _mitschreiben(
    gespraech_id: str | None,
    rolle: str,
    text: str,
    *,
    zeitpunkt: datetime | None = None,
) -> None:
    """Einen gesprochenen Zug in den Verlauf des getippten Chats legen.

    Ohne Kennung der Unterhaltung wird nichts geschrieben. Das ist kein
    Ausnahmefall, sondern der Vorgabewert von `fuehren`: die Tests der
    Sitzungsmechanik führen keine Datenbank mit, und sie sollen es auch nicht
    müssen, um die Reihenfolge zweier Ereignisse zu prüfen.

    Der Text des Menschen wird geschwärzt, der der KI nicht — dieselbe
    Asymmetrie wie im Chat und aus demselben Grund (`_finalize_stream`): in der
    Antwort kann eine Zuweisung eine *Anleitung* sein, und `[REDACTED]` an
    dieser Stelle wäre keine Antwort mehr. Auf dem Weg zum Anbieter geht die
    Historie ohnehin noch einmal durch `redact_sensitive_text`.

    Geschrieben wird in einem Thread. Auf der Ereignisschleife stünde in dieser
    Zeit die Tonpumpe, und eine Sprachsitzung merkt sich das als Aussetzer.
    Gewartet wird trotzdem darauf, damit zwei Schreibvorgänge nicht nebenher
    laufen.

    Für die **Reihenfolge** im Verlauf reicht das aber nicht, und hier stand
    einmal, sie sei die der Gegenstelle. Das ist sie nicht: die
    Eingabetranskription läuft bei OpenAI nebenher, und bei einer kurzen Frage
    trifft `response.done` vor dem fertigen Transkript ein. Sortiert wird nach
    `created_at`, also bekommt der Zug des Menschen mit ``zeitpunkt`` den
    Stempel vom Ende seines Sprechens mitgegeben statt den vom Schreiben. Ohne
    ihn stünde die Antwort über der Frage — im Panel und, über
    `build_provider_messages`, auch im nächsten Prompt.
    """
    if not gespraech_id:
        return
    sauber = text.strip()
    if rolle == "user":
        sauber = redact_sensitive_text(sauber).strip()
    if not sauber:
        # Eine leere Nachricht ist keine. Die Gegenstelle schickt sowohl
        # `response.done` für reine Werkzeugrunden als auch Transkripte, die nur
        # aus Hintergrundgeräusch entstanden sind.
        return

    try:
        await asyncio.to_thread(
            _nachricht_ablegen,
            gespraech_id,
            rolle,
            sauber[:MAX_NACHRICHT_ZEICHEN],
            zeitpunkt,
        )
    except Exception as fehler:
        # Ein Fehlschlag kostet einen Satz im Verlauf und nicht das Gespräch.
        # Der Mensch redet gerade; ihm die Verbindung abzureissen, weil eine
        # Zeile nicht in die Datenbank ging, wäre die schlechtere Antwort.
        logger.warning(
            "Sprachsitzung: Nachricht nicht gespeichert rolle=%s error=%s",
            rolle, type(fehler).__name__,
        )


def _nachricht_ablegen(
    gespraech_id: str, rolle: str, text: str, zeitpunkt: datetime | None = None
) -> None:
    """Der Schreibvorgang selbst — läuft im Thread, nie auf der Ereignisschleife.

    Eine eigene, kurzlebige Sitzung je Zug. Die Sprachsitzung hält bewusst keine
    offene Datenbanksitzung: sie läuft über Minuten, und eine Verbindung aus dem
    Pool so lange festzuhalten kostet sie einem Request, der sie braucht.

    Ist die Unterhaltung weg, wird nichts geschrieben. Der Fremdschlüssel würde
    das ohnehin abweisen; die Abfrage davor macht aus einem Fehler im Protokoll
    einen stillen, richtigen Nichtsttun-Fall.
    """
    with SessionLocal() as db:
        gespraech = db.get(AiConversation, gespraech_id)
        if gespraech is None:
            return
        nachricht = AiMessage(
            id=str(uuid4()),
            conversation_id=gespraech.id,
            role=rolle,
            content=text,
            status="complete",
        )
        if zeitpunkt is not None:
            # Nur wenn einer mitkam. Sonst gilt der Spaltendefault, und das ist
            # für die Antwort der KI auch der richtige Zeitpunkt — sie ist in
            # dem Moment fertig geworden.
            nachricht.created_at = zeitpunkt
        db.add(nachricht)
        # Ohne das rutschte die Unterhaltung im Panel nach unten, obwohl gerade
        # in ihr gesprochen wurde. `sections_json` bleibt NULL: die Sitzung
        # führt keine Gliederung aus Text und Werkzeugen, und der Verlauf
        # zeichnet solche Nachrichten als reinen Text — was gesprochener Text
        # auch ist.
        gespraech.updated_at = datetime.now(timezone.utc)
        db.commit()


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


def probe_fehlercode(fehler: BaseException) -> str:
    """Ein Fehlschlag der Probe als Code, den die Oberfläche schon kennt.

    Absichtlich auf die vorhandenen ``AI_PROVIDER_*``-Codes abgebildet statt auf
    neue: sie sind in beiden Sprachdateien übersetzt und sagen dasselbe. Ein
    eigener Satz Codes für den Sprachweg wäre eine zweite Wortwahl für dieselben
    drei Fälle — falscher Schlüssel, falsches Modell, nicht erreichbar.

    Der Wortlaut des Anbieters geht **nicht** mit. Er kann Kontingentstände und
    Kontonamen enthalten; der Code sagt dem Betreiber, was zu tun ist.
    """
    antwort = getattr(fehler, "response", None)
    status = getattr(antwort, "status_code", None)
    if isinstance(status, int):
        if status in (401, 403):
            return "AI_PROVIDER_AUTH_FAILED"
        if status in (400, 404):
            # Bei OpenAI heisst das fast immer: die Modellkennung stimmt nicht.
            # Die Adresse baut MSM selbst, sie kann nicht danebenliegen.
            return "AI_PROVIDER_REQUEST_REJECTED"
        if status == 429:
            return "AI_PROVIDER_RATE_LIMITED"
    if isinstance(fehler, (asyncio.TimeoutError, TimeoutError)):
        return "AI_PROVIDER_STREAM_TIMEOUT"
    return "AI_PROVIDER_UNAVAILABLE"


#: Wie lange die Probe auf das erste Ereignis wartet. Kurz, weil sie im
#: Einstellungsdialog auf einen Klick hin läuft und niemand zwanzig Sekunden vor
#: einem Spinner sitzen soll.
PROBE_TIMEOUT = 8.0


async def pruefen(adresse: str, schluessel: str) -> None:
    """Öffnet die Sitzung einmal und legt sofort wieder auf.

    Das Gegenstück zum Chattest, und es muss ein eigenes sein: der Chattest
    schickt ein „ping" an ``/chat/completions``, und darauf antwortet OpenAI bei
    einem Sprachmodell wörtlich *„This is not a chat model"*. Der Betreiber
    bekäme also eine Fehlermeldung für einen richtig eingerichteten Zugang.

    Geprüft wird genau das, was später auch passiert: Adresse, Schlüssel und
    Modell zusammen. Schon der Handschlag entscheidet — ein falscher Schlüssel
    endet in einem 401, ein falsches Modell in einem 400 oder 404, und beides
    kommt zurück, bevor ein einziger Ton geflossen ist. Es kostet nichts: eine
    Sitzung, die niemand bespricht, hat keine Tokens.
    """
    verbindung = await verbinden(adresse, schluessel)
    try:
        # Auf das erste Ereignis warten (`session.created`). Ohne das würde ein
        # Anbieter, der die Verbindung erst nach dem Handschlag ablehnt, als
        # Erfolg durchgehen.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(verbindung.recv(), PROBE_TIMEOUT)
    finally:
        with contextlib.suppress(Exception):
            await verbindung.close()


async def fuehren(
    browser: WebSocket,
    *,
    adresse: str,
    schluessel: str,
    konfiguration: dict,
    verlauf: list[dict],
    gespraech_id: str | None = None,
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

    ``gespraech_id`` ist die Unterhaltung, in die mitgeschrieben wird — dieselbe
    wie im getippten Chat, denn es ist derselbe Chat mit einem anderen Eingang.
    Ohne sie spricht die Sitzung genauso, sie merkt sich nur nichts; das ist der
    Fall in den Tests, die keine Datenbank brauchen, um die Übersetzung zwischen
    zwei Protokollen zu prüfen.
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
                _openai_nach_browser(
                    browser, oben, lage, werkzeuge, kontingent, gespraech_id
                )
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
