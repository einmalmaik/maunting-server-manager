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
        "interrupt_response": True,
    }
    assert "transcription" not in config["audio"]["input"]
    assert config["max_output_tokens"] == 512


def test_realtime_never_offers_background_workers(db: Session, regular_user, monkeypatch) -> None:
    provider = _realtime(db, "Realtime ohne Worker")
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
    assert namen.isdisjoint({"worker_start", "worker_cancel", "worker_antwort"})
    assert "keine Hintergrund-Worker starten" in vorbereiten.instructions
    assert rollen == ["voll"]


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

    assert panel.sent[1] == {"art": "werkzeug", "name": "web_search", "tool_name": "web_search", "failed": True}
    output_frame = json.loads(sideband.sent[0])
    assert json.loads(output_frame["item"]["output"]) == {"error": "TOOL_TIMEOUT"}
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

    await session._tool_ausfuehren({
        "call_id": "call_region",
        "name": "analyze_region",
        "arguments": '{"location":"Berlin"}',
    })

    first_output = json.loads(sideband.sent[0])
    assert json.loads(first_output["item"]["output"]) == initial
    assert panel.sent[1]["geo_analysis"] == initial

    session._response_aktiv = False
    await asyncio.gather(*tuple(session._region_tasks))
    assert panel.sent[2]["geo_analysis"] == complete
    assert json.loads(sideband.sent[-1]) == {"type": "response.create"}


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
        lambda **werte: (aufrufe.append(werte) is None, None),
    )
    session._offener_vorschlag = "proposal-current"
    wert, fehler = session._vorschlag_entscheiden("confirm")
    assert fehler is None
    assert wert == {"status": "confirmed"}
    assert aufrufe == [{"user_id": 7, "kennung": "proposal-current"}]
    assert session._offener_vorschlag is None
    _, zweiter_fehler = session._vorschlag_entscheiden("confirm")
    assert zweiter_fehler is not None


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
