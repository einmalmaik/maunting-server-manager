"""Invarianten des optionalen panelweiten OpenAI-Realtime-Zugangs."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pathlib import Path
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from uuid import uuid4

import httpx
from config import settings
from database import Base
from models import AiUsageEvent
from routers import ai_voice
from services import ai_provider_service
from services import ai_usage_service
from services.ai_voice import realtime_session


PREISE = {
    "realtime_text_input_price_micro_usd_per_million": 1,
    "realtime_text_output_price_micro_usd_per_million": 2,
    "realtime_audio_input_price_micro_usd_per_million": 3,
    "realtime_audio_output_price_micro_usd_per_million": 4,
}
ORIGIN = {"origin": "http://localhost:3000"}


def _realtime(db: Session, name: str):
    provider = ai_provider_service.create_provider(
        db,
        name=name,
        provider_kind="openai",
        default_model=None,
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-test-realtime-not-real",
        realtime_default=True,
        realtime_model="gpt-realtime",
        realtime_voice="marin",
        realtime_language="de",
        realtime_vad_eagerness="high",
        **PREISE,
    )
    db.commit()
    db.refresh(provider)
    return provider


def test_realtime_needs_openai_key_voice_and_model_but_not_prices(db: Session) -> None:
    provider = ai_provider_service.create_provider(
        db,
        name="Ohne Preise",
        provider_kind="openai",
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-test-realtime-not-real",
        realtime_default=True,
        realtime_model="gpt-realtime",
        realtime_voice="marin",
    )
    assert provider.realtime_default is True
    assert provider.realtime_audio_output_price_micro_usd_per_million is None

    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.create_provider(
            db,
            name="Falscher Anbieter",
            provider_kind="openrouter",
            enabled=True,
            requires_api_key=True,
            operator_api_key="sk-or-v1-test-not-real",
            realtime_default=True,
            realtime_model="openai/gpt-realtime",
            realtime_voice="marin",
            **PREISE,
        )


def test_realtime_2_accepts_only_its_supported_reasoning_efforts(db: Session) -> None:
    provider = ai_provider_service.create_provider(
        db,
        name="Realtime 2",
        provider_kind="openai",
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-test-realtime-not-real",
        realtime_default=True,
        realtime_model="gpt-realtime-2",
        realtime_voice="marin",
        realtime_reasoning_effort="medium",
        **PREISE,
    )
    assert provider.realtime_reasoning_effort == "medium"

    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.update_provider(
            db,
            provider,
            values={"realtime_model": "gpt-realtime-1.5"},
            operator_api_key=None,
            clear_operator_api_key=False,
        )
    db.rollback()

    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.create_provider(
            db,
            name="Kein Realtime-Modell",
            provider_kind="openai",
            enabled=True,
            requires_api_key=True,
            operator_api_key="sk-test-realtime-not-real",
            realtime_default=True,
            realtime_model="gpt-4.1",
            realtime_voice="marin",
            **PREISE,
        )


def test_aktivierung_ersetzt_den_bisherigen_realtime_zugang(db: Session) -> None:
    erster = _realtime(db, "Realtime 1")
    zweiter = _realtime(db, "Realtime 2")
    db.refresh(erster)
    assert erster.realtime_default is False
    assert zweiter.realtime_default is True
    assert ai_provider_service.realtime_zugang(db).id == zweiter.id


def test_key_entfernen_schaltet_realtime_ohne_fallbackmarke_ab(db: Session) -> None:
    provider = _realtime(db, "Realtime")
    ai_provider_service.update_provider(
        db,
        provider,
        values={},
        operator_api_key=None,
        clear_operator_api_key=True,
    )
    db.commit()
    assert provider.realtime_default is False
    assert ai_provider_service.realtime_zugang(db) is None


def test_voice_config_priorisiert_realtime_ohne_legacy_abhaengigkeit(
    client: TestClient, owner_cookies: dict, db: Session
) -> None:
    _realtime(db, "Realtime")
    antwort = client.get("/api/ai/voice/config", cookies=owner_cookies)
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["available"] is True
    assert daten["mode"] == "openai_realtime"
    assert daten["model"] == "gpt-realtime"
    assert daten["voice"] == "marin"


def test_realtime_websocket_never_enters_legacy_path(
    client: TestClient, owner_cookies: dict, db: Session, monkeypatch
) -> None:
    provider = _realtime(db, "Realtime")
    ausgefuehrt: list[int] = []

    def kein_legacy(*args, **kwargs):
        raise AssertionError("Legacy darf bei aktivem Realtime nicht aufgerufen werden")

    class StilleRealtimeSitzung:
        def __init__(self, websocket, **kwargs):
            ausgefuehrt.append(kwargs["user_id"])

        async def fuehren(self):
            return None

    monkeypatch.setattr(ai_voice, "sprachzugang", kein_legacy)
    monkeypatch.setattr(ai_voice, "pipecat_verfuegbar", kein_legacy)
    monkeypatch.setattr(ai_voice, "realtime_vorbereiten", lambda *args, **kwargs: _vorbereitung())
    monkeypatch.setattr(ai_voice, "RealtimeSitzung", StilleRealtimeSitzung)

    with client.websocket_connect(
        f"/api/ai/voice/ws?provider_id={provider.id + 100}",
        cookies=owner_cookies,
        headers=ORIGIN,
    ):
        pass

    assert ausgefuehrt


def _karte(kennung: str, werkzeug: str = "propose_backup") -> dict:
    """Ein offener Vorschlag, wie ihn `voice_werkzeug_ausfuehren` meldet."""
    return {
        "id": kennung,
        "call_id": f"call-{kennung}",
        "tool_name": werkzeug,
        "status": "proposed",
        "autonomous": False,
    }


def _vorbereitung() -> realtime_session.RealtimeVorbereitung:
    return realtime_session.RealtimeVorbereitung(
        provider_id=1,
        model="gpt-realtime",
        voice="marin",
        reasoning_effort=None,
        language="de",
        vad_eagerness="high",
        api_key="sk-test-never-log",
        instructions="Direkt antworten",
        tools=[{"type": "function", "name": "web_search", "parameters": {"type": "object"}}],
        conversation_id="conversation-1",
        usage_event_id=1,
    )


def test_realtime_session_uses_semantic_vad_without_transcription() -> None:
    config = realtime_session._session_config(_vorbereitung())
    assert config["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad",
        "eagerness": "high",
        "create_response": True,
        "interrupt_response": False,
    }
    assert "transcription" not in config["audio"]["input"]
    assert config["max_output_tokens"] == 32_768


def test_realtime_starts_workers_but_does_not_steer_them(db: Session, regular_user, monkeypatch) -> None:
    """Die Stimme uebergibt Arbeit, steuert Worker aber nicht.

    Vom 29.08. bis 24.09.2026 bekam Realtime gar kein Worker-Werkzeug:
    Recherchen und Karten sollte die Stimme selbst erledigen. Seit dem
    Betreiberplan zur Rechtevergabe ("unterwegs per Stimme die normalen Rechte
    geben") ist `worker_start` wieder dabei, sofern das Angebot es enthaelt
    (`ai.background.use`): Rechte anderer Benutzer aendert nur ein Worker.
    Abbrechen und Antworten bleiben beim Chat — die Stimme sieht die
    Worker-Fenster nicht. Die Regel fuer Recherchen gilt weiter und steht im
    Text.
    """
    provider = _realtime(db, "Realtime mit Worker-Start")
    provider.worker_model = "gpt-worker"
    db.commit()
    rollen: list[str] = []
    monkeypatch.setattr(
        realtime_session.ai_action_service,
        "angebotene_werkzeuge",
        lambda *_args: {"web_search", "worker_start", "worker_cancel", "worker_antwort"},
    )
    monkeypatch.setattr(realtime_session.ai_provider_service, "resolve_api_key", lambda *_args: "test-key")
    monkeypatch.setattr(
        realtime_session.ai_prompt,
        "build",
        lambda **kwargs: rollen.append(kwargs["rolle"]) or "Direkt antworten",
    )

    vorbereiten = realtime_session.vorbereiten(
        db,
        provider=provider,
        user=regular_user,
        herkunft="panel",
    )

    namen = {tool["name"] for tool in vorbereiten.tools}
    assert "voice_resolve_latest_proposal" in namen
    assert {"voice_set_region_view", "voice_leave_region_view"} <= namen
    assert "worker_start" in namen
    assert namen.isdisjoint({"worker_cancel", "worker_antwort"})
    assert "keine Hintergrund-Worker dafür starten" in vorbereiten.instructions
    assert "ändern lässt du sie mit worker_start" in vorbereiten.instructions
    assert rollen == ["realtime"]


def test_realtime_2_sends_the_operator_reasoning_effort() -> None:
    vorbereiten = _vorbereitung()
    config = realtime_session._session_config(
        realtime_session.RealtimeVorbereitung(
            **{**vorbereiten.__dict__, "model": "gpt-realtime-2", "reasoning_effort": "medium"}
        )
    )
    assert config["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_tool_name_is_announced_before_execution_and_payload_is_projected(monkeypatch) -> None:
    class Panel:
        def __init__(self):
            self.sent = []

        async def send_json(self, value):
            self.sent.append(value)

    class Sideband:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(value)

    panel = Panel()
    sideband = Sideband()
    session = realtime_session.RealtimeSitzung(
        panel,
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    session._sideband = sideband
    monkeypatch.setattr(
        realtime_session,
        "voice_werkzeug_ausfuehren",
        lambda *args, **kwargs: (
            {"results": [{"title": "Quelle", "url": "https://example.invalid"}]},
            None,
            {
                "tool_name": "web_search",
                "web_results": [{"title": "Quelle", "url": "https://example.invalid"}],
            },
            [],
        ),
    )
    await session._tool_ausfuehren({
        "call_id": "call_1",
        "name": "web_search",
        "arguments": '{"query":"test"}',
    })
    assert panel.sent[0]["art"] == "werkzeug_gestartet"
    assert "arguments" not in panel.sent[0]
    assert panel.sent[1]["web_results"][0]["title"] == "Quelle"
    assert len(sideband.sent) == 2


@pytest.mark.asyncio
async def test_slow_realtime_tool_returns_a_safe_timeout_and_starts_the_followup(monkeypatch) -> None:
    class Panel:
        def __init__(self):
            self.sent = []

        async def send_json(self, value):
            self.sent.append(value)

    class Sideband:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(value)

    panel = Panel()
    sideband = Sideband()
    session = realtime_session.RealtimeSitzung(
        panel,
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    session._sideband = sideband
    monkeypatch.setattr(realtime_session, "REALTIME_TOOL_TIMEOUT_SECONDS", 0.01)

    def slow_tool(*_args, **_kwargs):
        time.sleep(0.05)
        return {}, None, {"tool_name": "web_search"}, []

    monkeypatch.setattr(realtime_session, "voice_werkzeug_ausfuehren", slow_tool)

    await session._tool_ausfuehren({
        "call_id": "call_timeout",
        "name": "web_search",
        "arguments": '{"query":"Berlin"}',
    })

    assert panel.sent[1] == {
        "art": "werkzeug",
        "name": "web_search",
        "tool_name": "web_search",
        "failed": True,
        "code": "REALTIME_TOOL_TIMEOUT",
        "reason": "timeout",
    }
    output_frame = json.loads(sideband.sent[0])
    # In derselben Untrusted-Hülle wie im Chat (`werkzeugergebnis_umschlag`) —
    # auch die Absage. Eine Ausnahme für kurze Ergebnisse wäre eine zweite
    # Regel für dieselbe Frage, und das Modell müsste dann raten, ob ein
    # fehlender Marker „vertrauenswürdig" heißt oder „vergessen".
    assert json.loads(output_frame["item"]["output"]) == {
        "untrusted": True,
        "tool": "web_search",
        "data": {"error": "TOOL_TIMEOUT"},
    }
    assert json.loads(sideband.sent[1]) == {"type": "response.create"}


@pytest.mark.asyncio
async def test_realtime_region_sends_initial_data_before_optional_enrichment(monkeypatch) -> None:
    class Panel:
        def __init__(self):
            self.sent = []

        async def send_json(self, value):
            self.sent.append(value)

    class Sideband:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(value)

    panel = Panel()
    sideband = Sideband()
    session = realtime_session.RealtimeSitzung(
        panel,
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    session._angeboten.add("analyze_region")
    session._sideband = sideband
    initial = {
        "status": "success",
        "location": "Berlin",
        "coordinates": {"latitude": 52.52, "longitude": 13.405, "bbox": [13, 52, 14, 53]},
        "weather": {"temperature_celsius": 20},
        "satellite": {"available": True, "scenes": [], "layers": {}},
    }
    complete = {**initial, "traffic": {"status": "available"}, "news": [], "news_status": "available"}
    monkeypatch.setattr(session, "_region_anfang", lambda _args: initial)
    monkeypatch.setattr(session, "_region_ergaenzen", lambda _args, _initial: complete)
    # Im autonomen Modus: keine Karte davor.
    monkeypatch.setattr(
        realtime_session.voice_interactions, "freigabe_einholen", lambda *a, **k: None
    )

    await session._tool_ausfuehren({
        "call_id": "call_region",
        "name": "analyze_region",
        "arguments": '{"location":"Berlin"}',
    })

    first_output = json.loads(sideband.sent[0])
    # `analyze_region` zieht Nachrichten, Verkehrslage und öffentliche Posts von
    # draußen herein — die Hülle gehört hier genauso dran wie an einer Logzeile.
    # Die Anzeige im Panel (`geo_analysis`) bleibt davon unberührt: sie geht an
    # einen Menschen, nicht an das Modell.
    assert json.loads(first_output["item"]["output"]) == {
        "untrusted": True,
        "tool": "analyze_region",
        "data": initial,
    }
    assert panel.sent[1]["geo_analysis"] == initial

    session._response_aktiv = False
    await asyncio.gather(*tuple(session._region_tasks))
    assert panel.sent[2]["geo_analysis"] == complete
    assert json.loads(sideband.sent[-1]) == {"type": "response.create"}


@pytest.mark.asyncio
async def test_realtime_region_gibt_das_urteil_dem_modell_nicht_dem_schirm(monkeypatch) -> None:
    """Die Regionsanalyse nimmt einen eigenen Weg und holt die Ethik-Engine selbst.

    Ihr Urteil gehört an den Wert fürs Modell. Die Karte im Panel und das
    Nachladen bekommen den Stand ohne Beratung: sie ist für das Modell
    geschrieben, das mit dem Menschen spricht, nicht für den Schirm.
    """
    class Panel:
        def __init__(self):
            self.sent = []

        async def send_json(self, value):
            self.sent.append(value)

    class Sideband:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(value)

    panel = Panel()
    sideband = Sideband()
    session = realtime_session.RealtimeSitzung(
        panel,
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    session._angeboten.add("analyze_region")
    session._sideband = sideband
    initial = {"status": "success", "location": "Berlin", "weather": {"temperature_celsius": 20}}
    hinweis = {"untrusted": True, "quelle": "ethik_engine", "einschaetzung": "review",
               "empfehlung": "Nichts Privates zeigen.", "begruendung": "Ein Wohnhaus."}
    nachgeladen: list[dict] = []
    monkeypatch.setattr(session, "_region_anfang", lambda _args: initial)
    monkeypatch.setattr(session, "_region_nachladen", lambda _args, stand: nachgeladen.append(stand))
    monkeypatch.setattr(
        realtime_session.voice_interactions, "freigabe_einholen", lambda *a, **k: None
    )
    monkeypatch.setattr(
        realtime_session.voice_interactions, "ethik_anstossen", lambda *a, **k: None
    )
    monkeypatch.setattr(
        realtime_session.voice_interactions, "ethik_abholen", lambda *a, **k: hinweis
    )

    await session._tool_ausfuehren({
        "call_id": "call_region",
        "name": "analyze_region",
        "arguments": '{"location":"Berlin"}',
    })

    ausgabe = json.loads(json.loads(sideband.sent[0])["item"]["output"])
    assert ausgabe["data"] == {**initial, "ethik": hinweis}
    assert panel.sent[1]["geo_analysis"] == initial
    assert nachgeladen == [initial]


@pytest.mark.asyncio
async def test_realtime_region_fragt_ohne_autonomie_erst(monkeypatch) -> None:
    """Ohne autonomen Modus holt die Regionsanalyse nichts, bevor jemand ja sagt.

    Sie nimmt einen eigenen, schnelleren Weg als die übrigen Werkzeuge und lief
    deshalb an der Frage nach der Zustimmung vorbei: Wetter, Satellit und
    Nachrichten kamen, bevor jemand zugestimmt hatte. Vorgabe des Betreibers
    vom 23.09.2026: ohne autonomen Modus fragt die Stimme vor jedem Werkzeug.
    """
    class Panel:
        def __init__(self):
            self.sent = []

        async def send_json(self, value):
            self.sent.append(value)

    class Sideband:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(value)

    panel = Panel()
    sideband = Sideband()
    session = realtime_session.RealtimeSitzung(
        panel,
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    session._angeboten.add("analyze_region")
    session._sideband = sideband

    def _nicht_ohne_ja(_args):
        raise AssertionError("die Analyse lief ohne Zustimmung")

    monkeypatch.setattr(session, "_region_anfang", _nicht_ohne_ja)
    kennung = "0b7e7a52-2f6e-4a39-9d4e-3c2d1f0a9b11"
    karte = {
        "id": kennung,
        "call_id": "call_region",
        "tool_name": "analyze_region",
        "status": "proposed",
        "autonomous": False,
    }
    wert = {
        "proposals": [karte],
        "status": "needs_confirmation",
        "hinweis": realtime_session.voice_interactions.KLICK_NOETIG,
    }
    monkeypatch.setattr(
        realtime_session.voice_interactions,
        "freigabe_einholen",
        lambda *a, **k: (wert, None, {"tool_name": "analyze_region"}, [karte]),
    )

    await session._tool_ausfuehren({
        "call_id": "call_region",
        "name": "analyze_region",
        "arguments": '{"location":"Berlin"}',
    })

    ausgabe = json.loads(json.loads(sideband.sent[0])["item"]["output"])
    assert ausgabe["data"]["status"] == "needs_confirmation"
    assert "voice_resolve_latest_proposal" in ausgabe["data"]["hinweis"]
    # Die Karte steht in der Sprachansicht, ein gesprochenes Ja genügt.
    vorschlagsrahmen = [r for r in panel.sent if r.get("art") == "vorschlag"]
    assert vorschlagsrahmen == [{
        "art": "vorschlag",
        "vorschlag": {k: v for k, v in karte.items() if k != "call_id"},
        "klick": True,
    }]
    assert session._vorschlaege.rahmen() == vorschlagsrahmen[0]
    assert not session._region_tasks
    assert not any("geo_analysis" in r for r in panel.sent)


@pytest.mark.asyncio
async def test_nach_dem_ja_kommt_das_ergebnis_mit(monkeypatch) -> None:
    """Ein bestätigter Lesevorschlag liefert sein Ergebnis an Modell und Panel.

    Ein Lesevorschlag der Stimme hängt an keinem Lauf, der das Ergebnis
    weitertrüge. Ohne diesen Weg meldete das Ja nur „bestätigt": das Modell
    wusste nichts über das Wetter, und die Regionsansicht blieb leer.
    """
    class Panel:
        def __init__(self):
            self.sent = []

        async def send_json(self, value):
            self.sent.append(value)

    class Sideband:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(value)

    panel = Panel()
    sideband = Sideband()
    session = realtime_session.RealtimeSitzung(
        panel,
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    session._angeboten.add("voice_resolve_latest_proposal")
    session._sideband = sideband
    session._vorschlaege.merken(_karte("vorschlag-region", "analyze_region"))
    analyse = {
        "status": "success",
        "location": "Berlin",
        "coordinates": {"latitude": 52.52, "longitude": 13.405, "bbox": [13, 52, 14, 53]},
    }
    voice = realtime_session.voice_interactions
    monkeypatch.setattr(voice, "schon_entschieden", lambda **_: None)
    monkeypatch.setattr(voice, "braucht_klick", lambda **_: False)
    monkeypatch.setattr(
        voice,
        "vorschlag_ausfuehren",
        lambda **_: voice.Ausgang(erledigt=True, werkzeug="analyze_region", ergebnis=analyse),
    )

    await session._tool_ausfuehren({
        "call_id": "call_ja",
        "name": "voice_resolve_latest_proposal",
        "arguments": '{"decision":"confirm"}',
    })

    ausgabe = json.loads(json.loads(sideband.sent[0])["item"]["output"])
    # In der Untrusted-Hülle: Nachrichten und Posts kommen von draußen.
    assert ausgabe["untrusted"] is True
    assert ausgabe["data"] == {
        "status": "confirmed", "tool_name": "analyze_region", "result": analyse,
    }
    werkzeugrahmen = [r for r in panel.sent if r.get("geo_analysis") is not None]
    assert werkzeugrahmen and werkzeugrahmen[0]["geo_analysis"] == analyse
    # Die erledigte Karte verschwindet aus der Sprachansicht.
    assert {"art": "vorschlag", "vorschlag": None} in panel.sent


@pytest.mark.asyncio
async def test_parallel_realtime_tools_start_one_followup_response(monkeypatch) -> None:
    class Panel:
        async def send_json(self, _value):
            pass

    class Sideband:
        def __init__(self):
            self.sent = []

        async def send(self, value):
            self.sent.append(value)

    session = realtime_session.RealtimeSitzung(
        Panel(),
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    sideband = Sideband()
    session._sideband = sideband
    monkeypatch.setattr(
        realtime_session,
        "voice_werkzeug_ausfuehren",
        lambda *_args, **_kwargs: ({"ok": True}, None, {"tool_name": "web_search"}, []),
    )

    for call_id in ("call_one", "call_two"):
        task = asyncio.create_task(session._tool_ausfuehren({
            "call_id": call_id,
            "name": "web_search",
            "arguments": '{"query":"Berlin"}',
        }))
        session._tool_tasks.add(task)
        task.add_done_callback(session._tool_task_fertig)

    await asyncio.gather(*tuple(session._tool_tasks))
    await asyncio.sleep(0)

    frames = [json.loads(value) for value in sideband.sent]
    assert [frame["type"] for frame in frames].count("conversation.item.create") == 2
    assert [frame["type"] for frame in frames].count("response.create") == 1


@pytest.mark.asyncio
async def test_oversized_sdp_is_rejected_before_any_provider_request() -> None:
    class Panel:
        async def send_json(self, value):
            pass

    session = realtime_session.RealtimeSitzung(
        Panel(),
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    with pytest.raises(realtime_session.RealtimeSitzungsfehler, match="REALTIME_SDP_INVALID"):
        await session._handshake("x" * (realtime_session.MAX_SDP_ZEICHEN + 1))


@pytest.mark.asyncio
async def test_handshake_keeps_key_and_call_id_on_server(monkeypatch) -> None:
    requests = []
    sideband_calls = []

    class Http:
        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return httpx.Response(
                201,
                text="v=0\r\nanswer",
                headers={"location": "/v1/realtime/calls/rtc_safe-id"},
            )

    class Sideband:
        async def close(self):
            pass

    async def connect(url, **kwargs):
        sideband_calls.append((url, kwargs))
        return Sideband()

    monkeypatch.setattr(realtime_session.websockets, "connect", connect)
    session = realtime_session.RealtimeSitzung(
        object(),
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=Http(),
        herkunft="panel",
        familie=None,
    )
    answer = await session._handshake("v=0\r\noffer")
    assert answer == "v=0\r\nanswer"
    assert requests[0][0] == "https://api.openai.com/v1/realtime/calls"
    assert requests[0][1]["headers"]["Authorization"] == "Bearer sk-test-never-log"
    assert sideband_calls[0][0].endswith("call_id=rtc_safe-id")
    assert sideband_calls[0][1]["additional_headers"]["Authorization"] == "Bearer sk-test-never-log"


def test_realtime_usage_accumulates_all_token_classes_and_responses(
    db: Session, owner_user
) -> None:
    event = ai_usage_service.reserve_ai_usage(
        db,
        owner_user,
        request_id=uuid4(),
        estimated_tokens=0,
        provider_id=None,
        model="gpt-realtime",
    )
    db.commit()
    for _ in range(2):
        ai_usage_service.realtime_verbrauch_ergaenzen(
            db,
            event_id=event.id,
            text_input=10,
            text_output=3,
            audio_input=20,
            audio_output=5,
            cost_microunits=7,
        )
        db.commit()
    ai_usage_service.realtime_sitzung_abschliessen(db, event.id)
    db.commit()
    db.refresh(event)
    assert event.status == "completed"
    assert event.accounted_tokens == 76
    assert event.accounted_cost_microunits == 14
    assert event.provider_requests == 2
    assert event.realtime_text_input_tokens == 20
    assert event.realtime_text_output_tokens == 6
    assert event.realtime_audio_input_tokens == 40
    assert event.realtime_audio_output_tokens == 10


def test_realtime_session_without_provider_response_releases_reservation(
    db: Session, owner_user
) -> None:
    event = ai_usage_service.reserve_ai_usage(
        db,
        owner_user,
        request_id=uuid4(),
        estimated_tokens=0,
        provider_id=None,
        model="gpt-realtime",
    )
    db.commit()
    ai_usage_service.realtime_sitzung_abschliessen(db, event.id)
    db.commit()
    gespeichert = db.get(AiUsageEvent, event.id)
    assert gespeichert.status == "failed"
    assert gespeichert.accounted_tokens == 0


def test_voice_confirmation_is_bound_to_latest_proposal_in_session(monkeypatch) -> None:
    session = realtime_session.RealtimeSitzung(
        object(),
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    aufrufe = []
    monkeypatch.setattr(
        realtime_session.voice_interactions,
        "vorschlag_ausfuehren",
        lambda **werte: realtime_session.voice_interactions.Ausgang(
            erledigt=aufrufe.append(werte) is None
        ),
    )
    session._vorschlaege.merken(_karte("proposal-current"))
    wert, fehler = session._vorschlag_entscheiden("confirm")
    assert fehler is None
    assert wert == {"status": "confirmed"}
    assert aufrufe == [{"user_id": 7, "kennung": "proposal-current"}]
    assert session._vorschlaege.leer
    _, zweiter_fehler = session._vorschlag_entscheiden("confirm")
    assert zweiter_fehler is not None


def test_gesprochenes_ja_traegt_keine_unumkehrbare_aktion(monkeypatch) -> None:
    """Was auch im autonomen Modus fragt, braucht den Finger auf der Karte.

    Seit dem 23.09.2026 ist das jedes Löschen, auch eines mit Rückweg
    (Betreiberwahl „Klick auf die Karte").

    Im Panel entscheidet ein Mensch, indem er drückt. In der Sprachsitzung
    entscheidet das *Modell*, dass der Mensch zugestimmt habe — und derselbe
    Lauf hat in derselben Runde Logzeilen, Websuchtreffer oder Mailtext
    gelesen. Ein „der Benutzer hat bereits zugestimmt" darin ist genau die
    Vorlage, auf die ein Modell hereinfällt; die Untrusted-Markierung macht das
    unwahrscheinlicher, sie ist aber ein Prompt und keine Schranke.

    Die Trennlinie ist dieselbe wie im autonomen Modus und steht an derselben
    Stelle: `Werkzeug.immer_bestaetigen` → `ALWAYS_CONFIRM_TOOLS`.
    """
    session = realtime_session.RealtimeSitzung(
        object(),
        vorbereitung=_vorbereitung(),
        user_id=7,
        http_client=None,
        herkunft="panel",
        familie=None,
    )
    aufrufe: list[dict] = []
    monkeypatch.setattr(
        realtime_session.voice_interactions,
        "vorschlag_ausfuehren",
        lambda **werte: realtime_session.voice_interactions.Ausgang(
            erledigt=aufrufe.append(werte) is None
        ),
    )
    monkeypatch.setattr(
        realtime_session.voice_interactions, "braucht_klick", lambda **_: True
    )

    session._vorschlaege.merken(_karte("proposal-delete", "propose_file_delete"))
    wert, fehler = session._vorschlag_entscheiden("confirm")

    assert fehler is None
    assert wert["status"] == "needs_panel_confirmation"
    # Der Punkt: es ist nichts gelaufen. Die Karte bleibt stehen, der Benutzer
    # drückt im Panel — abgelehnt ist die gesprochene Bestätigung, nicht der
    # Vorschlag.
    assert aufrufe == []
    # Und die Sitzung weiß das noch: ein zweites Ja zeigt wieder auf den
    # Knopf. Bis zum 23.09.2026 vergaß sie die Karte beim ersten Ja, und
    # das zweite hörte „kein passender Vorschlag".
    wert, fehler = session._vorschlag_entscheiden("confirm")
    assert fehler is None
    assert wert["status"] == "needs_panel_confirmation"
    assert session._vorschlaege.rahmen()["vorschlag"]["id"] == "proposal-delete"
    assert aufrufe == []


class _Mitschrift:
    """Panel und Sideband zugleich: alles Gesendete landet in `sent`."""

    def __init__(self) -> None:
        self.sent: list = []

    async def send_json(self, value) -> None:
        self.sent.append(value)

    async def send(self, value) -> None:
        self.sent.append(value)


def _ausgabe(sideband: _Mitschrift, call_id: str) -> dict:
    """Was das Modell zu genau diesem Aufruf zurückbekam, ohne Untrusted-Hülle."""
    for roh in sideband.sent:
        rahmen = json.loads(roh)
        eintrag = rahmen.get("item") or {}
        if eintrag.get("call_id") == call_id:
            return json.loads(eintrag["output"])["data"]
    raise AssertionError(f"kein Ergebnis zu {call_id}")


@pytest.mark.asyncio
async def test_ein_ja_gilt_genau_einer_karte(monkeypatch) -> None:
    """Zwei Karten, ein Ja: es läuft die jüngste, die andere verfällt, angesagt.

    Bis zum 23.09.2026 hielt die Sitzung genau eine Kennung, und die ältere
    Karte verschwand still. Ein Stapel, der sie danach zur jüngsten machte,
    ließ ein doppelt geschicktes Ja sie ausführen, ohne dass jemand nach ihr
    gefragt hatte (Review vom selben Tag). Jetzt gilt die Regel der
    Pipeline-Stimme: ein Ja, ein Vorschlag, und das Modell erfährt den Rest.
    """
    panel, sideband = _Mitschrift(), _Mitschrift()
    session = realtime_session.RealtimeSitzung(
        panel, vorbereitung=_vorbereitung(), user_id=7, http_client=None,
        herkunft="panel", familie=None,
    )
    session._sideband = sideband
    session._angeboten |= {
        "propose_server_lifecycle", "propose_backup", "voice_resolve_latest_proposal",
    }
    voice = realtime_session.voice_interactions
    ausgefuehrt: list[str] = []
    monkeypatch.setattr(voice, "schon_entschieden", lambda *, user_id, kennung: None)
    monkeypatch.setattr(voice, "braucht_klick", lambda **_: False)
    monkeypatch.setattr(
        voice, "vorschlag_ausfuehren",
        lambda *, user_id, kennung: ausgefuehrt.append(kennung) or voice.Ausgang(erledigt=True),
    )
    antworten = iter([
        ({"status": "needs_confirmation"}, None, {"tool_name": "propose_server_lifecycle"},
         [_karte("neustart", "propose_server_lifecycle")]),
        ({"status": "needs_confirmation"}, None, {"tool_name": "propose_backup"},
         [_karte("backup", "propose_backup")]),
    ])
    monkeypatch.setattr(
        realtime_session, "voice_werkzeug_ausfuehren", lambda *a, **k: next(antworten)
    )
    for call_id, name in (("c1", "propose_server_lifecycle"), ("c2", "propose_backup")):
        await session._tool_ausfuehren({"call_id": call_id, "name": name, "arguments": "{}"})

    # Dasselbe Ja zweimal, wie nach einem Dazwischenreden.
    for call_id in ("ja1", "ja2"):
        await session._tool_ausfuehren({
            "call_id": call_id, "name": "voice_resolve_latest_proposal",
            "arguments": '{"decision":"confirm"}',
        })

    assert ausgefuehrt == ["backup"]
    erste = _ausgabe(sideband, "ja1")
    assert erste["status"] == "confirmed"
    assert erste["verworfene_vorschlaege"] == ["propose_server_lifecycle"]
    assert erste["hinweis_verworfen"] == voice.VERWORFEN
    assert "error" in _ausgabe(sideband, "ja2")
    assert [r for r in panel.sent if r.get("art") == "vorschlag"][-1] == {
        "art": "vorschlag", "vorschlag": None,
    }
    assert session._vorschlaege.leer


def test_ein_gescheitertes_ja_macht_die_aeltere_karte_nicht_zur_neuen(monkeypatch) -> None:
    """Scheitert die Ausführung, trifft das Ja zum Wiederholen nicht die Karte darunter.

    Das Modell bietet nach „konnte nicht bestätigt werden" an, es noch einmal
    zu versuchen. Lag darunter eine zweite Karte, führte das nächste Ja sie
    aus, obwohl der Mensch die erste meinte.
    """
    session = realtime_session.RealtimeSitzung(
        object(), vorbereitung=_vorbereitung(), user_id=7, http_client=None,
        herkunft="panel", familie=None,
    )
    voice = realtime_session.voice_interactions
    ausgefuehrt: list[str] = []

    def ausfuehren(*, user_id, kennung):
        ausgefuehrt.append(kennung)
        return voice.Ausgang(erledigt=kennung != "neustart")

    monkeypatch.setattr(voice, "schon_entschieden", lambda *, user_id, kennung: None)
    monkeypatch.setattr(voice, "braucht_klick", lambda **_: False)
    monkeypatch.setattr(voice, "vorschlag_ausfuehren", ausfuehren)
    session._vorschlaege.merken(_karte("konfig", "propose_config_update"))
    session._vorschlaege.merken(_karte("neustart", "propose_server_lifecycle"))

    wert, fehler = session._vorschlag_entscheiden("confirm")
    assert fehler is not None
    assert wert["verworfene_vorschlaege"] == ["propose_config_update"]

    _, fehler = session._vorschlag_entscheiden("confirm")
    assert fehler is not None
    assert ausgefuehrt == ["neustart"]


def test_ein_ja_nach_dem_klick_trifft_nicht_die_karte_darunter(monkeypatch) -> None:
    """Wer die Löschkarte geklickt hat und dann „ja" sagt, meint nicht den Neustart."""
    session = realtime_session.RealtimeSitzung(
        object(), vorbereitung=_vorbereitung(), user_id=7, http_client=None,
        herkunft="panel", familie=None,
    )
    voice = realtime_session.voice_interactions
    ausgefuehrt: list[str] = []
    monkeypatch.setattr(
        voice, "schon_entschieden",
        lambda *, user_id, kennung: "succeeded" if kennung.startswith("loeschen") else None,
    )
    monkeypatch.setattr(voice, "braucht_klick", lambda *, user_id, kennung: kennung.startswith("loeschen"))
    monkeypatch.setattr(
        voice, "vorschlag_ausfuehren",
        lambda *, user_id, kennung: ausgefuehrt.append(kennung) or voice.Ausgang(erledigt=True),
    )
    session._vorschlaege.merken(_karte("neustart", "propose_server_lifecycle"))
    session._vorschlaege.merken(_karte("loeschen", "propose_file_delete"))

    wert, fehler = session._vorschlag_entscheiden("confirm")

    assert fehler is None
    assert wert["status"] == "already_decided"
    assert wert["verworfene_vorschlaege"] == ["propose_server_lifecycle"]
    assert ausgefuehrt == []

    # Ebenso ein Nein nach dem Klick: es ist gelaufen, nicht abgebrochen.
    session._vorschlaege.merken(_karte("loeschen-2", "propose_file_delete"))
    wert, _ = session._vorschlag_entscheiden("reject")
    assert wert["status"] == "already_decided"
    assert session._vorschlaege.leer


def test_ein_entzogenes_recht_beendet_die_sitzung_nicht(monkeypatch) -> None:
    """Wird der Server zwischen Karte und Ja entzogen, gibt es eine Antwort.

    `owned_proposal` wirft dann `AI_ACTION_ACCESS_REVOKED`. Bis zum Review vom
    23.09.2026 endete die Gemini-Sitzung daran, und Realtime verstummte bei
    jedem Ja oder Nein zu derselben Karte.
    """
    from services.ai_action_errors import AiActionStateError

    session = realtime_session.RealtimeSitzung(
        object(), vorbereitung=_vorbereitung(), user_id=7, http_client=None,
        herkunft="panel", familie=None,
    )
    voice = realtime_session.voice_interactions

    def entzogen(**_):
        raise AiActionStateError("AI_ACTION_ACCESS_REVOKED")

    monkeypatch.setattr(voice, "schon_entschieden", entzogen)
    monkeypatch.setattr(voice, "vorschlag_ausfuehren", lambda **_: pytest.fail("ausgeführt"))
    session._vorschlaege.merken(_karte("fremd", "propose_server_lifecycle"))

    wert, fehler = session._vorschlag_entscheiden("reject")

    assert fehler is not None
    assert wert["error"] == fehler
    assert session._vorschlaege.leer


def test_eine_geklickte_karte_haelt_keine_meldung_mehr_auf(
    db: Session, regular_user
) -> None:
    """Die Meldungen warten, solange eine Karte wartet, und keinen Klick länger.

    Der Zusteller der Realtime-Sitzung fragt `noch_offen`. Den Klick im Panel
    erfährt die Sitzung nicht; die Datenbank weiß ihn.
    """
    from models import AiActionProposal, AiConversation

    fenster = AiConversation(id=str(uuid4()), user_id=regular_user.id, title="Stimme")
    db.add(fenster)
    db.flush()
    vorschlag = AiActionProposal(
        id=str(uuid4()), conversation_id=fenster.id, user_id=regular_user.id,
        server_id=None, tool_name="propose_file_delete", payload_encrypted="x",
        preview_json="{}", autonomous=False, correlation_id=str(uuid4()),
    )
    db.add(vorschlag)
    db.commit()
    stapel = realtime_session.voice_interactions.OffeneVorschlaege()
    stapel.merken(_karte(vorschlag.id, "propose_file_delete"))

    assert stapel.noch_offen(user_id=regular_user.id) is True

    vorschlag.status = "succeeded"
    db.commit()

    assert stapel.noch_offen(user_id=regular_user.id) is False
    # Die Karte bleibt trotzdem liegen: das nächste Ja soll „schon erledigt"
    # hören und nicht die Karte darunter treffen.
    assert not stapel.leer


def test_realtime_migration_carries_provider_and_usage_columns(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'realtime.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260828_01")
        assert "realtime_default" not in {
            column["name"] for column in inspect(engine).get_columns("ai_providers")
        }

        command.upgrade(config, "head")
        inspector = inspect(engine)
        provider_columns = {column["name"] for column in inspector.get_columns("ai_providers")}
        usage_columns = {column["name"] for column in inspector.get_columns("ai_usage_events")}
        indexes = {index["name"] for index in inspector.get_indexes("ai_providers")}
        assert {
            "realtime_default", "realtime_model", "realtime_voice",
            "realtime_reasoning_effort",
            "realtime_language", "realtime_vad_eagerness",
            *ai_provider_service.REALTIME_PREISFELDER,
        } <= provider_columns
        assert {
            "realtime_text_input_tokens", "realtime_text_output_tokens",
            "realtime_audio_input_tokens", "realtime_audio_output_tokens",
        } <= usage_columns
        assert "uq_ai_providers_realtime_default" in indexes
    finally:
        engine.dispose()
        settings.database_url = vorher
