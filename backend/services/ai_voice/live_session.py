"""GPT-Live: WebRTC im Browser, Sideband, Backend-Anbindung und Werkzeuge hier.

GPT-Live (``gpt-live-1``) ist kein weiteres Realtime-Modell, sondern ein eigener
Endpunkt mit eigenem Protokoll (``POST /v1/live/sessions``). Die Stimme hört und
spricht gleichzeitig und entscheidet selbst, wann sie redet. Nachgedacht und
gehandelt wird dahinter, in einem **Backend-Modell**, an das GPT-Live Aufgaben
abgibt („Responses delegation"). Die Werkzeuge ruft dieses Backend; ausgeführt
werden sie hier, über denselben Eingang wie bei Realtime
(`voice_werkzeug_ausfuehren`) und mit denselben Rechten und Schranken. Der Weg
ändert, **wer** ein Werkzeug wählt, nicht, **was** erlaubt ist.

Was bleibt wie bei Realtime (`realtime_session.RealtimeSitzung`, von der die
Klasse erbt): der Handschlag über das Panel, das Sideband auf dem Server, die
Werkzeugausführung, Vorschläge, Regionsnachträge und Meldungen. Was am Protokoll
hängt, steht in den Nahtstellen der Elternklasse und in dieser Datei — sonst
nirgends.

Quellen, alle gelesen am 22.09.2026 unter developers.openai.com/api:

* ``docs/guides/voice-webrtc?api=live`` — Anlage per JSON mit ``session`` und
  ``transport``, Antwort 201 mit ``session.id`` und ``transport.sdp``, kein
  ``session.start`` auf WebRTC; die Anlage berechnet 15 Sekunden.
* ``docs/guides/voice-server-controls?api=live`` — das Sideband unter
  ``…/live/sessions/{id}/attach``, gespiegeltes Audio, „execute the function
  once".
* ``docs/guides/live-delegation`` — Werkzeugaufrufe aus
  ``response.output_item.done``, Ergebnisse per ``response.item.create``,
  Fortsetzung per ``response.create``, Rahmen des Backend-Prompts.
* ``docs/guides/live-conversations`` — Abschriften, Moderation,
  ``session.close`` und ``session.closed``.
* ``docs/guides/live-prompting`` — Aufbau der Anweisungen an die Stimme.
* ``docs/guides/voice-latency-cost?api=live`` — Dauer als laufende Summe,
  Backend-Token je Antwort genau einmal.
* ``reference/resources/live/sideband-websocket`` und ``…/primary-websocket`` —
  die Felder im Einzelnen.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from itertools import count
from typing import Callable

try:
    import websockets
except ImportError:  # Der Sprachmodus ist eine optionale Betriebsart.
    websockets = None  # type: ignore[assignment]
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiProvider, User
from services import ai_model_catalog, ai_prompt, ai_provider_service, ai_reasoning, ai_usage_service
from services.ai_voice.realtime_session import (
    ENTITAETEN,
    ESKALATION,
    MAX_SDP_ZEICHEN,
    REGION_ANWEISUNGEN,
    WERKZEUG_REGELN,
    RealtimeSitzung,
    RealtimeSitzungsfehler,
    RealtimeVorbereitung,
    angebotene_werkzeuge,
    gedaechtnis,
    gedaechtnis_anhang,
    reservieren,
    sprachregel,
)
from services.ai_voice.sprachwege import OPENAI_LIVE
from services.ai_voice_debug import emit as voice_debug
from services.openai_compatible_adapter import SICHERHEITSSTOPP
from services.openai_responses_websocket import fehler_im_rahmen


#: Die Anlage einer WebRTC-Sitzung berechnet OpenAI mit 15 Sekunden, und diese
#: werden auf die Laufzeit angerechnet („credited against duration charges once
#: the session starts running; it is not an extra 15 seconds"). Gebucht wird
#: deshalb nie weniger — und nie beides zusammen.
ANLAGE_SEKUNDEN = 15
#: Wie lange nach ``session.close`` auf ``session.closed`` gewartet wird —
#: dieselbe Frist wie im Browserbeispiel der Dokumentation. Kommt nichts, bleibt
#: die zuletzt gemeldete Dauer gebucht, und die Enddauer ist unbestätigt.
SCHLIESSFRIST_SEKUNDEN = 15.0
#: Wie lange der Katalog nach dem Backend-Modell gefragt wird, bevor die Sitzung
#: ohne seine Auskunft angelegt wird. Er antwortet fast immer aus dem Speicher;
#: die Frist ist für den Fall, dass er es nicht tut, und die Stimme soll dann
#: nicht auf ihn warten.
KATALOGFRIST_SEKUNDEN = 3.0
#: Nach wieviel Stille ein Sprecher als fertig gilt — **nur** für die Anzeige und
#: für den Zeitpunkt von Nachträgen. Die Dokumentation ist ausdrücklich: „Gaps
#: between transcript events alone do not establish silence." Eine Entscheidung,
#: die zählt (ein Werkzeug, eine Buchung, ein Abschluss), hängt deshalb nie daran.
STILLE_SEKUNDEN = 1.5
#: Mehr Dauer als ein Tag ist keine Messung, sondern ein kaputter Rahmen.
MAX_SEKUNDEN = 24 * 3600
MAX_RAHMEN_ZEICHEN = 2 * 1024 * 1024
MAX_ANLAGE_ANTWORT_ZEICHEN = 256 * 1024
MAX_CALL_ID_ZEICHEN = 256
MAX_ABSCHRIFT_ZEICHEN = 2_000
MAX_FAEHIGKEIT_ZEICHEN = 160
#: Die Sitzungskennung landet in einem URL-Pfad. OpenAI nennt sie opak („Treat
#: the session ID as opaque and preserve it unchanged, including its prefix") —
#: unverändert heisst aber nicht ungeprüft. Zugelassen sind nur Zeichen, die in
#: einem Pfadsegment nichts bedeuten, und kein Punkt am Anfang (``..``).
_SITZUNGS_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._~-]{0,255}$")
_FEHLERCODE = re.compile(r"^[a-z0-9_.]{1,64}$")
#: Die Enden einer Backend-Antwort. ``response.completed`` trägt die Nutzung;
#: eine abgebrochene oder gescheiterte Antwort hat trotzdem etwas gekostet.
_ANTWORT_ENDEN = frozenset({"response.completed", "response.incomplete", "response.failed"})


@dataclass(frozen=True)
class LiveVorbereitung(RealtimeVorbereitung):
    """Der Sitzungsvertrag für GPT-Live.

    ``instructions`` sind die der Stimme, ``tools`` die des Backends —
    ``backend_model`` und ``backend_instructions`` gehören dazu.
    ``reasoning_effort`` ist die Denkstufe des **Backends**; die Stimme selbst
    führt keine.
    """

    backend_model: str = ""
    backend_instructions: str = ""


def _faehigkeit(tool: dict) -> str | None:
    """Eine Zeile für „Backend tools:" — Name und erster Satz der Beschreibung.

    Die Stimme entscheidet anhand dieser Liste, was sie abgibt („GPT-Live uses
    this list to decide which requests to hand off"). Ausgeführt wird nach der
    Liste nichts; die echten Schemas bekommt das Backend.
    """
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        return None
    beschreibung = " ".join(str(tool.get("description") or "").split())
    satz = beschreibung.split(". ", 1)[0].rstrip(".")
    if len(satz) > MAX_FAEHIGKEIT_ZEICHEN:
        satz = satz[:MAX_FAEHIGKEIT_ZEICHEN].rsplit(" ", 1)[0] + " …"
    return f"- {name}: {satz}" if satz else f"- {name}"


def live_anweisungen(tools: list[dict], sprache: str, memory: str = "") -> str:
    """Die Anweisungen an die Stimme — Rolle, Ton und wann sie abgibt.

    Aufgebaut nach der Vorlage aus „Prompting GPT-Live": die drei Überschriften
    ``Backchannel policy``, ``Interruption policy`` und ``Delegation policy``
    samt ``Backend tools``, ``Delegate to the backend when`` und ``Do not
    delegate to the backend when`` bleiben wörtlich englisch („Keep the
    template's … headings"); der Text darunter ist in der Sprache, in der die
    Stimme spricht. Verfahren und Werkzeugregeln gehören nicht hierher, sondern
    ins Backend (`backend_anweisungen`).
    """
    faehigkeiten = "\n".join(zeile for zeile in map(_faehigkeit, tools) if zeile)
    return "\n\n".join((
        "Du bist die Stimme des MSM-Assistenten, des Assistenten eines Gameserver-Panels. "
        "Hinter dir arbeitet ein Backend mit den Werkzeugen des Panels: Server, Logs, "
        "Konfigurationen, Mods, Netzwerk und Nodes, dazu Notizen, Kalender, Karten und "
        "Websuche. Ganz normale Fragen beantwortest du selbst.\n"
        "Sprich ruhig, direkt und natürlich, wie jemand, der sein Fach kennt: keine "
        "Füllsätze, keine Höflichkeitsschleifen, keine gespielte Begeisterung. Nenne "
        "zuerst das Ergebnis, dann nur die nötigen Details. Nenne Zahlen gerundet und in "
        "Worten und lies keine Pfade, Kennungen oder Feldnamen vor. Nenne nie Namen, "
        "Familie oder Anbieter des Sprachmodells, das dich antreibt.\n"
        + sprachregel(sprache),
        "Backchannel policy: Nutze Rückmeldelaute sparsam. Bestätige natürlich, ohne mit "
        "der eigentlichen Antwort zu konkurrieren.",
        "Interruption policy: Hör auf zu sprechen, wenn der Benutzer dich unterbricht, "
        "und hör zu, was er sagt.",
        "Delegation policy:\nBackend tools:\n" + faehigkeiten,
        "Delegate to the backend when:\n"
        "- Die Anfrage braucht eines der Backend-Werkzeuge, Daten aus dem Panel oder "
        "aktuelle Informationen.\n"
        "- Die Antwort braucht sorgfältiges Nachdenken über eine einfache Auskunft hinaus.\n"
        "- Eine Korrektur ändert eine Aufgabe, die schon läuft.\n"
        "- Der Benutzer stimmt einem offenen Vorschlag zu oder lehnt ihn ab.",
        "Do not delegate to the backend when:\n"
        "- Du aus dem Gespräch oder einem noch gültigen Ergebnis antworten kannst.\n"
        "- Der Benutzer grüsst dich oder bittet dich, ein Ergebnis zu wiederholen.\n"
        "- Du eine kurze Rückfrage brauchst, um die Anfrage zu verstehen.",
        "Delegiere, bevor du eine Antwort gibst, die von Backend-Arbeit abhängt. Rate kein "
        "Ergebnis, während du wartest, und melde eine Aktion erst als erledigt, wenn das "
        "Backend es bestätigt hat.",
    )) + gedaechtnis_anhang(memory)


def backend_anweisungen(basis_prompt: str, memory: str = "") -> str:
    """Die Anweisungen an das Backend-Modell.

    Die drei Abschnitte sind die aus „Start with your existing backend prompt":
    der Gesprächsrahmen, die Aufgabe (hier der Prompt des Panels in der Rolle
    ``live``, siehe `ai_prompt.HINTER_DER_STIMME`, samt denselben Werkzeugregeln
    wie bei Realtime) und die Form des Ergebnisses.
    """
    return "\n\n".join((
        "## Voice conversation context\n"
        "Du hilfst einem Assistenten in einem laufenden Sprachgespräch. Abschriften können "
        "Fehler, unfertige Sätze und spätere Korrekturen enthalten. Nutze den neuesten "
        "Kontext und geprüfte Daten. Ist ein nötiges Detail noch unklar, frag danach, statt "
        "zu raten.",
        "## Task instructions\n" + basis_prompt,
        "# Tools\nUnabhängige Werkzeuge parallel nutzen. " + WERKZEUG_REGELN,
        *REGION_ANWEISUNGEN,
        ENTITAETEN,
        "# Long Context Behavior\nKeinen Chatverlauf erwarten. Nutze nur, was der "
        "Sprachassistent übergibt, den aktuellen Panelzustand und freigegebene Erinnerungen.",
        ESKALATION,
        "## Return the result\n"
        "Gib die relevanten Fakten, den aktuellen Stand der Aufgabe und den nächsten Schritt "
        "zurück. Melde eine Aktion erst als erledigt, wenn das Werkzeug den Erfolg bestätigt. "
        "Ist das Ergebnis unklar, sag das und was geprüft werden muss.",
    )) + gedaechtnis_anhang(memory)


def vorbereiten(
    db: Session,
    *,
    provider: AiProvider,
    user: User,
    herkunft: str,
) -> LiveVorbereitung:
    """Friert den Sitzungsvertrag ein — Stimme, Backend und Werkzeuge."""
    ai_provider_service._assert_realtime_werte(provider)
    api_key = ai_provider_service.resolve_api_key(db, provider, user.id)
    if not api_key:
        raise RealtimeSitzungsfehler("REALTIME_NOT_CONFIGURED")
    backend = ai_provider_service.backend_modell(provider)
    if backend is None:
        raise RealtimeSitzungsfehler("REALTIME_NOT_CONFIGURED")

    tools = angebotene_werkzeuge(db, provider=provider, user=user, herkunft=herkunft)
    sprache = provider.realtime_language or "auto"
    memory = gedaechtnis(db, user)
    basis_prompt = ai_prompt.build(
        gesprochen=True,
        rolle="live",
        desktop=herkunft == "desktop",
        db=db,
    )
    conversation_id, usage_event_id = reservieren(db, provider=provider, user=user)
    return LiveVorbereitung(
        provider_id=provider.id,
        provider_kind=provider.provider_kind,
        base_url=ai_provider_service.base_url(provider),
        model=provider.realtime_model or "",
        voice=provider.realtime_voice or "",
        reasoning_effort=provider.realtime_reasoning_effort,
        language=sprache,
        api_key=api_key,
        instructions=live_anweisungen(tools, sprache, memory),
        tools=tools,
        conversation_id=conversation_id,
        usage_event_id=usage_event_id,
        disable_safety=bool(getattr(provider, "disable_safety", False)),
        backend_model=backend,
        backend_instructions=backend_anweisungen(basis_prompt, memory),
    )


def _session_config(v: LiveVorbereitung, stufe: str | None) -> dict:
    """``session`` für ``POST /v1/live/sessions``.

    * kein ``audio.format`` — WebRTC handelt das Format selbst aus („omit
      ``audio.format`` from session configuration").
    * ``client.data_channel`` mit zwei leeren Listen: der Browser darf über
      seinen Datenkanal nichts senden und bekommt nichts. Genau dafür ist das
      Feld da („capabilities for an untrusted frontend attached to a unified
      WebRTC session. Trusted sideband connections are unaffected"). Ohne das
      bekäme der Browser mit ``session.started`` die Anweisungen und Werkzeuge
      des Backends zu sehen, und ein ``session.update`` von dort tauschte
      Backend-Modell und Anweisungen aus. Was der Browser anzeigen soll,
      schickt ihm das Panel selbst.
    * ``parallel_tool_calls`` an: unabhängige Werkzeuge laufen hier ohnehin
      nebeneinander (`_werkzeug_nebenlaeufigkeit`), und „Run independent work
      concurrently" steht im Latenzabschnitt der Delegationsseite. Die
      Migrationsseite rät zu ``false`` nur „for the initial migration" — die
      Buchführung hier trägt beliebig viele Aufrufe je Antwort.
    * ``store`` aus: nichts wird bei OpenAI für spätere Abzweigungen oder
      Mitschnitte aufbewahrt. Das ist die Vorgabe, hier steht es ausdrücklich.
    """
    responses: dict = {
        "model": v.backend_model,
        "instructions": v.backend_instructions,
        "parallel_tool_calls": True,
    }
    if v.tools:
        responses["tools"] = v.tools
        responses["tool_choice"] = "auto"
    if stufe is not None:
        responses["reasoning"] = {"effort": stufe}
    return {
        "model": v.model,
        "instructions": v.instructions,
        "audio": {"output": {"voice": v.voice}},
        "delegation": {"type": "responses", "responses": responses},
        "client": {"data_channel": {"allowed_client_events": [], "allowed_server_events": []}},
        "store": False,
    }


def _anlage_lesen(text: str) -> tuple[str, str]:
    """(Sitzungskennung, Antwort-SDP) aus der Antwort auf die Anlage."""
    if not text or len(text) > MAX_ANLAGE_ANTWORT_ZEICHEN:
        raise RealtimeSitzungsfehler("REALTIME_HANDSHAKE_FAILED")
    try:
        daten = json.loads(text)
    except ValueError as exc:
        raise RealtimeSitzungsfehler("REALTIME_HANDSHAKE_FAILED") from exc
    sitzung = daten.get("session") if isinstance(daten, dict) else None
    transport = daten.get("transport") if isinstance(daten, dict) else None
    kennung = sitzung.get("id") if isinstance(sitzung, dict) else None
    sdp = transport.get("sdp") if isinstance(transport, dict) else None
    if not isinstance(kennung, str) or not _SITZUNGS_ID.fullmatch(kennung):
        raise RealtimeSitzungsfehler("REALTIME_HANDSHAKE_FAILED")
    if not isinstance(sdp, str) or not sdp.strip() or len(sdp) > MAX_SDP_ZEICHEN:
        raise RealtimeSitzungsfehler("REALTIME_HANDSHAKE_FAILED")
    return kennung, sdp


def _fehlercode(text: str) -> str | None:
    """``error.code`` aus einem abgelehnten Anlageversuch — nur fürs Protokoll.

    Nur ein kurzes Codewort geht durch; der Rest der Antwort ist Fremdtext.
    """
    try:
        daten = json.loads(text[:64 * 1024])
    except ValueError:
        return None
    fehler = daten.get("error") if isinstance(daten, dict) else None
    code = fehler.get("code") if isinstance(fehler, dict) else None
    return code if isinstance(code, str) and _FEHLERCODE.fullmatch(code) else None


def _lesen(roh: object) -> dict | None:
    if not isinstance(roh, str) or len(roh) > MAX_RAHMEN_ZEICHEN:
        return None
    try:
        ereignis = json.loads(roh)
    except ValueError:
        return None
    return ereignis if isinstance(ereignis, dict) else None


def _sekunden(ereignis: dict) -> float | None:
    """``usage.seconds`` aus ``session.usage.updated`` oder ``session.closed``."""
    usage = ereignis.get("usage")
    wert = usage.get("seconds") if isinstance(usage, dict) else None
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    if not math.isfinite(wert) or wert < 0 or wert > MAX_SEKUNDEN:
        return None
    return float(wert)


def _antwort_von(inner: dict) -> dict:
    antwort = inner.get("response")
    return antwort if isinstance(antwort, dict) else {}


def _kennung(wert: object) -> str | None:
    return wert if isinstance(wert, str) and wert else None


class LiveSitzung(RealtimeSitzung):
    """Eine GPT-Live-Sitzung.

    Die Buchführung der Backend-Antworten folgt „Handle Responses delegation":
    Aufrufe kommen aus ``response.output_item.done``, jeder Aufruf bekommt genau
    ein Ergebnis, und fortgesetzt wird erst, wenn die Antwort beendet **und**
    jedes ihrer Ergebnisse abgegeben ist — „Send a response.item.create result
    for every pending function call, then send response.create". Wann eine
    Antwort beendet ist, sagt ihr Lebenszyklus (``response.completed`` auch dann,
    wenn Aufrufe offen sind); die Antwort selbst enthält dort keine Ausgabe mehr
    („contain ``response.output: []`` even when function calls need results").
    """

    def __init__(self, websocket, **kwargs) -> None:
        super().__init__(websocket, **kwargs)
        self._nummern = count(1)
        self._sitzungs_id: str | None = None
        #: (Textpreis ein, Textpreis aus, Minutenpreis) — einmal beim Aufbau
        #: gelesen. Ein Preis, der sich mitten in der Sitzung ändert, machte die
        #: laufenden Summen unten zu einer Mischung aus zwei Tarifen.
        self._preise: tuple[int, int, int] = (0, 0, 0)
        #: Backend-Antworten, die begonnen und noch nicht geendet haben.
        self._laufende: set[str] = set()
        self._aktuelle_antwort: dict[str | None, str] = {}
        self._letzte_antwort: str | None = None
        #: Wieviele Aufrufe eine noch laufende Antwort gestellt hat.
        self._aufrufe_je_antwort: dict[str, int] = {}
        #: Beendete Antworten, die auf ihre Ergebnisse warten.
        self._wartende = 0
        self._verwaist = False
        self._aufgerufen: set[str] = set()
        self._gebuchte_antworten: set[str] = set()
        self._dauer_ms = 0
        self._dauer_kosten = 0
        #: Gebucht wird in Threads (`asyncio.to_thread`). Wird ein Leser mitten
        #: in einer Buchung abgebrochen, läuft sein Thread weiter, während der
        #: Abbau daneben bucht — ohne Schloss läsen beide denselben alten Stand
        #: und buchten dieselbe Differenz zweimal.
        self._buchungsschloss = threading.Lock()
        self._eingabe = ""
        self._zuletzt_sprach: str | None = None
        self._zuletzt_gehoert = 0.0
        self._zuletzt_gesagt = 0.0
        self._bereit_gemeldet = True
        self._geschlossen = False

    # ── Rahmen ────────────────────────────────────────────────────────

    def _befehl(self, art: str, **felder: object) -> dict:
        """Ein Befehl an die Sitzung, mit eigener ``event_id``.

        Die Kennung ist freiwillig; mit ihr lässt sich ein ``error`` über
        ``client_event_id`` dem Befehl zuordnen, der ihn ausgelöst hat.
        """
        return {"type": art, "event_id": f"msm_{next(self._nummern)}", **felder}

    def _eintrag_rahmen(self, item: dict) -> dict:
        # Ein Eintrag geht bei GPT-Live nicht in das Gespräch der Stimme, sondern
        # in das des Backends — dort, wo Werkzeugergebnisse und getippte Eingaben
        # hingehören („queue a user message for the backend").
        return self._befehl("response.item.create", item=item)

    def _antwort_rahmen(self) -> dict:
        return self._befehl("response.create")

    async def _senden(self, rahmen: dict) -> None:
        assert self._sideband is not None
        await self._sideband.send(json.dumps(rahmen))

    # ── Aufbau ────────────────────────────────────────────────────────

    async def _handshake(self, sdp: str) -> str:
        if not sdp or len(sdp) > MAX_SDP_ZEICHEN:
            await self._debug_senden("REALTIME_SDP_INVALID", hint="SDP groesse ungueltig")
            raise RealtimeSitzungsfehler("REALTIME_SDP_INVALID")
        if websockets is None:
            await self._debug_senden("REALTIME_NOT_AVAILABLE", hint="websockets Paket fehlt")
            raise RealtimeSitzungsfehler("REALTIME_NOT_AVAILABLE")
        basis = self.v.base_url.rstrip("/")
        if not basis.startswith("https://"):
            raise RealtimeSitzungsfehler("REALTIME_NOT_CONFIGURED")
        self._preise = await asyncio.to_thread(self._preise_lesen)
        stufe = await self._backend_stufe()
        kopf = {"Authorization": f"Bearer {self.v.api_key}"}
        try:
            antwort = await self.http.post(
                f"{basis}/live/sessions",
                headers=kopf,
                json={
                    "session": _session_config(self.v, stufe),
                    "transport": {"type": "webrtc", "sdp": sdp},
                },
            )
        except Exception as exc:
            await self._debug_senden("REALTIME_HANDSHAKE_FAILED", hint=type(exc).__name__)
            raise RealtimeSitzungsfehler("REALTIME_HANDSHAKE_FAILED") from exc
        if antwort.status_code not in {200, 201}:
            code = _fehlercode(antwort.text or "")
            await self._debug_senden(
                "REALTIME_HANDSHAKE_FAILED",
                hint=f"HTTP {antwort.status_code}" + (f" {code}" if code else ""),
            )
            raise RealtimeSitzungsfehler("REALTIME_HANDSHAKE_FAILED")
        try:
            kennung, antwort_sdp = _anlage_lesen(antwort.text)
        except RealtimeSitzungsfehler:
            # 2xx heisst angelegt, und angelegt heisst berechnet — auch wenn
            # sich die Antwort nicht lesen lässt. Schliessen lässt sich eine
            # Sitzung ohne Kennung nicht; sie endet bei OpenAI von selbst.
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._dauer_buchen, 0.0, 1)
            await self._debug_senden("REALTIME_HANDSHAKE_FAILED", hint="Antwort der Anlage ungueltig")
            raise
        self._sitzungs_id = kennung
        adresse = "wss://" + basis.removeprefix("https://") + f"/live/sessions/{kennung}/attach"
        try:
            self._sideband = await websockets.connect(
                adresse,
                additional_headers=kopf,
                max_size=MAX_RAHMEN_ZEICHEN,
                open_timeout=10,
            )
        except Exception as exc:
            # Angelegt ist die Sitzung trotzdem, und damit berechnet. Gebucht
            # wird die Anlage auch dann, wenn danach nichts mehr geht.
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._dauer_buchen, 0.0, 1)
            await self._debug_senden("REALTIME_HANDSHAKE_FAILED", hint=f"Sideband: {type(exc).__name__}")
            raise RealtimeSitzungsfehler("REALTIME_HANDSHAKE_FAILED") from exc
        # Erst nach dem Sideband: überschreitet schon die Anlage eine Grenze,
        # lässt sich die Sitzung so noch ordentlich schliessen.
        await self._buchen_oder_beenden(self._dauer_buchen, 0.0, 1)
        await self._debug_senden("REALTIME_HANDSHAKE_OK", hint="WebRTC verbunden via GPT-Live")
        return antwort_sdp

    def _preise_lesen(self) -> tuple[int, int, int]:
        with SessionLocal() as db:
            provider = db.get(AiProvider, self.v.provider_id)
            if provider is None:
                raise RealtimeSitzungsfehler("REALTIME_NOT_CONFIGURED")
            felder = (*ai_provider_service.REALTIME_PREISFELDER[:2], ai_provider_service.REALTIME_MINUTENPREIS)
            ein, aus, minute = (int(getattr(provider, feld) or 0) for feld in felder)
        return ein, aus, minute

    async def _backend_stufe(self) -> str | None:
        """Die Denkstufe des Backends, geprüft gegen das, was sein Modell kann.

        Der Betreiber wählt aus den Wörtern, die GPT-Live annimmt
        (`sprachwege.OPENAI_LIVE.denkstufen`); welche davon das Backend-Modell
        führt, sagt nur dessen Katalog („Supported values depend on the backend
        model"). Geprüft wird mit derselben Regel wie für die festgelegte Stufe
        eines laufenden Auftrags (`ai_reasoning.eingefroren_pruefen`): was passt,
        geht unverändert hinaus, was nicht passt, wird nach unten geklemmt, und
        schweigt der Katalog, bleibt die Wahl des Betreibers stehen.
        """
        gewuenscht = self.v.reasoning_effort
        if not gewuenscht:
            return None
        try:
            modell = await asyncio.wait_for(
                ai_model_catalog.finde(
                    self.http,
                    self.v.provider_kind,
                    getattr(self.v, "backend_model", ""),
                    schluessel=self.v.api_key,
                ),
                timeout=KATALOGFRIST_SEKUNDEN,
            )
        except Exception as exc:
            voice_debug("LIVE_KATALOG_STUMM", hint=type(exc).__name__)
            modell = None
        _aktiv, stufe = ai_reasoning.eingefroren_pruefen(
            modell, aktiv=gewuenscht != ai_reasoning.AUS_STUFE, stufe=gewuenscht
        )
        if stufe is not None and stufe not in OPENAI_LIVE.denkstufen:
            stufe = None
        if stufe != gewuenscht:
            await self._debug_senden(
                "LIVE_BACKEND_STUFE_ANGEPASST", hint=f"{gewuenscht} -> {stufe or 'keine'}"
            )
        return stufe

    # ── Verbrauch ─────────────────────────────────────────────────────

    def _buchen(self, *, text_input: int = 0, text_output: int = 0, kosten: int, anfragen: int) -> None:
        """Bucht Verbrauch, der schon angefallen ist — notfalls über die Grenze.

        Erst mit Prüfung; lehnt die Grenze ab, wird dieselbe Buchung ohne
        Prüfung nachgeholt und die Ablehnung weitergereicht: die Sitzung endet,
        die Kosten bleiben stehen (`ai_usage_service.realtime_verbrauch_ergaenzen`,
        ``grenzen_pruefen``).
        """
        werte = {
            "event_id": self.v.usage_event_id,
            "text_input": text_input,
            "text_output": text_output,
            "audio_input": 0,
            "audio_output": 0,
            "cost_microunits": kosten,
            "anfragen": anfragen,
        }
        with SessionLocal() as db:
            try:
                ai_usage_service.realtime_verbrauch_ergaenzen(db, **werte)
                db.commit()
                return
            except ai_usage_service.AiQuotaExceeded as exc:
                db.rollback()
                if exc.reason == "realtime_session_limit":
                    raise
                ai_usage_service.realtime_verbrauch_ergaenzen(db, grenzen_pruefen=False, **werte)
                db.commit()
                raise

    def _dauer_buchen(self, sekunden: float, anfragen: int = 0) -> None:
        """Die Dauer der Sitzung, als laufende Summe.

        ``usage.seconds`` ist kumuliert („Do not sum this value across usage
        events"); gebucht wird die Differenz zur letzten Buchung, mindestens die
        15 Sekunden der Anlage. Wie bei den Token rechnet die Summe und nicht
        jede Meldung einzeln — sonst rundete jede kurze Meldung für sich ab.
        """
        with self._buchungsschloss:
            ziel = max(ANLAGE_SEKUNDEN * 1000, round(sekunden * 1000), self._dauer_ms)
            if ziel == self._dauer_ms and not anfragen:
                return
            gesamt = ziel * self._preise[2] // 60_000
            kosten = max(0, gesamt - self._dauer_kosten)
            try:
                self._buchen(kosten=kosten, anfragen=anfragen)
            except ai_usage_service.AiQuotaExceeded as exc:
                if exc.reason != "realtime_session_limit":
                    self._dauer_ms, self._dauer_kosten = ziel, gesamt
                raise
            self._dauer_ms, self._dauer_kosten = ziel, gesamt

    def _backend_buchen(self, antwort: dict) -> None:
        """Die Token einer Backend-Antwort, genau einmal je Antwortkennung.

        „Count each backend response once, using its response ID." Auch eine
        Antwort ohne Nutzungsangabe ist eine Anfrage an den Anbieter gewesen.
        """
        kennung = _kennung(antwort.get("id"))
        with self._buchungsschloss:
            if kennung is None or kennung in self._gebuchte_antworten:
                return
            self._gebuchte_antworten.add(kennung)
            usage = antwort.get("usage") if isinstance(antwort.get("usage"), dict) else {}
            ti = self._tokenzahl(usage, "input_tokens")
            to = self._tokenzahl(usage, "output_tokens")
            summe_ein = self._verbrauch_tokens[0] + ti
            summe_aus = self._verbrauch_tokens[1] + to
            # Zwischengespeicherte Eingabe steckt in ``input_tokens`` und wird
            # wie bei Realtime zum normalen Eingabepreis gebucht.
            gesamt = (summe_ein * self._preise[0] + summe_aus * self._preise[1]) // 1_000_000
            kosten = max(0, gesamt - self._verbrauch_kosten)
            try:
                self._buchen(text_input=ti, text_output=to, kosten=kosten, anfragen=1)
            except ai_usage_service.AiQuotaExceeded as exc:
                if exc.reason != "realtime_session_limit":
                    self._verbrauch_tokens[0], self._verbrauch_tokens[1] = summe_ein, summe_aus
                    self._verbrauch_kosten = gesamt
                raise
            self._verbrauch_tokens[0], self._verbrauch_tokens[1] = summe_ein, summe_aus
            self._verbrauch_kosten = gesamt

    async def _buchen_oder_beenden(self, buchung: Callable, *argumente: object) -> None:
        try:
            await asyncio.to_thread(buchung, *argumente)
        except ai_usage_service.AiQuotaExceeded as exc:
            grund = (
                "realtime_kontingent"
                if exc.reason == "monthly_realtime_cost_limit_cents"
                else "kontingent"
            )
            await self._debug_senden("REALTIME_QUOTA", hint=grund)
            await self._panel_senden({"art": "stoerung", "grund": grund})
            raise RealtimeSitzungsfehler("REALTIME_QUOTA") from exc

    async def _nachbuchen(self, buchung: Callable, *argumente: object) -> None:
        """Buchen, während die Sitzung schon endet — eine Grenze beendet nichts mehr."""
        try:
            await asyncio.to_thread(buchung, *argumente)
        except ai_usage_service.AiQuotaExceeded:
            pass
        except Exception as exc:
            voice_debug("LIVE_USAGE_FAILED", hint=type(exc).__name__)

    # ── Lesen ─────────────────────────────────────────────────────────

    async def _sideband_lesen(self) -> None:
        assert self._sideband is not None
        async for roh in self._sideband:
            ereignis = _lesen(roh)
            if ereignis is not None:
                await self._verarbeiten(ereignis)
            if self._geschlossen:
                return
        # „A connection closing without this event does not confirm successful
        # finalization." Gebucht ist die zuletzt gemeldete Dauer.
        await self._debug_senden("LIVE_CLOSE_UNCONFIRMED", hint="Sideband ohne session.closed beendet")
        raise RealtimeSitzungsfehler("REALTIME_SIDEBAND_CLOSED")

    async def _verarbeiten(self, ereignis: dict) -> None:
        art = ereignis.get("type")
        if art == "session.input_transcript.delta":
            await self._gehoert(ereignis)
        elif art == "session.output_transcript.delta":
            await self._gesagt(ereignis)
        elif art == "session.delegation.created":
            await self._delegiert()
        elif art == "response.event":
            await self._backend_ereignis(ereignis)
        elif art == "session.usage.updated":
            sekunden = _sekunden(ereignis)
            if sekunden is not None:
                await self._buchen_oder_beenden(self._dauer_buchen, sekunden)
        elif art == "session.closed":
            await self._geschlossen_melden(ereignis)
        elif art == "error":
            await self._fehler_melden(ereignis)
        elif art == "info":
            voice_debug("LIVE_INFO", hint=str(ereignis.get("code") or "")[:100])
        # Gespiegeltes Audio (``session.input_audio.append``,
        # ``session.output_audio.delta``) und die Quittungen der Befehle
        # braucht hier niemand: der Ton läuft über WebRTC, und ein Fehler käme
        # als ``error``.

    async def _gehoert(self, ereignis: dict) -> None:
        stueck = ereignis.get("delta")
        if not isinstance(stueck, str) or not stueck:
            return
        self._zuletzt_gehoert = time.monotonic()
        self._bereit_gemeldet = False
        if self._zuletzt_sprach != "ich":
            # Ein Sprecherwechsel beginnt eine neue Zeile. Die Oberfläche
            # ersetzt die letzte eigene Zeile, solange die KI dazwischen nichts
            # sagt — deshalb geht immer das bisher Gehörte als Ganzes hinaus.
            self._zuletzt_sprach = "ich"
            self._eingabe = ""
        self._eingabe = (self._eingabe + stueck)[-MAX_ABSCHRIFT_ZEICHEN:]
        if not self._user_spricht:
            self._user_spricht = True
            self.lage.aeusserungen += 1
            await self._panel_senden({"art": "zustand", "zustand": "hoert"})
        await self._panel_senden({"art": "gehoert", "text": self._eingabe})

    async def _gesagt(self, ereignis: dict) -> None:
        stueck = ereignis.get("delta")
        if not isinstance(stueck, str) or not stueck:
            return
        self._zuletzt_gesagt = time.monotonic()
        self._bereit_gemeldet = False
        self._zuletzt_sprach = "ki"
        if not self._assistant_spricht:
            self._assistant_spricht = True
            await self._panel_senden({"art": "zustand", "zustand": "spricht"})
        await self._panel_senden({"art": "antworttext", "text": stueck})

    async def _delegiert(self) -> None:
        self._response_aktiv = True
        self._bereit_gemeldet = False
        if not self._assistant_spricht:
            await self._panel_senden({"art": "zustand", "zustand": "denkt"})

    async def _backend_ereignis(self, huelle: dict) -> None:
        inner = huelle.get("event")
        if not isinstance(inner, dict):
            return
        delegation = _kennung(huelle.get("delegation_id"))
        art = inner.get("type")
        if art == "response.created":
            kennung = _kennung(_antwort_von(inner).get("id"))
            if kennung is None:
                return
            self._laufende.add(kennung)
            self._aktuelle_antwort[delegation] = kennung
            self._letzte_antwort = kennung
            self._response_aktiv = True
            self._bereit_gemeldet = False
        elif art == "response.output_item.added":
            item = inner.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "function_call"
                and isinstance(item.get("name"), str)
                and _kennung(item.get("call_id"))
            ):
                await self._tool_start(item["call_id"], item["name"])
        elif art == "response.output_item.done":
            item = inner.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                self._aufruf_annehmen(
                    item, self._aktuelle_antwort.get(delegation) or self._letzte_antwort
                )
        elif art in _ANTWORT_ENDEN:
            antwort = _antwort_von(inner)
            await self._buchen_oder_beenden(self._backend_buchen, antwort)
            kennung = _kennung(antwort.get("id"))
            if kennung is not None:
                self._laufende.discard(kennung)
                if self._aufrufe_je_antwort.pop(kennung, 0):
                    self._wartende += 1
            if art == "response.completed":
                self.lage.laeufe += 1
            elif art == "response.failed":
                await self._backend_fehler(inner)
            await self._nach_antwort()
        elif art == "error":
            await self._backend_fehler(inner)

    def _aufruf_annehmen(self, item: dict, antwort: str | None) -> None:
        call_id = item.get("call_id")
        name = item.get("name")
        if (
            not isinstance(call_id, str)
            or not call_id
            or len(call_id) > MAX_CALL_ID_ZEICHEN
            or not isinstance(name, str)
            or not name
        ):
            voice_debug("LIVE_TOOL_CALL_INVALID", hint="call_id oder name fehlt")
            return
        # „If both connections receive a function-call event, execute the
        # function once." Hier gilt dasselbe für einen doppelt gelieferten.
        if call_id in self._aufgerufen:
            return
        self._aufgerufen.add(call_id)
        if antwort is not None and antwort in self._laufende:
            self._aufrufe_je_antwort[antwort] = self._aufrufe_je_antwort.get(antwort, 0) + 1
        elif not self._verwaist:
            # Keine laufende Antwort zu diesem Aufruf gesehen — dann kommt auch
            # kein Ende, auf das zu warten wäre. Er zählt als eine beendete.
            voice_debug("LIVE_TOOL_OHNE_ANTWORT", hint=name)
            self._verwaist = True
            self._wartende += 1
        task = asyncio.create_task(self._tool_ausfuehren({
            "call_id": call_id,
            "name": name,
            "arguments": item.get("arguments", "{}"),
        }))
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_task_fertig)

    async def _tool_folgeantwort_starten(self) -> None:
        """Setzt fort, sobald nichts mehr läuft und jedes Ergebnis abgegeben ist.

        Ein ``response.create`` je wartender Antwort — die Dokumentation nennt
        ihn „continue a delegated response waiting for tool results", einen für
        eine. Geschickt wird erst, wenn keine Antwort mehr läuft: dieselbe
        Anweisung ohne Bezug könnte sonst eine Antwort treffen, deren Aufrufe
        noch gar nicht alle angekommen sind.
        """
        async with self._tool_folgeantwort_schloss:
            if (
                not self._wartende
                or self._tool_tasks
                or self._laufende
                or self._sideband is None
                or self._schliesst
            ):
                return
            anzahl, self._wartende = self._wartende, 0
            self._verwaist = False
            self._tool_folgeantwort_ausstehend = False
            async with self._response_schloss:
                try:
                    for _ in range(anzahl):
                        await self._senden(self._antwort_rahmen())
                    self._response_aktiv = True
                except Exception:
                    self._response_aktiv = False
                    await self._panel_senden({"art": "fehler", "code": "REALTIME_TOOL_DELIVERY_FAILED"})

    async def _nach_antwort(self) -> None:
        """Was geschieht, sobald eine Backend-Antwort endet."""
        if self._laufende or self._tool_tasks:
            return
        if self._wartende:
            await self._tool_folgeantwort_starten()
            return
        self._response_aktiv = False
        nachtrag = self._response_nachtrag_ausstehend
        if nachtrag is None or self._schliesst or self._sideband is None:
            return
        self._response_nachtrag_ausstehend = None
        async with self._response_schloss:
            try:
                self._response_aktiv = True
                await self._senden(nachtrag)
                await self._senden(self._antwort_rahmen())
            except Exception:
                self._response_aktiv = False
                await self._debug_senden("REALTIME_NACHTRAG_FAILED", hint="sideband send failed")

    # ── Fehler und Ende ───────────────────────────────────────────────

    async def _backend_fehler(self, inner: dict) -> None:
        fund = fehler_im_rahmen(inner)
        marke, text = fund if fund is not None else ("AI_PROVIDER_REQUEST_REJECTED", None)
        await self._debug_senden("REALTIME_RESPONSE_FAILED", hint=marke, message=text or "")
        if marke == SICHERHEITSSTOPP:
            await self._sicherheitsstopp()
        if not self._laufende and not self._tool_tasks and not self._wartende:
            # Dieselbe Regel wie in `_fehler_melden`. Ein verschachteltes
            # ``error`` bringt kein Lebensende mit; ohne diese Zeile blieb die
            # Sitzung nach ``session.delegation.created`` für immer
            # beschäftigt — keine Meldung, kein Nachtrag, kein „bereit".
            self._response_aktiv = False
        await self._panel_senden({
            "art": "stoerung",
            "grund": "rate_limit" if marke == "AI_PROVIDER_RATE_LIMITED" else "realtime_response",
            "code": marke,
            "hint": marke,
            "message": text or "",
        })

    async def _fehler_melden(self, ereignis: dict) -> None:
        """Ein ``error`` der Sitzung — ein abgelehnter Befehl oder Moderation.

        Keiner davon beendet die Sitzung von selbst („Others cut off assistant
        audio … and emit an error event without ending the session"); ob sie
        endet, sagt ``session.closed``. Nur der Sicherheitsstopp beendet sie
        hier.
        """
        fund = fehler_im_rahmen(ereignis)
        marke, text = fund if fund is not None else ("AI_PROVIDER_REQUEST_REJECTED", None)
        fehler = ereignis.get("error") if isinstance(ereignis.get("error"), dict) else {}
        code = fehler.get("code") if isinstance(fehler.get("code"), str) else ""
        await self._debug_senden("REALTIME_SIDEBAND_ERROR", hint=(code or marke)[:100], message=text or "")
        if marke == SICHERHEITSSTOPP:
            await self._sicherheitsstopp()
        if not self._laufende and not self._tool_tasks and not self._wartende:
            # Ein abgelehnter Befehl hinterlässt keine laufende Antwort; ohne
            # diese Zeile warteten Nachträge und Meldungen darauf für immer.
            self._response_aktiv = False
        await self._panel_senden({"art": "stoerung", "grund": "realtime_response", "code": "REALTIME_SIDEBAND_ERROR"})

    async def _sicherheitsstopp(self) -> None:
        """OpenAIs Missbrauchsüberwachung hat die Arbeit gestoppt.

        „Stop dispatching further actions for the affected conversation. Do not
        automatically retry the blocked workflow." Die Sitzung endet, und der
        Mensch sieht warum — dieselbe Marke wie im Chat
        (`openai_compatible_adapter.SICHERHEITSSTOPP`).

        Der Riegel fällt **hier** und nicht erst im Abbau: ein Werkzeug, das
        gerade fertig wird, lieferte sonst noch sein Ergebnis samt Fortsetzung
        ab. Laufende Werkzeuge werden abgebrochen; eines, das schon in seinem
        Thread arbeitet, lässt sich nicht mehr zurückholen, sein Ergebnis geht
        aber nicht mehr hinaus.
        """
        self._schliesst = True
        for task in self._tool_tasks:
            task.cancel()
        await self._debug_senden("LIVE_SAFETY_STOPPED", hint=SICHERHEITSSTOPP)
        raise RealtimeSitzungsfehler(SICHERHEITSSTOPP)

    async def _geschlossen_melden(self, ereignis: dict) -> None:
        self._geschlossen = True
        grund = ereignis.get("reason") if isinstance(ereignis.get("reason"), str) else ""
        sekunden = _sekunden(ereignis)
        if sekunden is not None:
            await self._nachbuchen(self._dauer_buchen, sekunden)
        voice_debug("LIVE_CLOSED", hint=grund)
        if self._schliesst:
            return
        if grund == "expired":
            self.lage.abgelaufen = True
            await self._panel_senden({"art": "abgelaufen"})
        elif grund == "content":
            await self._panel_senden({"art": "fehler", "code": "REALTIME_CONTENT_STOPPED"})
        elif grund == "connection_lost":
            await self._panel_senden({"art": "fehler", "code": "REALTIME_CONNECTION_LOST"})

    async def _ordentlich_schliessen(self) -> None:
        """``session.close`` senden und auf ``session.closed`` warten.

        So steht es unter „Usage and graceful close": erst das Ende anmelden,
        dann weiterlesen, bis die Sitzung ihre Enddauer meldet. Laufende
        Werkzeuge hat `fuehren` zu diesem Zeitpunkt schon abgebrochen; eine
        Antwort, die auf ihr Ergebnis wartet, setzt OpenAI nach dem Schliessen
        ohnehin nicht mehr fort („one waiting for a function result cannot
        continue after closing starts").
        """
        if self._geschlossen or self._sideband is None or self._sitzungs_id is None:
            return
        self._schliesst = True
        await self._senden(self._befehl("session.close"))
        try:
            await asyncio.wait_for(self._bis_geschlossen(), timeout=SCHLIESSFRIST_SEKUNDEN)
        except TimeoutError:
            voice_debug("LIVE_CLOSE_UNCONFIRMED", hint="kein session.closed in der Frist")

    async def _bis_geschlossen(self) -> None:
        """Liest nach ``session.close`` nur noch, was gebucht werden muss."""
        assert self._sideband is not None
        async for roh in self._sideband:
            ereignis = _lesen(roh)
            if ereignis is None:
                continue
            art = ereignis.get("type")
            if art == "session.usage.updated":
                sekunden = _sekunden(ereignis)
                if sekunden is not None:
                    await self._nachbuchen(self._dauer_buchen, sekunden)
            elif art == "response.event":
                inner = ereignis.get("event")
                if isinstance(inner, dict) and inner.get("type") in _ANTWORT_ENDEN:
                    await self._nachbuchen(self._backend_buchen, _antwort_von(inner))
            elif art == "session.closed":
                await self._geschlossen_melden(ereignis)
                return
        voice_debug("LIVE_CLOSE_UNCONFIRMED", hint="Sideband vor session.closed zu")

    # ── Nebenläufe ────────────────────────────────────────────────────

    def _nebenlaeufe(self) -> list:
        return [*super()._nebenlaeufe(), self._stille_beobachten()]

    async def _stille_beobachten(self) -> None:
        """Wann Mensch und Stimme schweigen — für Anzeige und Nachträge.

        GPT-Live meldet keinen Anfang und kein Ende einer Äusserung, nur
        Abschriftstücke. Wer eine Weile keines geschickt hat, gilt hier als
        still. Daran hängt nur, was die Oberfläche zeigt und wann ein Nachtrag
        oder eine Meldung eingereicht wird — beides verträgt einen Irrtum.
        """
        while True:
            await asyncio.sleep(0.25)
            jetzt = time.monotonic()
            if self._user_spricht and jetzt - self._zuletzt_gehoert > STILLE_SEKUNDEN:
                self._user_spricht = False
            if self._assistant_spricht and jetzt - self._zuletzt_gesagt > STILLE_SEKUNDEN:
                self._assistant_spricht = False
            if (
                not self._bereit_gemeldet
                and not self._user_spricht
                and not self._assistant_spricht
                and not self._response_aktiv
                and not self._tool_tasks
            ):
                self._bereit_gemeldet = True
                await self._panel_senden({"art": "zustand", "zustand": "bereit"})
