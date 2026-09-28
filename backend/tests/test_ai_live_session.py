"""GPT-Live: der Vertrag mit OpenAI, Zug um Zug.

Jede Erwartung hier ist aus OpenAIs Dokumentation zu GPT-Live abgeleitet
(gelesen am 22.09.2026; die Seiten stehen im Kopf von
`services/ai_voice/live_session.py`) und nicht aus dem Code, der sie erfüllt.
Wo ein Test eine Stelle der Dokumentation prüft, steht sie wörtlich dabei.

Die Gegenstelle ist eine Attrappe: ein Sideband, das Ereignisse in der Form
liefert, die die Referenz beschreibt, und festhält, was die Sitzung schickt.
Ein Lauf gegen den echten Endpunkt braucht einen OpenAI-Schlüssel und steht
deshalb nicht hier.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from models import AiUsageEvent
from services import ai_limit_service, ai_provider_service, ai_usage_service
from services.ai_provider_registry import Modell
from services.ai_voice import live_session, realtime_session
from services.ai_voice.realtime_session import RealtimeSitzungsfehler
from services.openai_compatible_adapter import SICHERHEITSSTOPP

# Zusammengesetzt, nie als Literal: das Repo ist öffentlich.
SCHLUESSEL = "sk-" + "test-live-not-real"
#: 0,05 USD je Minute — der Preis auf OpenAIs Modellseite zu gpt-live-1
#: („Voice sessions cost $0.05 per minute, billed per second").
MINUTENPREIS = 50_000
TEXTPREIS_EIN = 1_000_000
TEXTPREIS_AUS = 8_000_000
#: 15 Sekunden zum Minutenpreis — was die Anlage kostet.
ANLAGE_KOSTEN = 15_000 * MINUTENPREIS // 60_000
ANGEBOT = "v=0\r\nlive-offer"
ANTWORT_SDP = "v=0\r\nlive-answer"


# ── Aufbau ────────────────────────────────────────────────────────────────


def _live_zugang(db: Session, **abweichend):
    werte = dict(
        name=f"GPT-Live {uuid4().hex[:8]}",
        provider_kind="openai",
        default_model="gpt-6-luna",
        enabled=True,
        requires_api_key=True,
        operator_api_key=SCHLUESSEL,
        realtime_default=True,
        realtime_model="gpt-live-1",
        realtime_voice="marin",
        realtime_text_input_price_micro_usd_per_million=TEXTPREIS_EIN,
        realtime_text_output_price_micro_usd_per_million=TEXTPREIS_AUS,
        realtime_minute_price_micro_usd=MINUTENPREIS,
    )
    werte.update(abweichend)
    provider = ai_provider_service.create_provider(db, **werte)
    db.commit()
    db.refresh(provider)
    return provider


def _vorbereitung(db: Session, user, **abweichend) -> live_session.LiveVorbereitung:
    provider = _live_zugang(db)
    event = ai_usage_service.reserve_ai_usage(
        db,
        user,
        request_id=uuid4(),
        estimated_tokens=0,
        provider_id=provider.id,
        model="gpt-live-1",
        realtime=True,
    )
    db.commit()
    werte = dict(
        provider_id=provider.id,
        provider_kind="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-live-1",
        voice="marin",
        reasoning_effort=None,
        language="de",
        api_key=SCHLUESSEL,
        instructions="Die Stimme",
        tools=[{
            "type": "function",
            "name": "web_search",
            "description": "Sucht im Netz. Liefert Treffer.",
            "parameters": {"type": "object"},
        }],
        conversation_id="conversation-1",
        usage_event_id=event.id,
        backend_model="gpt-6-luna",
        backend_instructions="Das Backend",
    )
    werte.update(abweichend)
    return live_session.LiveVorbereitung(**werte)


class Panel:
    """Der Browser auf der anderen Seite des Panel-Sockets."""

    def __init__(self, *eingang: dict) -> None:
        self.gesendet: list[dict] = []
        self._eingang: asyncio.Queue = asyncio.Queue()
        for nachricht in eingang:
            self._eingang.put_nowait(nachricht)

    async def send_json(self, daten: dict) -> None:
        self.gesendet.append(daten)

    async def receive_json(self) -> dict:
        return await self._eingang.get()

    def sagen(self, nachricht: dict) -> None:
        self._eingang.put_nowait(nachricht)

    def ohne_debug(self) -> list[dict]:
        return [rahmen for rahmen in self.gesendet if rahmen.get("art") != "debug"]


_ENDE = object()


class Sideband:
    """OpenAIs Sideband: liefert Ereignisse, hält fest, was ankommt."""

    def __init__(self, *, beim_schliessen: tuple[dict, ...] = ()) -> None:
        self.gesendet: list[dict] = []
        self._eingang: asyncio.Queue = asyncio.Queue()
        self._beim_schliessen = beim_schliessen
        self.bei_fortsetzung = None
        self.geschlossen = False

    def liefern(self, *ereignisse: object) -> None:
        for ereignis in ereignisse:
            self._eingang.put_nowait(json.dumps(ereignis) if isinstance(ereignis, dict) else ereignis)

    def enden(self) -> None:
        self._eingang.put_nowait(_ENDE)

    async def send(self, roh: str) -> None:
        rahmen = json.loads(roh)
        self.gesendet.append(rahmen)
        if rahmen["type"] == "session.close":
            self.liefern(*self._beim_schliessen)
        if rahmen["type"] == "response.create" and self.bei_fortsetzung is not None:
            self.bei_fortsetzung()

    def arten(self) -> list[str]:
        return [rahmen["type"] for rahmen in self.gesendet]

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        roh = await self._eingang.get()
        if roh is _ENDE:
            raise StopAsyncIteration
        return roh

    async def close(self) -> None:
        self.geschlossen = True


class Http:
    """``POST /v1/live/sessions`` — antwortet wie in der Referenz."""

    def __init__(self, antwort: httpx.Response | None = None) -> None:
        self.anfragen: list[tuple[str, dict]] = []
        self.antwort = antwort or httpx.Response(201, json={
            "session": {"id": "live_123"},
            "transport": {"type": "webrtc", "sdp": ANTWORT_SDP},
        })

    async def post(self, url: str, **kwargs) -> httpx.Response:
        self.anfragen.append((url, kwargs))
        return self.antwort


def _sitzung(vorbereitung, *, panel=None, http=None) -> live_session.LiveSitzung:
    return live_session.LiveSitzung(
        panel or Panel(),
        vorbereitung=vorbereitung,
        user_id=7,
        http_client=http or Http(),
        herkunft="panel",
        familie=None,
    )


def _verbindung(monkeypatch, sideband: Sideband | Exception) -> list[tuple[str, dict]]:
    aufrufe: list[tuple[str, dict]] = []

    async def connect(url, **kwargs):
        aufrufe.append((url, kwargs))
        if isinstance(sideband, Exception):
            raise sideband
        return sideband

    monkeypatch.setattr(live_session.websockets, "connect", connect)
    return aufrufe


def _werkzeuge(monkeypatch, sperre: threading.Event | None = None) -> list[str]:
    """Führt jedes Werkzeug aus, auf Wunsch erst nach Freigabe durch den Test."""
    ausgefuehrt: list[str] = []

    def ausfuehren(_user_id, call, **_kwargs):
        if sperre is not None:
            sperre.wait(5)
        ausgefuehrt.append(call.id)
        return {"ok": True}, None, {"tool_name": call.name}, []

    monkeypatch.setattr(realtime_session, "voice_werkzeug_ausfuehren", ausfuehren)
    return ausgefuehrt


def _zeile(db: Session, event_id: int) -> AiUsageEvent:
    db.expire_all()
    return db.get(AiUsageEvent, event_id)


# Die Ereignisse, wie die Referenz sie beschreibt.

def _backend(inneres: dict, delegation: str | None = "item_1") -> dict:
    return {"type": "response.event", "event_id": "event_x", "delegation_id": delegation, "event": inneres}


def _begonnen(antwort: str, delegation: str | None = "item_1") -> dict:
    return _backend({"type": "response.created", "response": {"id": antwort, "status": "in_progress", "output": []}}, delegation)


def _aufruf(call_id: str, *, art: str = "response.output_item.done", delegation: str | None = "item_1") -> dict:
    return _backend({
        "type": art,
        "item": {
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": "web_search",
            "arguments": '{"query":"Berlin"}',
        },
    }, delegation)


def _fertig(antwort: str, *, ein: int = 0, aus: int = 0, art: str = "response.completed") -> dict:
    # „The forwarded lifecycle events, including response.completed, contain
    # response.output: [] even when function calls need results."
    return _backend({
        "type": art,
        "response": {
            "id": antwort,
            "status": "completed",
            "output": [],
            "tools": [],
            "instructions": None,
            "usage": {"input_tokens": ein, "output_tokens": aus},
        },
    })


def _dauer(sekunden: object) -> dict:
    return {"type": "session.usage.updated", "usage": {"seconds": sekunden, "context_window": {}}}


def _geschlossen(grund: str, sekunden: float) -> dict:
    return {
        "type": "session.closed",
        "reason": grund,
        "session": {"id": "live_123"},
        "usage": {"seconds": sekunden},
    }


async def _werkzeuge_abwarten(sitzung: live_session.LiveSitzung) -> None:
    if sitzung._tool_tasks:
        await asyncio.gather(*tuple(sitzung._tool_tasks), return_exceptions=True)
    # Die Fortsetzung startet der Rückruf der Task als eigene Task.
    for _ in range(3):
        await asyncio.sleep(0)


# ── Anlage ────────────────────────────────────────────────────────────────


def test_die_sitzung_schickt_genau_den_dokumentierten_vertrag(db: Session, owner_user) -> None:
    config = live_session._session_config(_vorbereitung(db, owner_user), "medium")

    assert config["model"] == "gpt-live-1"
    assert config["instructions"] == "Die Stimme"
    # „For WebRTC, omit audio.format from session configuration."
    assert config["audio"] == {"output": {"voice": "marin"}}
    responses = config["delegation"]["responses"]
    assert config["delegation"]["type"] == "responses"
    assert responses["model"] == "gpt-6-luna"
    assert responses["instructions"] == "Das Backend"
    assert responses["tools"][0]["name"] == "web_search"
    assert responses["tool_choice"] == "auto"
    assert responses["parallel_tool_calls"] is True
    assert responses["reasoning"] == {"effort": "medium"}
    # Der Browser bekommt nichts und darf nichts: ohne diese Liste sähe er mit
    # `session.started` die Anweisungen und Werkzeuge des Backends, und ein
    # `session.update` von dort tauschte sie aus.
    assert config["client"] == {"data_channel": {"allowed_client_events": [], "allowed_server_events": []}}
    assert config["store"] is False
    # Ohne Stufe kein Feld — dann gilt die Vorgabe des Backend-Modells.
    assert "reasoning" not in live_session._session_config(_vorbereitung(db, owner_user), None)["delegation"]["responses"]


@pytest.mark.asyncio
async def test_anlage_und_sideband_wie_in_der_referenz(db: Session, owner_user, monkeypatch) -> None:
    vorbereitung = _vorbereitung(db, owner_user)
    http = Http()
    sideband = Sideband()
    verbindungen = _verbindung(monkeypatch, sideband)

    antwort = await _sitzung(vorbereitung, http=http)._handshake(ANGEBOT)

    assert antwort == ANTWORT_SDP
    url, anfrage = http.anfragen[0]
    assert url == "https://api.openai.com/v1/live/sessions"
    assert anfrage["headers"] == {"Authorization": f"Bearer {SCHLUESSEL}"}
    assert anfrage["json"]["transport"] == {"type": "webrtc", "sdp": ANGEBOT}
    assert anfrage["json"]["session"]["model"] == "gpt-live-1"
    # „wss://api.openai.com/v1/live/sessions/{session_id}/attach", derselbe
    # Schlüssel im Kopf und keiner in der Adresse.
    assert verbindungen[0][0] == "wss://api.openai.com/v1/live/sessions/live_123/attach"
    assert verbindungen[0][1]["additional_headers"] == {"Authorization": f"Bearer {SCHLUESSEL}"}
    # „No session.start event is required for WebRTC" — und das Sideband
    # braucht keine erste Nachricht.
    assert sideband.gesendet == []
    # „bills 15 seconds of voice duration while the session initializes" —
    # gebucht als eine Anfrage, damit das Ende sie nicht auf null setzt.
    zeile = _zeile(db, vorbereitung.usage_event_id)
    assert zeile.accounted_cost_microunits == ANLAGE_KOSTEN == 12_500
    assert zeile.provider_requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kennung", ["../live_123", "live/123", "", ".live", "live 123", "x" * 300, 42])
async def test_eine_sitzungskennung_die_kein_pfadsegment_ist_endet_die_anlage(
    db: Session, owner_user, monkeypatch, kennung
) -> None:
    # „Treat the session ID as opaque and preserve it unchanged" — unverändert,
    # aber sie landet in einem Pfad. Was dort etwas bedeutete, kommt nicht an.
    verbindungen = _verbindung(monkeypatch, Sideband())
    http = Http(httpx.Response(201, json={"session": {"id": kennung}, "transport": {"type": "webrtc", "sdp": ANTWORT_SDP}}))

    with pytest.raises(RealtimeSitzungsfehler, match="REALTIME_HANDSHAKE_FAILED"):
        await _sitzung(_vorbereitung(db, owner_user), http=http)._handshake(ANGEBOT)
    assert verbindungen == []


@pytest.mark.asyncio
async def test_eine_abgelehnte_anlage_meldet_nur_ihr_codewort(db: Session, owner_user, monkeypatch) -> None:
    verbindungen = _verbindung(monkeypatch, Sideband())
    panel = Panel()
    vorbereitung = _vorbereitung(db, owner_user)
    http = Http(httpx.Response(400, json={"error": {
        "code": "unsupported_model", "message": "Fremdtext mit <b>Markup</b>", "type": "invalid_request_error",
    }}))

    with pytest.raises(RealtimeSitzungsfehler, match="REALTIME_HANDSHAKE_FAILED"):
        await _sitzung(vorbereitung, panel=panel, http=http)._handshake(ANGEBOT)

    assert verbindungen == []
    assert {"art": "debug", "code": "REALTIME_HANDSHAKE_FAILED", "hint": "HTTP 400 unsupported_model"} in panel.gesendet
    assert "Markup" not in json.dumps(panel.gesendet)
    # Nichts angelegt, nichts gebucht.
    zeile = _zeile(db, vorbereitung.usage_event_id)
    assert not zeile.provider_requests
    assert not zeile.accounted_cost_microunits


@pytest.mark.asyncio
async def test_scheitert_das_sideband_bleibt_die_anlage_gebucht(db: Session, owner_user, monkeypatch) -> None:
    _verbindung(monkeypatch, OSError("verweigert"))
    vorbereitung = _vorbereitung(db, owner_user)

    with pytest.raises(RealtimeSitzungsfehler, match="REALTIME_HANDSHAKE_FAILED"):
        await _sitzung(vorbereitung)._handshake(ANGEBOT)

    zeile = _zeile(db, vorbereitung.usage_event_id)
    assert zeile.accounted_cost_microunits == ANLAGE_KOSTEN
    assert zeile.provider_requests == 1



@pytest.mark.asyncio
async def test_eine_angelegte_sitzung_mit_unlesbarer_antwort_bleibt_gebucht(
    db: Session, owner_user
) -> None:
    """2xx heisst angelegt — und angelegt heisst berechnet, auch ohne lesbare Antwort.

    Bis zum 23.09.2026 endete eine Anlage, deren Antwort sich nicht lesen
    liess, ohne Buchung. Die Sitzung stand bei OpenAI trotzdem, und ihre
    15 Sekunden auch (`live_session.ANLAGE_SEKUNDEN`).
    """
    vorbereitung = _vorbereitung(db, owner_user)
    sitzung = _sitzung(vorbereitung, http=Http(httpx.Response(201, text="kein json")))

    with pytest.raises(RealtimeSitzungsfehler, match="REALTIME_HANDSHAKE_FAILED"):
        await sitzung._handshake(ANGEBOT)

    zeile = _zeile(db, vorbereitung.usage_event_id)
    assert zeile.accounted_cost_microunits == ANLAGE_KOSTEN
    assert zeile.provider_requests == 1


def test_zwei_gleichzeitige_dauerbuchungen_buchen_die_differenz_einmal(
    db: Session, owner_user, monkeypatch
) -> None:
    """Zwei Buchungen aus zwei Threads — dieselbe Differenz geht einmal hinaus.

    Gebucht wird in `asyncio.to_thread`. Wird der Leser mitten in einer
    Buchung abgebrochen, läuft sein Thread weiter, und der Abbau bucht daneben
    die Enddauer. Beide lasen den alten Stand und buchten dieselbe Differenz.
    """
    sitzung = _sitzung(_vorbereitung(db, owner_user))
    sitzung._preise = (0, 0, MINUTENPREIS)
    gebucht: list[int] = []

    def buchen(*, kosten: int, anfragen: int, **_werte) -> None:
        time.sleep(0.05)
        gebucht.append(kosten)

    monkeypatch.setattr(sitzung, "_buchen", buchen)
    faeden = [threading.Thread(target=sitzung._dauer_buchen, args=(60.0,)) for _ in range(2)]
    for faden in faeden:
        faden.start()
    for faden in faeden:
        faden.join()

    assert sum(gebucht) == 60_000 * MINUTENPREIS // 60_000


# ── Denkstufe des Backends ────────────────────────────────────────────────


def _katalog(monkeypatch, modell: Modell | None | Exception) -> None:
    async def finde(_client, _kind, _model_id, *, schluessel=None):
        if isinstance(modell, Exception):
            raise modell
        return modell

    monkeypatch.setattr(live_session.ai_model_catalog, "finde", finde)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("modell", "gewuenscht", "gesendet"),
    [
        # „Supported values depend on the backend model": was es nicht führt,
        # wird nach unten geklemmt, statt als 400 zurückzukommen.
        (Modell("gpt-6-luna", "GPT-6 Luna", True, ("none", "low", "medium", "high")), "xhigh", "high"),
        (Modell("gpt-6-luna", "GPT-6 Luna", True, ("none", "low", "medium", "high", "xhigh")), "xhigh", "xhigh"),
        (Modell("gpt-6-luna", "GPT-6 Luna", True, ("none", "low", "medium", "high")), "none", "none"),
        # Denkt das Modell nicht — oder weiss der Katalog es nicht —, ist jede
        # Stufe ein 400, auch `none`.
        (Modell("gpt-4.1", "GPT-4.1", False), "medium", None),
        (Modell("gpt-6-luna", "GPT-6 Luna", None), "medium", None),
        # Schweigt der Katalog, bleibt die Wahl des Betreibers.
        (None, "xhigh", "xhigh"),
        (TimeoutError(), "xhigh", "xhigh"),
    ],
)
async def test_die_denkstufe_gehoert_dem_backend_modell(
    db: Session, owner_user, monkeypatch, modell, gewuenscht, gesendet
) -> None:
    _katalog(monkeypatch, modell)
    _verbindung(monkeypatch, Sideband())
    http = Http()

    await _sitzung(_vorbereitung(db, owner_user, reasoning_effort=gewuenscht), http=http)._handshake(ANGEBOT)

    responses = http.anfragen[0][1]["json"]["session"]["delegation"]["responses"]
    assert responses.get("reasoning") == ({"effort": gesendet} if gesendet else None)


# ── Werkzeuge über die Delegation ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("werkzeug_zuerst", [True, False])
async def test_ein_aufruf_bekommt_ein_ergebnis_und_danach_eine_fortsetzung(
    db: Session, owner_user, monkeypatch, werkzeug_zuerst
) -> None:
    """„Send a response.item.create result for every pending function call,
    then send response.create to continue the backend response."

    In beiden Reihenfolgen: das Werkzeug ist fertig, bevor die Antwort endet,
    oder die Antwort endet (mit ``output: []``), während es noch läuft.
    """
    sperre = threading.Event()
    if werkzeug_zuerst:
        sperre.set()
    ausgefuehrt = _werkzeuge(monkeypatch, sperre)
    sitzung = _sitzung(_vorbereitung(db, owner_user))
    sideband = Sideband()
    sitzung._sideband = sideband

    await sitzung._verarbeiten({"type": "session.delegation.created", "delegation": {
        "id": "item_1", "target": "responses", "type": "function_call", "response_id": "resp_1",
    }, "offset_ms": 1200})
    await sitzung._verarbeiten(_begonnen("resp_1"))
    # „an arguments-done event alone is not sufficient" — erst `done` zählt.
    await sitzung._verarbeiten(_aufruf("call_1", art="response.output_item.added"))
    assert ausgefuehrt == []
    await sitzung._verarbeiten(_aufruf("call_1"))
    if werkzeug_zuerst:
        await _werkzeuge_abwarten(sitzung)
        # Das Ergebnis ist da, die Antwort läuft noch: fortgesetzt wird nicht.
        assert sideband.arten() == ["response.item.create"]
        await sitzung._verarbeiten(_fertig("resp_1", ein=1_000, aus=200))
    else:
        await sitzung._verarbeiten(_fertig("resp_1", ein=1_000, aus=200))
        # Die Antwort ist zu Ende, ihr Werkzeug läuft noch: fortgesetzt wird
        # erst mit seinem Ergebnis.
        assert sideband.arten() == []
        sperre.set()
        await _werkzeuge_abwarten(sitzung)

    assert ausgefuehrt == ["call_1"]
    assert sideband.arten() == ["response.item.create", "response.create"]
    ergebnis = sideband.gesendet[0]["item"]
    assert ergebnis["type"] == "function_call_output"
    assert ergebnis["call_id"] == "call_1"
    # „output" ist eine Zeichenkette — mit derselben Untrusted-Hülle wie im Chat.
    assert json.loads(ergebnis["output"]) == {"untrusted": True, "tool": "web_search", "data": {"ok": True}}
    # Jeder Befehl trägt eine eigene `event_id` — so lässt sich ein `error`
    # über `client_event_id` zuordnen.
    kennungen = [rahmen["event_id"] for rahmen in sideband.gesendet]
    assert len(set(kennungen)) == len(kennungen)


@pytest.mark.asyncio
async def test_ein_doppelt_gelieferter_aufruf_laeuft_einmal(db: Session, owner_user, monkeypatch) -> None:
    # „If both connections receive a function-call event, execute the function once."
    ausgefuehrt = _werkzeuge(monkeypatch)
    sitzung = _sitzung(_vorbereitung(db, owner_user))
    sitzung._sideband = Sideband()

    await sitzung._verarbeiten(_begonnen("resp_1"))
    await sitzung._verarbeiten(_aufruf("call_1"))
    await sitzung._verarbeiten(_aufruf("call_1"))
    await sitzung._verarbeiten(_fertig("resp_1"))
    await _werkzeuge_abwarten(sitzung)

    assert ausgefuehrt == ["call_1"]
    assert sitzung._sideband.arten() == ["response.item.create", "response.create"]


@pytest.mark.asyncio
async def test_parallele_aufrufe_einer_antwort_eine_fortsetzung(db: Session, owner_user, monkeypatch) -> None:
    ausgefuehrt = _werkzeuge(monkeypatch)
    sitzung = _sitzung(_vorbereitung(db, owner_user))
    sitzung._sideband = Sideband()

    await sitzung._verarbeiten(_begonnen("resp_1"))
    await sitzung._verarbeiten(_aufruf("call_1"))
    await sitzung._verarbeiten(_aufruf("call_2"))
    await sitzung._verarbeiten(_fertig("resp_1"))
    await _werkzeuge_abwarten(sitzung)

    assert sorted(ausgefuehrt) == ["call_1", "call_2"]
    assert sitzung._sideband.arten().count("response.item.create") == 2
    # Erst alle Ergebnisse, dann die eine Fortsetzung.
    assert sitzung._sideband.arten()[-1] == "response.create"
    assert sitzung._sideband.arten().count("response.create") == 1


@pytest.mark.asyncio
async def test_solange_eine_andere_antwort_laeuft_wird_nicht_fortgesetzt(
    db: Session, owner_user, monkeypatch
) -> None:
    # Ein `response.create` hat keinen Bezug. Schickte die Sitzung ihn,
    # während eine zweite Antwort noch Aufrufe liefern kann, träfe er eine
    # Antwort, deren Ergebnisse noch gar nicht alle da sind.
    sperre = threading.Event()
    _werkzeuge(monkeypatch, sperre)
    sitzung = _sitzung(_vorbereitung(db, owner_user))
    sitzung._sideband = Sideband()

    await sitzung._verarbeiten(_begonnen("resp_1", "item_1"))
    await sitzung._verarbeiten(_begonnen("resp_2", "item_2"))
    await sitzung._verarbeiten(_aufruf("call_1", delegation="item_1"))
    await sitzung._verarbeiten(_fertig("resp_1"))
    # Das Ergebnis kommt, nachdem `resp_1` geendet hat — dann wäre alles für
    # die Fortsetzung da, gäbe es `resp_2` nicht.
    sperre.set()
    await _werkzeuge_abwarten(sitzung)
    assert sitzung._sideband.arten() == ["response.item.create"]

    await sitzung._verarbeiten(_fertig("resp_2"))
    assert sitzung._sideband.arten() == ["response.item.create", "response.create"]


# ── Abrechnung ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_die_dauer_ist_eine_laufende_summe(db: Session, owner_user, monkeypatch) -> None:
    """„Use the latest usage.seconds as the running total … updates of 12 and
    then 15 seconds mean 15 seconds of use." Und die 15 Sekunden der Anlage
    werden angerechnet, nicht aufgeschlagen."""
    _verbindung(monkeypatch, Sideband())
    vorbereitung = _vorbereitung(db, owner_user)
    sitzung = _sitzung(vorbereitung)
    await sitzung._handshake(ANGEBOT)

    for sekunden in (12, 15, 30, 30, 29, True, -1, "40", float("nan"), 10**9):
        await sitzung._verarbeiten(_dauer(sekunden))
    zeile = _zeile(db, vorbereitung.usage_event_id)
    assert zeile.accounted_cost_microunits == 30_000 * MINUTENPREIS // 60_000 == 25_000

    await sitzung._verarbeiten(_geschlossen("remote_hangup", 45.5))
    zeile = _zeile(db, vorbereitung.usage_event_id)
    # Sekundengenau, nicht auf die Minute gerundet („not rounded up to the
    # next whole minute").
    assert zeile.accounted_cost_microunits == 45_500 * MINUTENPREIS // 60_000
    # Die Dauer ist keine weitere Anfrage — gezählt hat nur die Anlage.
    assert zeile.provider_requests == 1


@pytest.mark.asyncio
async def test_backend_token_zaehlen_je_antwort_genau_einmal(db: Session, owner_user) -> None:
    # „Count each backend response once, using its response ID."
    vorbereitung = _vorbereitung(db, owner_user)
    sitzung = _sitzung(vorbereitung)
    sitzung._preise = (TEXTPREIS_EIN, TEXTPREIS_AUS, MINUTENPREIS)
    sitzung._sideband = Sideband()

    await sitzung._verarbeiten(_begonnen("resp_1"))
    await sitzung._verarbeiten(_fertig("resp_1", ein=1_000, aus=200))
    await sitzung._verarbeiten(_fertig("resp_1", ein=1_000, aus=200))
    await sitzung._verarbeiten(_begonnen("resp_2"))
    # Auch eine abgebrochene Antwort hat gekostet.
    await sitzung._verarbeiten(_fertig("resp_2", ein=500, aus=0, art="response.incomplete"))

    zeile = _zeile(db, vorbereitung.usage_event_id)
    assert zeile.realtime_text_input_tokens == 1_500
    assert zeile.realtime_text_output_tokens == 200
    assert zeile.realtime_audio_input_tokens == 0
    assert zeile.accounted_cost_microunits == (1_500 * TEXTPREIS_EIN + 200 * TEXTPREIS_AUS) // 1_000_000
    assert zeile.provider_requests == 2


@pytest.mark.asyncio
async def test_eine_grenze_beendet_die_sitzung_ohne_die_kosten_zu_verschweigen(
    db: Session, owner_user, monkeypatch
) -> None:
    # Ein Cent Realtime-Budget im Monat; die Anlage allein kostet 1,25 Cent.
    monkeypatch.setattr(
        ai_usage_service,
        "resolve_effective_limits",
        lambda _db, _user: replace(ai_limit_service.UNLIMITED_AI_LIMITS, monthly_realtime_cost_limit_cents=1),
    )
    _verbindung(monkeypatch, Sideband())
    panel = Panel()
    vorbereitung = _vorbereitung(db, owner_user)

    with pytest.raises(RealtimeSitzungsfehler, match="REALTIME_QUOTA"):
        await _sitzung(vorbereitung, panel=panel)._handshake(ANGEBOT)

    assert {"art": "stoerung", "grund": "realtime_kontingent"} in panel.gesendet
    # Angefallen ist die Anlage trotzdem. Stünde sie nicht in der Zeile, begänne
    # der nächste Anlauf wieder unter der Grenze.
    zeile = _zeile(db, vorbereitung.usage_event_id)
    assert zeile.accounted_cost_microunits == ANLAGE_KOSTEN
    assert zeile.provider_requests == 1


# ── Gespräch ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abschriften_werden_zu_zeilen_und_gespiegeltes_audio_zu_nichts(
    db: Session, owner_user
) -> None:
    panel = Panel()
    sitzung = _sitzung(_vorbereitung(db, owner_user), panel=panel)
    sitzung._sideband = Sideband()

    # Das Sideband bekommt Ein- und Ausgabeaudio gespiegelt; der Ton läuft
    # über WebRTC, hier hat er nichts zu suchen.
    await sitzung._verarbeiten({"type": "session.input_audio.append", "audio": "AAAA"})
    await sitzung._verarbeiten({"type": "session.output_audio.delta", "delta": "AAAA"})
    assert panel.gesendet == []
    assert sitzung._sideband.gesendet == []

    for stueck in ("Wie viel ", "Speicher hat Valheim?"):
        await sitzung._verarbeiten({"type": "session.input_transcript.delta", "delta": stueck, "start_ms": 0, "end_ms": 900})
    await sitzung._verarbeiten({"type": "session.output_transcript.delta", "delta": "Gut zwei Gigabyte.", "start_ms": 1000, "end_ms": 1800})

    assert panel.gesendet == [
        {"art": "zustand", "zustand": "hoert"},
        {"art": "gehoert", "text": "Wie viel "},
        {"art": "gehoert", "text": "Wie viel Speicher hat Valheim?"},
        {"art": "zustand", "zustand": "spricht"},
        {"art": "antworttext", "text": "Gut zwei Gigabyte."},
    ]
    assert sitzung.lage.aeusserungen == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fehlerereignis",
    [
        {"type": "error", "error": {"type": "invalid_request_error", "code": "misalignment_policy_violation", "message": "…"}},
        _backend({"type": "response.failed", "response": {"id": "resp_1", "status": "failed", "error": {"code": "misalignment_policy_violation", "message": "…"}}}),
    ],
)
async def test_ein_sicherheitsstopp_beendet_die_sitzung_ohne_wiederholung(
    db: Session, owner_user, monkeypatch, fehlerereignis
) -> None:
    """„Stop dispatching further actions for the affected conversation. Do not
    automatically retry the blocked workflow." Ein Aufruf, der danach noch
    ankommt, läuft nicht mehr."""
    ausgefuehrt = _werkzeuge(monkeypatch)
    panel = Panel({"art": "webrtc_offer", "sdp": ANGEBOT})
    sideband = Sideband(beim_schliessen=(_geschlossen("close_requested", 20),))
    _verbindung(monkeypatch, sideband)
    sitzung = _sitzung(_vorbereitung(db, owner_user), panel=panel)
    sideband.liefern(_begonnen("resp_1"), fehlerereignis, _aufruf("call_1"), _fertig("resp_1"))

    await asyncio.wait_for(sitzung.fuehren(), timeout=10)

    assert ausgefuehrt == []
    assert {"art": "fehler", "code": SICHERHEITSSTOPP} in panel.gesendet
    assert "response.create" not in sideband.arten()
    assert sideband.arten()[-1] == "session.close"



@pytest.mark.asyncio
async def test_ein_backendfehler_ohne_laufende_antwort_gibt_die_sitzung_frei(
    db: Session, owner_user
) -> None:
    """Ein ``error`` des Backends ohne Lebensende — danach wartet nichts mehr darauf.

    ``session.delegation.created`` setzt die Sitzung auf „antwortet". Ein
    Fehler im Backend kann als verschachteltes ``error`` kommen, ohne
    ``response.failed``; bis zum 23.09.2026 blieb die Sitzung dann für immer
    beschäftigt: keine Meldung, kein Nachtrag, kein „bereit" mehr. Es gilt
    dieselbe Regel wie für ein ``error`` der Sitzung (`_fehler_melden`) — und
    eine Antwort, die läuft, bleibt laufend.
    """
    fehler = _backend({"type": "error", "error": {
        "type": "invalid_request_error", "code": "invalid_value", "message": "…",
    }})
    sitzung = _sitzung(_vorbereitung(db, owner_user))
    sitzung._sideband = Sideband()

    await sitzung._verarbeiten({"type": "session.delegation.created", "delegation": {"id": "item_1", "target": "responses"}})
    assert sitzung._response_aktiv is True
    await sitzung._verarbeiten(fehler)
    assert sitzung._response_aktiv is False

    await sitzung._verarbeiten(_begonnen("resp_1"))
    await sitzung._verarbeiten(fehler)
    assert sitzung._response_aktiv is True

@pytest.mark.asyncio
async def test_nach_dem_sicherheitsstopp_setzt_auch_ein_offenes_ergebnis_nichts_fort(
    db: Session, owner_user, monkeypatch
) -> None:
    """Der Stopp kommt, während ein Aufruf noch läuft — danach geht nur ``session.close`` hinaus.

    Zwei Aufrufe einer Antwort: der erste liefert sein Ergebnis, der zweite
    hängt noch, als der Stopp eintrifft. Bis zum 23.09.2026 stieß der Abbau,
    der den zweiten abbricht, über dessen Rückruf trotzdem die Fortsetzung an —
    ein ``response.create`` an eine gestoppte Sitzung, genau die automatische
    Wiederholung, die OpenAI untersagt.
    """
    sperre = threading.Event()
    ausgefuehrt: list[str] = []

    def ausfuehren(_user_id, call, **_kwargs):
        if call.id == "call_2":
            sperre.wait(5)
        ausgefuehrt.append(call.id)
        return {"ok": True}, None, {"tool_name": call.name}, []

    monkeypatch.setattr(realtime_session, "voice_werkzeug_ausfuehren", ausfuehren)
    panel = Panel({"art": "webrtc_offer", "sdp": ANGEBOT})
    sideband = Sideband(beim_schliessen=(_geschlossen("close_requested", 20),))
    _verbindung(monkeypatch, sideband)
    sitzung = _sitzung(_vorbereitung(db, owner_user), panel=panel)
    sideband.liefern(_begonnen("resp_1"), _aufruf("call_1"), _aufruf("call_2"), _fertig("resp_1"))

    lauf = asyncio.create_task(sitzung.fuehren())
    try:
        for _ in range(500):
            if "response.item.create" in sideband.arten():
                break
            await asyncio.sleep(0.01)
        sideband.liefern({"type": "error", "error": {
            "type": "invalid_request_error", "code": "misalignment_policy_violation", "message": "…",
        }})
        await asyncio.wait_for(lauf, timeout=10)
    finally:
        sperre.set()

    assert ausgefuehrt[:1] == ["call_1"]
    assert {"art": "fehler", "code": SICHERHEITSSTOPP} in panel.gesendet
    assert sideband.arten() == ["response.item.create", "session.close"]


@pytest.mark.asyncio
async def test_eine_abgelaufene_sitzung_meldet_das_ende_und_schliesst_nicht_nochmal(
    db: Session, owner_user, monkeypatch
) -> None:
    panel = Panel({"art": "webrtc_offer", "sdp": ANGEBOT})
    sideband = Sideband()
    _verbindung(monkeypatch, sideband)
    vorbereitung = _vorbereitung(db, owner_user)
    sitzung = _sitzung(vorbereitung, panel=panel)
    sideband.liefern(_geschlossen("expired", 60))

    lage = await asyncio.wait_for(sitzung.fuehren(), timeout=10)

    assert lage.abgelaufen is True
    # Der Browser verbindet daraufhin von selbst neu (`useSprachsitzung`).
    assert {"art": "abgelaufen"} in panel.gesendet
    assert "session.close" not in sideband.arten()
    assert _zeile(db, vorbereitung.usage_event_id).accounted_cost_microunits == 60_000 * MINUTENPREIS // 60_000


@pytest.mark.asyncio
async def test_der_ganze_lauf_von_der_anlage_bis_session_closed(
    db: Session, owner_user, monkeypatch
) -> None:
    """Anlage, Delegation, Werkzeug, Fortsetzung und ordentliches Ende in der
    Reihenfolge aus „Usage and graceful close": erst ``session.close``, dann
    weiterlesen bis ``session.closed``, das die Enddauer trägt."""
    ausgefuehrt = _werkzeuge(monkeypatch)
    panel = Panel({"art": "webrtc_offer", "sdp": ANGEBOT})
    sideband = Sideband(beim_schliessen=(_dauer(42), _geschlossen("close_requested", 42)))
    sideband.bei_fortsetzung = lambda: panel.sagen({"art": "beenden"})
    _verbindung(monkeypatch, sideband)
    vorbereitung = _vorbereitung(db, owner_user)
    sitzung = _sitzung(vorbereitung, panel=panel)
    sideband.liefern(
        {"type": "session.delegation.created", "delegation": {"id": "item_1", "target": "responses", "response_id": "resp_1"}},
        _begonnen("resp_1"),
        _aufruf("call_1"),
        _fertig("resp_1", ein=1_000, aus=200),
    )

    await asyncio.wait_for(sitzung.fuehren(), timeout=10)

    assert ausgefuehrt == ["call_1"]
    assert sideband.arten() == ["response.item.create", "response.create", "session.close"]
    assert sideband.geschlossen is True
    assert panel.gesendet[panel.gesendet.index({"art": "webrtc_answer", "sdp": ANTWORT_SDP}) + 1] == {
        "art": "zustand", "zustand": "bereit",
    }
    assert {"art": "zustand", "zustand": "denkt"} in panel.gesendet
    zeile = _zeile(db, vorbereitung.usage_event_id)
    assert zeile.status == "completed"
    assert zeile.provider_requests == 2  # die Anlage und die eine Backend-Antwort
    assert zeile.realtime_text_input_tokens == 1_000
    assert zeile.realtime_text_output_tokens == 200
    assert zeile.accounted_cost_microunits == (
        42_000 * MINUTENPREIS // 60_000
        + (1_000 * TEXTPREIS_EIN + 200 * TEXTPREIS_AUS) // 1_000_000
    )


class EinLeserSideband(Sideband):
    """Wie websockets: zwei gleichzeitige Leser sind ein Fehler (``ConcurrencyError``)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._liest = False
        self.doppelt_gelesen = 0

    async def __anext__(self) -> str:
        if self._liest:
            self.doppelt_gelesen += 1
            raise RuntimeError("cannot call recv while another coroutine is already running recv")
        self._liest = True
        try:
            return await super().__anext__()
        finally:
            self._liest = False


