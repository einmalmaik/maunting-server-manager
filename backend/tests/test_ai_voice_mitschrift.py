"""Gedächtnis v2, Stufe 2: was in einer Sprachsitzung gesagt wurde.

Die Stimme merkt sich nichts mehr selbst. Was gesagt wurde, geht nach dem Ende
der Sitzung an den Gedächtnisschreiber (`ai_gedaechtnis_schreiber.
aus_mitschrift`), auf allen drei Wegen: OpenAI-Realtime, GPT-Live mit
Backend-Modell und Gemini-Live. Festgehalten wird hier:

- Mitgeschrieben wird nur mit ``ai.memory.use`` und eingeschaltetem
  Gedächtnis. Sonst entsteht keine Mitschrift, und Realtime bekommt keine
  kostenpflichtige Abschrift.
- Die Zeilen stehen in der Reihenfolge des Gesprächs, auch wenn ein Anbieter
  die Abschrift später oder durcheinander liefert.
- Was die Abschrift bei Realtime kostet, wird gebucht, als Gedächtnis.

Die Gegenstellen sind Attrappen, ihre Ereignisse haben die Form aus den
Referenzen der Anbieter. Der Schreiber selbst ist in
`test_ai_gedaechtnis_schreiber` geprüft und wird hier nur abgefangen. Alle
Personen sind erfunden.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiUsageEvent, Role, RolePermission, User
from services import ai_gedaechtnis_schreiber, ai_memory_service, ai_prompt, ai_provider_service, ai_usage_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_voice import gemini_live_session, live_session, mitschrift, realtime_session
from services.ai_voice.mitschrift import Mitschrift
from services.role_service import set_user_roles
from tests import test_ai_live_session as gpt_live

# Zusammengesetzt, nie als Literal: das Repo ist öffentlich.
SCHLUESSEL = "sk-" + "test-mitschrift-not-real"
ABSCHRIFTMODELL = "gpt-4o-mini-transcribe"
TOKENPREIS = 2_000_000
REISE = "Ich fliege nächste Woche nach Singapur."
GRUSS = "Viel Spaß in Singapur!"


def _abfangen(monkeypatch) -> list[dict]:
    """Was an den Schreiber geht."""
    abgegeben: list[dict] = []

    def aus_mitschrift(**kwargs):
        abgegeben.append(kwargs)
        return True

    monkeypatch.setattr(ai_gedaechtnis_schreiber, "aus_mitschrift", aus_mitschrift)
    return abgegeben


def _texte(zeilen) -> list[tuple[str, str]]:
    return [(sprecher, text) for sprecher, text, _zeit in zeilen]


def _gedaechtnisrecht(db: Session, user: User) -> None:
    """Nur ``ai.memory.use``, ohne Grenzen — ersetzt die Rollen des Kontos."""
    role = Role(name=f"mitschrift-{user.id}-{uuid4().hex[:6]}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    set_role_limit(db, role.id, {feld: None for feld in LIMIT_FIELDS})
    set_user_roles(db, user, [role.id])
    db.commit()


# ── Die Mitschrift selbst ─────────────────────────────────────────────────


def test_ohne_einwilligung_entsteht_keine_mitschrift(monkeypatch) -> None:
    abgegeben = _abfangen(monkeypatch)
    m = Mitschrift(False)
    m.stueck("ich", "Merk dir: Jonas fährt nach Singapur.")
    m.zeile("ki", "Gern.")
    m.vormerken("item_1", "ich")
    m.einsetzen("item_1", "ich", "Noch etwas.")
    m.zug("ich", "Und das.")

    assert m.zeilen == []
    assert m.abgeben(user_id=1, provider_id=None) is False
    assert abgegeben == []


def test_stuecke_desselben_sprechers_bilden_eine_zeile() -> None:
    m = Mitschrift(True)
    for sprecher, stueck in (
        ("ich", "Ich fliege nächste Woche "),
        ("ich", "nach Singapur."),
        ("ki", GRUSS),
        ("ich", "Danke."),
    ):
        m.stueck(sprecher, stueck)

    assert _texte(m.zeilen) == [("ich", REISE), ("ki", GRUSS), ("ich", "Danke.")]


def test_eine_spaete_abschrift_steht_an_ihrem_platz() -> None:
    """Realtime: die Abschrift einer Äußerung kann nach der Antwort kommen."""
    m = Mitschrift(True)
    m.vormerken("item_1", "ich")
    m.vormerken("msg_1", "ki")
    m.einsetzen("msg_1", "ki", GRUSS)
    m.einsetzen("item_1", "ich", REISE)
    # Dieselbe Antwort noch einmal (aus `response.done`): die erste Fassung gilt.
    m.einsetzen("msg_1", "ki", "Anders formuliert.")
    # Eine Abschrift, die scheiterte, hinterlässt keine leere Zeile.
    m.vormerken("item_2", "ich")

    assert _texte(m.zeilen) == [("ich", REISE), ("ki", GRUSS)]


def test_ein_zug_legt_erst_den_menschen_dann_die_ki_ab() -> None:
    """Gemini: die Abschrift des Menschen kommt „ohne zugesicherte Reihenfolge“."""
    m = Mitschrift(True)
    m.zug("ki", "Es ist")
    m.zug("ich", "Wie spät ist es")
    m.zug("ki", " kurz nach drei.")
    m.zug("ich", " in Singapur?")
    m.zug_ende()

    assert _texte(m.zeilen) == [("ich", "Wie spät ist es in Singapur?"), ("ki", "Es ist kurz nach drei.")]


def test_der_deckel_schuetzt_den_speicher(monkeypatch) -> None:
    monkeypatch.setattr(mitschrift, "MAX_ZEICHEN", 30)
    m = Mitschrift(True)
    m.zeile("ich", "Ich mag Kaffee ohne Zucker.")
    m.zeile("ki", "Notiert, danke.")
    m.zeile("ich", "Ok")

    assert _texte(m.zeilen) == [("ich", "Ich mag Kaffee ohne Zucker.")]


def test_abgeben_reicht_die_zeilen_genau_einmal_weiter(monkeypatch) -> None:
    abgegeben = _abfangen(monkeypatch)
    m = Mitschrift(True)
    m.vormerken("item_9", "ich")
    m.zug("ich", "Merk dir: Jonas trinkt keinen Kaffee.")

    assert m.abgeben(user_id=7, provider_id=3) is True
    assert m.abgeben(user_id=7, provider_id=3) is False

    assert len(abgegeben) == 1
    assert abgegeben[0]["user_id"] == 7
    assert abgegeben[0]["provider_id"] == 3
    assert abgegeben[0]["beginn"] == m.beginn
    # Der offene Zug wird beim Abgeben abgeschlossen, die leere Vormerkung fällt weg.
    assert _texte(abgegeben[0]["zeilen"]) == [("ich", "Merk dir: Jonas trinkt keinen Kaffee.")]


def test_ein_fehler_beim_abgeben_stoert_das_ende_der_sitzung_nicht(monkeypatch) -> None:
    def kaputt(**_kwargs):
        raise RuntimeError("Schreiber nicht erreichbar")

    monkeypatch.setattr(ai_gedaechtnis_schreiber, "aus_mitschrift", kaputt)
    m = Mitschrift(True)
    m.zeile("ich", "Hallo")

    assert m.abgeben(user_id=1, provider_id=None) is False


# ── Wer mitgeschrieben wird ───────────────────────────────────────────────


def test_mitgeschrieben_wird_nur_mit_recht_und_schalter(db: Session, regular_user: User) -> None:
    set_user_roles(db, regular_user, [])
    db.commit()
    assert realtime_session.mitschreiben(db, regular_user) is False

    # Eingeschaltet, aber ohne `ai.memory.use`: nichts.
    ai_memory_service.set_preference(db, regular_user, True)
    assert realtime_session.mitschreiben(db, regular_user) is False

    _gedaechtnisrecht(db, regular_user)
    assert realtime_session.mitschreiben(db, regular_user) is True

    ai_memory_service.set_preference(db, regular_user, False)
    assert realtime_session.mitschreiben(db, regular_user) is False


def _realtime_zugang(db: Session, **abweichend):
    werte = dict(
        name=f"Realtime {uuid4().hex[:8]}",
        provider_kind="openai",
        default_model="gpt-6-luna",
        enabled=True,
        requires_api_key=True,
        operator_api_key=SCHLUESSEL,
        realtime_default=True,
        realtime_model="gpt-realtime",
        realtime_voice="marin",
        transcription_model=ABSCHRIFTMODELL,
        token_price_micro_usd_per_million=TOKENPREIS,
    )
    werte.update(abweichend)
    provider = ai_provider_service.create_provider(db, **werte)
    db.commit()
    db.refresh(provider)
    return provider


def _ohne_beiwerk(monkeypatch) -> None:
    monkeypatch.setattr(realtime_session.ai_action_service, "angebotene_werkzeuge", lambda *_a: {"web_search"})
    monkeypatch.setattr(ai_provider_service, "resolve_api_key", lambda *_a: SCHLUESSEL)
    monkeypatch.setattr(ai_prompt, "build", lambda **_k: "Direkt antworten")


def _realtime_vorbereiten(db: Session, user: User, provider) -> realtime_session.RealtimeVorbereitung:
    v = realtime_session.vorbereiten(db, provider=provider, user=user, herkunft="panel")
    # Die Reservierung der Sitzung gleich wieder schliessen: der Test baut
    # mehrere hintereinander, eine echte Sitzung täte das am Ende.
    ai_usage_service.realtime_sitzung_abschliessen(db, v.usage_event_id)
    db.commit()
    return v


def test_realtime_hoert_den_menschen_nur_mit_einwilligung_und_abschriftmodell(
    db: Session, regular_user: User, monkeypatch
) -> None:
    _ohne_beiwerk(monkeypatch)
    _gedaechtnisrecht(db, regular_user)
    provider = _realtime_zugang(db)

    aus = _realtime_vorbereiten(db, regular_user, provider)
    assert aus.mitschreiben is False
    assert "transcription" not in realtime_session._session_config(aus)["audio"]["input"]
    # Und die Stimme weiß es: ohne Schalter bleibt aus der Sitzung nichts.
    assert "aus einem Sprachgespräch wird nichts gemerkt" in aus.instructions

    ai_memory_service.set_preference(db, regular_user, True)
    an = _realtime_vorbereiten(db, regular_user, provider)
    assert an.mitschreiben is True
    assert an.mitschrift_modell == ABSCHRIFTMODELL
    assert realtime_session._session_config(an)["audio"]["input"]["transcription"] == {"model": ABSCHRIFTMODELL}
    assert "Gedächtnis: eingeschaltet." in an.instructions

    # Ohne Abschriftmodell am Zugang hört Realtime den Menschen nicht mit. Eine
    # Mitschrift nur der Antworten gäbe dem Schreiber keinen einzigen Beleg.
    provider.transcription_enabled = False
    db.commit()
    ohne = _realtime_vorbereiten(db, regular_user, provider)
    assert ohne.mitschrift_modell == ""
    assert "transcription" not in realtime_session._session_config(ohne)["audio"]["input"]
    # Die Lage sagt es, sonst verspräche die Stimme, was niemand aufschreibt.
    assert "Gedächtnis: in diesem Sprachgespräch aus" in ohne.instructions
    assert "Gedächtnis: eingeschaltet." not in ohne.instructions
    sitzung = realtime_session.RealtimeSitzung(
        MagicMock(), vorbereitung=ohne, user_id=regular_user.id,
        http_client=None, herkunft="panel", familie=None,
    )
    assert sitzung._mitschrift.aktiv is False


def test_gpt_live_schreibt_mit_einwilligung_ohne_abschriftmodell_mit(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """GPT-Live schickt beide Abschriften ohnehin; ein Modell braucht es nicht."""
    _ohne_beiwerk(monkeypatch)
    _gedaechtnisrecht(db, regular_user)
    ai_memory_service.set_preference(db, regular_user, True)
    provider = _realtime_zugang(db, realtime_model="gpt-live-1", transcription_model=None)

    v = live_session.vorbereiten(db, provider=provider, user=regular_user, herkunft="panel")

    assert v.mitschreiben is True
    assert "Gedächtnis: eingeschaltet." in v.backend_instructions
    sitzung = live_session.LiveSitzung(
        MagicMock(), vorbereitung=v, user_id=regular_user.id,
        http_client=None, herkunft="panel", familie=None,
    )
    assert sitzung._mitschrift.aktiv is True


def test_gemini_braucht_fuer_die_mitschrift_kein_abschriftmodell(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """Gemini-Live liefert die Abschrift beider Seiten selbst; die Lage sagt „eingeschaltet“."""
    from services.ai_voice.sprachwege import GEMINI_LIVE

    _ohne_beiwerk(monkeypatch)
    _gedaechtnisrecht(db, regular_user)
    ai_memory_service.set_preference(db, regular_user, True)
    provider = _realtime_zugang(db, transcription_model=None)
    monkeypatch.setattr(ai_provider_service, "sprachweg", lambda _provider: GEMINI_LIVE)

    v = _realtime_vorbereiten(db, regular_user, provider)

    assert (v.mitschreiben, v.mitschrift_modell) == (True, "")
    assert "Gedächtnis: eingeschaltet." in v.instructions


# ── Die drei Wege ─────────────────────────────────────────────────────────


class _Panel:
    def __init__(self, *eingang: dict) -> None:
        self.gesendet: list[dict] = []
        self._eingang: asyncio.Queue = asyncio.Queue()
        for nachricht in eingang:
            self._eingang.put_nowait(nachricht)

    async def send_json(self, daten: dict) -> None:
        self.gesendet.append(daten)

    async def receive_json(self) -> dict:
        return await self._eingang.get()


class _Seitenkanal:
    """Spielt Ereignisse ab wie das Sideband von OpenAI und endet dann."""

    def __init__(self, ereignisse: list[dict]) -> None:
        self._ereignisse = [json.dumps(e) for e in ereignisse]
        self.gesendet: list[dict] = []

    async def send(self, roh: str) -> None:
        self.gesendet.append(json.loads(roh))

    def __aiter__(self):
        return self._fluss()

    async def _fluss(self):
        for roh in self._ereignisse:
            yield roh

    async def close(self) -> None:
        pass


def _realtime_hin_und_her() -> list[dict]:
    """Ein gesprochenes Hin und Her, wie Realtime es meldet — die Abschrift der
    Äußerung kommt erst nach der Abschrift der Antwort."""
    return [
        {"type": "input_audio_buffer.speech_started", "item_id": "item_1"},
        {"type": "input_audio_buffer.speech_stopped", "item_id": "item_1"},
        {"type": "input_audio_buffer.committed", "previous_item_id": None, "item_id": "item_1"},
        {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
        {"type": "response.output_item.added", "response_id": "resp_1", "output_index": 0,
         "item": {"id": "msg_1", "type": "message", "role": "assistant", "content": []}},
        {"type": "output_audio_buffer.started", "response_id": "resp_1"},
        {"type": "response.output_audio_transcript.done", "response_id": "resp_1", "item_id": "msg_1",
         "output_index": 0, "content_index": 0, "transcript": GRUSS},
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "item_1",
         "content_index": 0, "transcript": REISE,
         "usage": {"type": "tokens", "input_tokens": 40, "output_tokens": 12, "total_tokens": 52,
                   "input_token_details": {"text_tokens": 0, "audio_tokens": 40}}},
        {"type": "response.done", "response": {"id": "resp_1", "status": "completed", "output": [
            {"id": "msg_1", "type": "message", "role": "assistant",
             "content": [{"type": "output_audio", "transcript": GRUSS}]},
        ]}},
    ]


async def _realtime_fuehren(db: Session, user: User, monkeypatch, *, mitschreiben: bool):
    provider = _realtime_zugang(db)
    event = ai_usage_service.reserve_ai_usage(
        db, user, request_id=uuid4(), estimated_tokens=0,
        provider_id=provider.id, model="gpt-realtime", realtime=True,
    )
    db.commit()
    v = realtime_session.RealtimeVorbereitung(
        provider_id=provider.id, model="gpt-realtime", voice="marin", api_key=SCHLUESSEL,
        conversation_id="conversation-1", usage_event_id=event.id,
        mitschreiben=mitschreiben, mitschrift_modell=ABSCHRIFTMODELL,
    )
    sitzung = realtime_session.RealtimeSitzung(
        _Panel({"art": "webrtc_offer", "sdp": "v=0\r\nangebot"}), vorbereitung=v, user_id=user.id,
        http_client=None, herkunft="panel", familie=None,
    )

    ereignisse = _realtime_hin_und_her()
    if not mitschreiben:
        # Ohne bestellte Abschrift (`_session_config`) kommt auch keine.
        ereignisse = [e for e in ereignisse if not e["type"].startswith("conversation.item.input_audio")]

    async def handschlag(_sdp: str) -> str:
        sitzung._sideband = _Seitenkanal(ereignisse)
        return "v=0\r\nantwort"

    monkeypatch.setattr(sitzung, "_handshake", handschlag)
    monkeypatch.setattr(sitzung, "_verbrauch", lambda _event: None)
    await asyncio.wait_for(sitzung.fuehren(), timeout=10)
    return provider


def _gedaechtnisbuchungen(db: Session, user: User) -> list[AiUsageEvent]:
    db.expire_all()
    return db.query(AiUsageEvent).filter(
        AiUsageEvent.user_id == user.id, AiUsageEvent.zweck == "gedaechtnis"
    ).all()


@pytest.mark.asyncio
async def test_realtime_ordnet_die_spaete_abschrift_ein_und_bucht_sie(
    db: Session, regular_user: User, monkeypatch
) -> None:
    _gedaechtnisrecht(db, regular_user)
    abgegeben = _abfangen(monkeypatch)

    provider = await _realtime_fuehren(db, regular_user, monkeypatch, mitschreiben=True)

    assert len(abgegeben) == 1
    assert abgegeben[0]["provider_id"] == provider.id
    assert abgegeben[0]["user_id"] == regular_user.id
    assert _texte(abgegeben[0]["zeilen"]) == [("ich", REISE), ("ki", GRUSS)]
    # Die Abschrift kostet nach dem Preis des Zugangs — eine Zeile je Sitzung,
    # als Gedächtnis, damit sie in der Übersicht nicht als Gespräch erscheint.
    [zeile] = _gedaechtnisbuchungen(db, regular_user)
    assert zeile.status == "completed"
    assert zeile.model == ABSCHRIFTMODELL
    assert zeile.provider_id == provider.id
    assert zeile.accounted_tokens == 52
    assert (zeile.prompt_tokens, zeile.completion_tokens) == (40, 12)
    assert zeile.accounted_cost_microunits == 52 * TOKENPREIS // 1_000_000
    assert zeile.provider_requests == 1


@pytest.mark.asyncio
async def test_realtime_ohne_einwilligung_gibt_nichts_ab_und_bucht_nichts(
    db: Session, regular_user: User, monkeypatch
) -> None:
    _gedaechtnisrecht(db, regular_user)
    abgegeben = _abfangen(monkeypatch)

    await _realtime_fuehren(db, regular_user, monkeypatch, mitschreiben=False)

    assert abgegeben == []
    assert _gedaechtnisbuchungen(db, regular_user) == []


@pytest.mark.asyncio
async def test_gpt_live_gibt_beide_seiten_am_ende_ab(db: Session, owner_user: User, monkeypatch) -> None:
    abgegeben = _abfangen(monkeypatch)
    panel = gpt_live.Panel({"art": "webrtc_offer", "sdp": gpt_live.ANGEBOT})
    sideband = gpt_live.Sideband()
    gpt_live._verbindung(monkeypatch, sideband)
    vorbereitung = gpt_live._vorbereitung(db, owner_user, mitschreiben=True)
    sitzung = gpt_live._sitzung(vorbereitung, panel=panel)
    sideband.liefern(
        {"type": "session.input_transcript.delta", "delta": "Ich fliege nächste Woche ", "start_ms": 0, "end_ms": 900},
        {"type": "session.input_transcript.delta", "delta": "nach Singapur.", "start_ms": 900, "end_ms": 1500},
        {"type": "session.output_transcript.delta", "delta": "Viel Spaß ", "start_ms": 1600, "end_ms": 2000},
        {"type": "session.output_transcript.delta", "delta": "in Singapur!", "start_ms": 2000, "end_ms": 2600},
        gpt_live._geschlossen("client_closed", 20),
    )

    await asyncio.wait_for(sitzung.fuehren(), timeout=10)

    assert len(abgegeben) == 1
    assert abgegeben[0]["provider_id"] == vorbereitung.provider_id
    assert _texte(abgegeben[0]["zeilen"]) == [("ich", REISE), ("ki", GRUSS)]


@pytest.mark.asyncio
async def test_gpt_live_ohne_einwilligung_gibt_nichts_ab(db: Session, owner_user: User, monkeypatch) -> None:
    abgegeben = _abfangen(monkeypatch)
    sideband = gpt_live.Sideband()
    gpt_live._verbindung(monkeypatch, sideband)
    sitzung = gpt_live._sitzung(
        gpt_live._vorbereitung(db, owner_user),
        panel=gpt_live.Panel({"art": "webrtc_offer", "sdp": gpt_live.ANGEBOT}),
    )
    sideband.liefern(
        {"type": "session.input_transcript.delta", "delta": REISE},
        gpt_live._geschlossen("client_closed", 20),
    )

    await asyncio.wait_for(sitzung.fuehren(), timeout=10)

    assert abgegeben == []


class _Google:
    """Googles Live-Socket: nimmt das Setup an, spielt Ereignisse ab, endet."""

    def __init__(self, *ereignisse: dict) -> None:
        self._ereignisse = [json.dumps(e) for e in ereignisse]
        self.gesendet: list[dict] = []

    async def send(self, roh: str) -> None:
        self.gesendet.append(json.loads(roh))

    async def close(self) -> None:
        pass

    def __aiter__(self):
        return self._fluss()

    async def _fluss(self):
        for roh in self._ereignisse:
            yield roh


async def _gemini_fuehren(monkeypatch, google: _Google, *, mitschreiben: bool) -> None:
    v = realtime_session.RealtimeVorbereitung(
        provider_id=5, provider_kind="google", model="gemini-2.5-flash", voice="Puck",
        api_key="AIza" + "SyTestKey", mitschreiben=mitschreiben,
    )
    panel = MagicMock()
    panel.send_json = AsyncMock()

    async def warten():
        await asyncio.Event().wait()

    panel.receive = warten
    sitzung = gemini_live_session.GeminiLiveSitzung(
        websocket=panel, vorbereitung=v, user_id=7, http_client=MagicMock(),
    )

    async def verbinden(_url, **_kwargs):
        return google

    monkeypatch.setattr(gemini_live_session.websockets, "connect", verbinden)
    monkeypatch.setattr(sitzung, "_abschliessen", lambda: None)
    await asyncio.wait_for(sitzung.fuehren(), timeout=10)


@pytest.mark.asyncio
async def test_gemini_bestellt_die_abschrift_und_ordnet_sie_je_zug(monkeypatch) -> None:
    abgegeben = _abfangen(monkeypatch)
    google = _Google(
        {"setupComplete": {}},
        {"serverContent": {"outputTranscription": {"text": "Es ist"}}},
        {"serverContent": {"inputTranscription": {"text": "Wie spät ist es"}}},
        {"serverContent": {"outputTranscription": {"text": " kurz nach drei."}}},
        {"serverContent": {"inputTranscription": {"text": " in Singapur?"}}},
        {"serverContent": {"turnComplete": True}},
        {"serverContent": {"inputTranscription": {"text": "Danke!"}}},
    )

    await _gemini_fuehren(monkeypatch, google, mitschreiben=True)

    setup = google.gesendet[0]["setup"]
    # Felder von `setup` selbst, nicht von `generationConfig`.
    assert setup["inputAudioTranscription"] == {}
    assert setup["outputAudioTranscription"] == {}
    assert "inputAudioTranscription" not in setup["generationConfig"]
    assert len(abgegeben) == 1
    assert abgegeben[0]["provider_id"] == 5
    # Der angefangene Zug am Ende geht mit.
    assert _texte(abgegeben[0]["zeilen"]) == [
        ("ich", "Wie spät ist es in Singapur?"),
        ("ki", "Es ist kurz nach drei."),
        ("ich", "Danke!"),
    ]


@pytest.mark.asyncio
async def test_gemini_ohne_einwilligung_bestellt_keine_abschrift(monkeypatch) -> None:
    abgegeben = _abfangen(monkeypatch)
    google = _Google(
        {"setupComplete": {}},
        {"serverContent": {"inputTranscription": {"text": REISE}}},
        {"serverContent": {"turnComplete": True}},
    )

    await _gemini_fuehren(monkeypatch, google, mitschreiben=False)

    setup = google.gesendet[0]["setup"]
    assert "inputAudioTranscription" not in setup
    assert "outputAudioTranscription" not in setup
    assert abgegeben == []
