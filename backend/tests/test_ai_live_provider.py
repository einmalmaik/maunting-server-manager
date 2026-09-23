"""GPT-Live am OpenAI-Zugang: Einstellung, Wahl des Weges, Prompt und Schema.

Die Protokolltests stehen in `test_ai_live_session.py`. Hier geht es um das,
was vor der Sitzung entschieden wird: ob ein Zugang GPT-Live sprechen darf, mit
welcher Stimme, welcher Stufe und welchem Backend-Modell, und dass der Router
dann die richtige Sitzung baut.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from config import settings
from database import Base
from routers import ai_voice
from services import ai_prompt, ai_provider_service, ai_usage_service
from services.ai_voice import live_session, realtime_session
from services.ai_voice.sprachwege import GEMINI_LIVE, OPENAI_LIVE, OPENAI_REALTIME

SCHLUESSEL = "sk-" + "test-live-not-real"
ORIGIN = {"origin": "http://localhost:3000"}


def _zugang(db: Session, **abweichend):
    werte = dict(
        name=f"Zugang {uuid4().hex[:8]}",
        provider_kind="openai",
        default_model="gpt-6-luna",
        enabled=True,
        requires_api_key=True,
        operator_api_key=SCHLUESSEL,
        realtime_default=True,
        realtime_model="gpt-live-1",
        realtime_voice="marin",
    )
    werte.update(abweichend)
    provider = ai_provider_service.create_provider(db, **werte)
    db.commit()
    db.refresh(provider)
    return provider


def _abgelehnt(db: Session, muster: str, **abweichend) -> None:
    with pytest.raises(ai_provider_service.AiProviderConfigurationError, match=muster):
        _zugang(db, **abweichend)
    db.rollback()


# ── Der Weg entscheidet sich am Modell ────────────────────────────────────


def test_gpt_live_ist_ein_zweiter_weg_am_selben_openai_zugang(db: Session) -> None:
    provider = _zugang(db, realtime_voice="beacon", realtime_minute_price_micro_usd=50_000)

    assert ai_provider_service.sprachweg(provider) is OPENAI_LIVE
    assert ai_provider_service.realtime_zugang(db).id == provider.id
    # Ohne eigenes Backend-Modell denkt das Standardmodell des Zugangs.
    assert ai_provider_service.backend_modell(provider) == "gpt-6-luna"
    # Abgerechnet nach Minuten und nach den Token des Backends — Audio-Token
    # gibt es auf diesem Weg nicht.
    assert ai_provider_service.realtime_preisfelder(provider) == (
        "realtime_text_input_price_micro_usd_per_million",
        "realtime_text_output_price_micro_usd_per_million",
        ai_provider_service.REALTIME_MINUTENPREIS,
    )

    realtime = _zugang(db, realtime_model="gpt-realtime-2")
    assert ai_provider_service.sprachweg(realtime) is OPENAI_REALTIME
    assert ai_provider_service.realtime_preisfelder(realtime) == ai_provider_service.REALTIME_PREISFELDER


def test_stimmen_und_stufen_gelten_je_weg(db: Session) -> None:
    # „beacon" gibt es nur bei GPT-Live, „Puck" nur bei Gemini.
    _abgelehnt(db, "Unbekannte GPT-Live-Stimme", realtime_voice="Puck")
    _abgelehnt(db, "Unbekannte OpenAI-Realtime-Stimme", realtime_model="gpt-realtime-2", realtime_voice="beacon")
    # `delegation.responses.reasoning.effort`: none bis xhigh, kein max.
    _abgelehnt(db, "Unbekannte Denkstufe für GPT-Live", realtime_reasoning_effort="max")
    for stufe in OPENAI_LIVE.denkstufen:
        assert _zugang(db, realtime_reasoning_effort=stufe).realtime_reasoning_effort == stufe
    # Nachsichtig gelesen, streng gespeichert.
    assert _zugang(db, realtime_voice="Beacon").realtime_voice == "beacon"


def test_gpt_live_braucht_ein_modell_das_hinter_der_stimme_denkt(db: Session) -> None:
    _abgelehnt(db, "Backend-Modell", default_model=None)

    provider = _zugang(db, default_model=None, realtime_backend_model="gpt-6-luna")
    assert ai_provider_service.backend_modell(provider) == "gpt-6-luna"
    # Das eigene Feld schlägt das Standardmodell.
    provider = _zugang(db, realtime_backend_model="gpt-5.6-terra")
    assert ai_provider_service.backend_modell(provider) == "gpt-5.6-terra"


def test_was_nur_den_namen_teilt_ist_kein_sprachweg(db: Session) -> None:
    # gpt-live-transcribe führt `v1/live/sessions` laut seiner Modellseite
    # ausdrücklich nicht.
    _abgelehnt(db, "kein OpenAI-Realtime-Modell und kein GPT-Live-Modell", realtime_model="gpt-live-transcribe")


def test_azure_traegt_gpt_live_nicht(db: Session) -> None:
    _abgelehnt(
        db,
        "kein OpenAI-Realtime-Modell",
        provider_kind="azure_openai",
        azure_resource_name="mein-ai-hub",
    )


# ── Was die Oberfläche bekommt ────────────────────────────────────────────


def test_die_anbieterliste_nennt_die_wege_mit_ihren_feldern(client: TestClient, owner_cookies: dict) -> None:
    antwort = client.get("/api/ai/settings/provider-kinds", cookies=owner_cookies)
    assert antwort.status_code == 200
    arten = {art["kind"]: art for art in antwort.json()}

    # Dieselben Felder, nach denen das Backend urteilt — die Oberfläche führt
    # keine zweite Liste.
    assert arten["openai"]["sprachwege"] == [OPENAI_REALTIME.als_dict(), OPENAI_LIVE.als_dict()]
    assert arten["google"]["sprachwege"] == [GEMINI_LIVE.als_dict()]
    assert arten["azure_openai"]["sprachwege"] == [OPENAI_REALTIME.als_dict()]
    assert arten["openrouter"]["sprachwege"] == []
    for art in arten.values():
        assert art["realtime_tauglich"] is bool(art["sprachwege"])


def test_die_sprachkonfiguration_nennt_weg_und_backend_modell(
    client: TestClient, owner_cookies: dict, db: Session
) -> None:
    _zugang(db, realtime_backend_model="gpt-5.6-terra")

    daten = client.get("/api/ai/voice/config", cookies=owner_cookies).json()

    assert daten["available"] is True
    assert daten["mode"] == "openai_live"
    assert daten["model"] == "gpt-live-1"
    assert daten["backend_model"] == "gpt-5.6-terra"


def test_realtime_meldet_kein_backend_modell(client: TestClient, owner_cookies: dict, db: Session) -> None:
    _zugang(db, realtime_model="gpt-realtime-2")

    daten = client.get("/api/ai/voice/config", cookies=owner_cookies).json()

    assert daten["mode"] == "openai_realtime"
    assert daten["backend_model"] is None


def test_der_sprachsocket_baut_fuer_gpt_live_die_live_sitzung(
    client: TestClient, owner_cookies: dict, db: Session, monkeypatch
) -> None:
    provider = _zugang(db)
    gebaut: list[str] = []

    def kein_realtime(*_args, **_kwargs):
        raise AssertionError("Der Realtime-Weg darf bei GPT-Live nicht gewählt werden")

    class StilleLiveSitzung:
        def __init__(self, _websocket, **kwargs):
            gebaut.append(kwargs["vorbereitung"].backend_model)

        async def fuehren(self):
            return None

    monkeypatch.setattr(ai_voice, "realtime_vorbereiten", kein_realtime)
    monkeypatch.setattr(ai_voice, "RealtimeSitzung", kein_realtime)
    monkeypatch.setattr(
        ai_voice,
        "live_vorbereiten",
        lambda *_args, **_kwargs: live_session.LiveVorbereitung(provider_id=provider.id, backend_model="gpt-6-luna"),
    )
    monkeypatch.setattr(ai_voice, "LiveSitzung", StilleLiveSitzung)

    with client.websocket_connect("/api/ai/voice/ws", cookies=owner_cookies, headers=ORIGIN):
        pass

    assert gebaut == ["gpt-6-luna"]


# ── Sitzungsvertrag ───────────────────────────────────────────────────────


def test_vorbereiten_trennt_stimme_und_backend(db: Session, regular_user, monkeypatch) -> None:
    provider = _zugang(db, realtime_reasoning_effort="high", realtime_language="de")
    rollen: list[str] = []
    monkeypatch.setattr(
        realtime_session.ai_action_service,
        "angebotene_werkzeuge",
        lambda *_args: {"web_search", "worker_start"},
    )
    monkeypatch.setattr(ai_provider_service, "resolve_api_key", lambda *_args: SCHLUESSEL)
    monkeypatch.setattr(
        ai_prompt,
        "build",
        lambda **kwargs: rollen.append(kwargs["rolle"]) or "PANELPROMPT",
    )

    v = live_session.vorbereiten(db, provider=provider, user=regular_user, herkunft="panel")

    assert rollen == ["live"]
    assert v.model == "gpt-live-1"
    assert v.backend_model == "gpt-6-luna"
    # Die Stufe gehört dem Backend, die Stimme hat keine.
    assert v.reasoning_effort == "high"
    namen = {tool["name"] for tool in v.tools}
    # Dieselbe Werkzeugwahl wie bei Realtime (`angebotene_werkzeuge`): die
    # Sprachsteuerung ja, Hintergrund-Worker nein.
    assert {"voice_resolve_latest_proposal", "voice_set_region_view"} <= namen
    assert "worker_start" not in namen
    # Die Stimme bekommt die Liste, das Backend die Schemas und den Panelprompt.
    assert "- voice_resolve_latest_proposal" in v.instructions
    assert "PANELPROMPT" not in v.instructions
    assert "## Task instructions\nPANELPROMPT" in v.backend_instructions


def test_die_rolle_live_spricht_nicht_selbst() -> None:
    live = ai_prompt.build(gesprochen=True, rolle="live")
    realtime = ai_prompt.build(gesprochen=True, rolle="realtime")

    assert live.endswith(ai_prompt.HINTER_DER_STIMME)
    assert realtime.endswith(ai_prompt.GESPROCHEN)
    # Ansagen und Codeblöcke setzen voraus, dass der Mensch diesen Text
    # bekommt — hinter GPT-Live bekommt ihn die Stimme.
    assert ai_prompt.MITREDEN not in live
    assert ai_prompt.BELEGE not in live
    assert ai_prompt.GESPROCHEN not in live
    # Zustimmung und Vorschläge gelten auf beiden Wegen gleich.
    assert ai_prompt.ZUSTIMMUNG_GESPROCHEN in live
    assert ai_prompt.ZUSTIMMUNG_GESPROCHEN in realtime
    assert realtime.count(ai_prompt.ZUSTIMMUNG_GESPROCHEN) == 1


# ── Abrechnung und Schema ─────────────────────────────────────────────────


def test_die_dauer_ist_keine_weitere_anfrage(db: Session, owner_user) -> None:
    event = ai_usage_service.reserve_ai_usage(
        db, owner_user, request_id=uuid4(), estimated_tokens=0, model="gpt-live-1", realtime=True,
    )
    db.commit()
    for anfragen, kosten in ((1, 12_500), (0, 5_000), (0, 5_000)):
        ai_usage_service.realtime_verbrauch_ergaenzen(
            db, event_id=event.id, text_input=0, text_output=0, audio_input=0,
            audio_output=0, cost_microunits=kosten, anfragen=anfragen,
        )
        db.commit()

    db.refresh(event)
    assert event.provider_requests == 1
    assert event.accounted_cost_microunits == 22_500


def test_gpt_live_migration_traegt_backend_modell_und_minutenpreis(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'live.db'}"
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    neu = {"realtime_backend_model", "realtime_minute_price_micro_usd"}
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260922_01")
        assert neu.isdisjoint(column["name"] for column in inspect(engine).get_columns("ai_providers"))

        command.upgrade(config, "head")
        spalten = {column["name"]: column for column in inspect(engine).get_columns("ai_providers")}
        assert neu <= set(spalten)
        assert all(spalten[name]["nullable"] for name in neu)
    finally:
        engine.dispose()
        settings.database_url = vorher