_NEBENLAEUFE = {"_sideband_lesen", "_panel_lesen", "_meldungen_zustellen", "_stille_beobachten"}


@pytest.mark.asyncio
async def test_an_der_zeitgrenze_enden_alle_nebenlaeufe_vor_dem_schliessen(
    db: Session, owner_user, monkeypatch
) -> None:
    """Die Zeitgrenze bricht `_laufen` ab — und mit ihm jeden Nebenlauf.

    `asyncio.wait` bricht die Aufgaben, auf die es wartet, beim eigenen Abbruch
    nicht ab. Bis zum 23.09.2026 las der Sideband-Leser nach Ablauf deshalb
    weiter, während `_ordentlich_schliessen` auf demselben Sideband auf
    ``session.closed`` wartete: websockets lehnt den zweiten Leser ab, und die
    Enddauer, die nur ``session.closed`` trägt, blieb ungebucht.
    """
    monkeypatch.setattr(realtime_session, "MAX_SITZUNGSSEKUNDEN", 0.2)
    panel = Panel({"art": "webrtc_offer", "sdp": ANGEBOT})
    sideband = EinLeserSideband(beim_schliessen=(_geschlossen("close_requested", 30),))
    _verbindung(monkeypatch, sideband)
    vorbereitung = _vorbereitung(db, owner_user)
    sitzung = _sitzung(vorbereitung, panel=panel)

    lage = await asyncio.wait_for(sitzung.fuehren(), timeout=10)
    uebrig = [
        task.get_coro().__name__
        for task in asyncio.all_tasks()
        if not task.done() and task.get_coro().__name__ in _NEBENLAEUFE
    ]

    assert lage.abgelaufen is True
    assert uebrig == []
    assert sideband.doppelt_gelesen == 0
    assert sideband.arten() == ["session.close"]
    assert sitzung._geschlossen is True
    assert _zeile(db, vorbereitung.usage_event_id).accounted_cost_microunits == 30_000 * MINUTENPREIS // 60_000


@pytest.mark.asyncio
async def test_kommt_kein_session_closed_endet_das_warten_nach_der_frist(
    db: Session, owner_user, monkeypatch
) -> None:
    # „A connection closing without this event does not confirm successful
    # finalization." Gewartet wird, aber nicht für immer.
    monkeypatch.setattr(live_session, "SCHLIESSFRIST_SEKUNDEN", 0.05)
    sitzung = _sitzung(_vorbereitung(db, owner_user))
    sitzung._sitzungs_id = "live_123"
    sitzung._sideband = Sideband()

    await asyncio.wait_for(sitzung._ordentlich_schliessen(), timeout=5)

    assert sitzung._sideband.arten() == ["session.close"]
    assert sitzung._geschlossen is False


# ── Anweisungen ───────────────────────────────────────────────────────────


def test_die_stimme_bekommt_die_vorlage_aus_prompting_gpt_live() -> None:
    """„Keep the template's Backchannel policy, Interruption policy, and
    Delegation policy headings" — und die Liste der Backend-Werkzeuge, nach der
    die Stimme entscheidet, was sie abgibt."""
    anweisungen = live_session.live_anweisungen(
        [
            {"name": "web_search", "description": "Sucht im Netz. Liefert Treffer mit Quellen."},
            {"name": "server_status", "description": "x" * 400},
        ],
        "de",
    )

    for ueberschrift in (
        "Backchannel policy:",
        "Interruption policy:",
        "Delegation policy:\nBackend tools:",
        "Delegate to the backend when:",
        "Do not delegate to the backend when:",
    ):
        assert ueberschrift in anweisungen
    assert "- web_search: Sucht im Netz" in anweisungen
    assert "Liefert Treffer" not in anweisungen
    zeile = next(z for z in anweisungen.splitlines() if z.startswith("- server_status"))
    assert len(zeile) <= len("- server_status: ") + live_session.MAX_FAEHIGKEIT_ZEICHEN + 2


def test_das_backend_bekommt_die_drei_abschnitte_der_delegationsseite() -> None:
    anweisungen = live_session.backend_anweisungen("PANELPROMPT")

    kontext = anweisungen.index("## Voice conversation context")
    aufgabe = anweisungen.index("## Task instructions\nPANELPROMPT")
    ergebnis = anweisungen.index("## Return the result")
    assert kontext < aufgabe < ergebnis
