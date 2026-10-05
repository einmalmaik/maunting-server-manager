"""Computer-Use per Stimme — übergeben, sofort antworten, Ergebnis nachreichen.

Bis zum 05.10.2026 ging es gar nicht: die Sprachsitzung schnitt die
Desktop-Werkzeuge aus dem Angebot, und gerufen endeten sie im benannten
Fehlschlag von `server_tools`. Eine Sitzung kann nicht parken wie ein Lauf im
Chat; der Mensch vor der Karte braucht länger als die Werkzeugfrist. Siehe
`services/ai_voice/desktop_auftraege.py`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy.orm import Session

from models import AiRun, DesktopJob, User
from services import ai_run_service, desktop_job_service
from services.ai_stream.types import BILDFELD, KEIN_BLICK_GRUND
from services.ai_voice import desktop_auftraege, realtime_session
from services.ai_voice.desktop_auftraege import SprachAuftraege
from services.openai_compatible_adapter import ProviderToolCall

#: Kein echtes Foto — ein kurzer base64-Wert genügt, das Format prüft hier
#: niemand.
BILD = "QUJD"


@pytest.fixture
def angebot(monkeypatch):
    """Was der Benutzer darf — der Rechtepfad selbst ist anderswo geprüft."""
    werkzeuge = {"desktop_system", "desktop_steuern", "desktop_launch_app"}
    monkeypatch.setattr(
        "services.ai_action_service.angebotene_werkzeuge", lambda *_args: set(werkzeuge)
    )
    return werkzeuge


def _gespraech(db: Session, user: User) -> str:
    from models import AiConversation

    gespraech = AiConversation(id=f"stimme-{user.id}", user_id=user.id, kind="primary", title="Stimme")
    db.add(gespraech)
    db.commit()
    return gespraech.id


def _auftraege(db: Session, user: User, herkunft: str = "desktop") -> SprachAuftraege:
    return SprachAuftraege(
        user_id=user.id,
        conversation_id=_gespraech(db, user),
        herkunft=herkunft,
        familie="familie-1",
    )


def _blick(kennung: str = "call-1") -> ProviderToolCall:
    return ProviderToolCall(id=kennung, name="desktop_system", arguments={"aktion": "bildschirm"})


class TestUebergeben:
    def test_legt_einen_auftrag_an_und_antwortet_sofort(
        self, db: Session, regular_user: User, angebot
    ):
        auftraege = _auftraege(db, regular_user)
        wert, fehler, auftrag_id = auftraege.anlegen(_blick(), sieht=None)

        assert fehler is None
        assert wert["uebergeben"] is True
        # Nie ein „erledigt“: das Modell soll auf die Meldung warten.
        assert "nicht, es sei erledigt" in wert["hinweis"]
        job = db.get(DesktopJob, auftrag_id)
        assert job is not None and job.status == "pending"
        assert job.device_family == "familie-1"
        argumente = desktop_job_service.argumente(job)
        # Das Urteil des Panels, nicht des Modells.
        assert argumente["autonom"] is False
        assert auftraege.offen

    def test_der_lauf_ist_abgeschlossen_und_wird_nie_geweckt(
        self, db: Session, regular_user: User, angebot
    ):
        auftraege = _auftraege(db, regular_user)
        _, _, erster = auftraege.anlegen(_blick("call-1"), sieht=None)
        _, _, zweiter = auftraege.anlegen(_blick("call-2"), sieht=None)

        laeufe = {db.get(DesktopJob, erster).run_id, db.get(DesktopJob, zweiter).run_id}
        assert len(laeufe) == 1, "ein Lauf je Sitzung"
        run = db.get(AiRun, laeufe.pop())
        assert run.status == "completed"
        assert run.stop_reason == desktop_auftraege.STOP_GRUND
        assert ai_run_service.lauf_fortsetzen(db, run_id=run.id) is False

    def test_aus_dem_panel_kein_auftrag(self, db: Session, regular_user: User, angebot):
        auftraege = _auftraege(db, regular_user, herkunft="panel")
        wert, fehler, auftrag_id = auftraege.anlegen(_blick(), sieht=None)
        assert auftrag_id is None and fehler and "error" in wert
        assert db.query(DesktopJob).count() == 0

    def test_ohne_recht_kein_auftrag(self, db: Session, regular_user: User, monkeypatch):
        monkeypatch.setattr(
            "services.ai_action_service.angebotene_werkzeuge", lambda *_args: set()
        )
        auftraege = _auftraege(db, regular_user)
        _, fehler, auftrag_id = auftraege.anlegen(_blick(), sieht=None)
        assert auftrag_id is None and fehler
        assert db.query(DesktopJob).count() == 0

    def test_ein_blinder_blick_wird_gar_nicht_erst_aufgenommen(
        self, db: Session, regular_user: User, angebot
    ):
        auftraege = _auftraege(db, regular_user)
        wert, _, auftrag_id = auftraege.anlegen(_blick(), sieht=False)
        assert auftrag_id is None
        assert wert == {"error": KEIN_BLICK_GRUND}
        assert db.query(DesktopJob).count() == 0

    def test_mehr_als_der_deckel_wird_abgewiesen(
        self, db: Session, regular_user: User, angebot
    ):
        auftraege = _auftraege(db, regular_user)
        for nummer in range(desktop_auftraege.MAX_OFFENE):
            assert auftraege.anlegen(_blick(f"call-{nummer}"), sieht=None)[2] is not None
        _, fehler, auftrag_id = auftraege.anlegen(_blick("call-zuviel"), sieht=None)
        assert auftrag_id is None and "warten auf den Rechner" in fehler


class TestNachreichen:
    def test_ein_fertiges_ergebnis_kommt_mit_bild(
        self, db: Session, regular_user: User, angebot
    ):
        auftraege = _auftraege(db, regular_user)
        _, _, auftrag_id = auftraege.anlegen(_blick(), sieht=None)
        assert auftraege.fertige_abholen() == []

        job = db.get(DesktopJob, auftrag_id)
        desktop_job_service.ergebnis_melden(
            db, job=job, ok=True, ergebnis={"breite": 1920, BILDFELD: BILD}
        )
        fertig = auftraege.fertige_abholen()
        assert [kennung for kennung, _ in fertig] == [auftrag_id]
        assert not auftraege.offen

        text, bilder = desktop_auftraege.meldung([fertig[0][1]], sieht=None)
        assert "Meldung des Panels" in text
        assert BILD not in text, "das Bild reist nie als Text"
        assert bilder == [BILD]
        inhalt = desktop_auftraege.openai_inhalt(text, bilder)
        assert inhalt[1] == {"type": "input_image", "image_url": f"data:image/jpeg;base64,{BILD}"}
        teile = desktop_auftraege.gemini_teile(text, bilder)
        assert teile[1] == {"inlineData": {"mimeType": "image/jpeg", "data": BILD}}

    def test_ohne_augen_bleibt_nur_der_text(self, db: Session, regular_user: User, angebot):
        eintrag = {"tool_name": "desktop_steuern", "status": "done", "ergebnis": {BILDFELD: BILD}}
        text, bilder = desktop_auftraege.meldung([eintrag], sieht=False)
        assert bilder == [] and BILD not in text

    def test_ein_verfallener_auftrag_kommt_als_verfall(
        self, db: Session, regular_user: User, angebot
    ):
        from datetime import datetime, timedelta, timezone

        auftraege = _auftraege(db, regular_user)
        _, _, auftrag_id = auftraege.anlegen(_blick(), sieht=None)
        job = db.get(DesktopJob, auftrag_id)
        job.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

        (kennung, eintrag), = auftraege.fertige_abholen()
        assert kennung == auftrag_id
        assert eintrag["error_code"] == "DESKTOP_JOB_EXPIRED"


class _Panel:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, value):
        self.sent.append(value)


class _Sideband:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, value):
        self.sent.append(json.loads(value))


def _sitzung(user_id: int, conversation_id: str) -> tuple[realtime_session.RealtimeSitzung, _Panel, _Sideband]:
    panel, sideband = _Panel(), _Sideband()
    sitzung = realtime_session.RealtimeSitzung(
        panel,
        vorbereitung=realtime_session.RealtimeVorbereitung(
            provider_id=1,
            model="gpt-realtime",
            voice="marin",
            reasoning_effort=None,
            language="de",
            vad_eagerness="high",
            api_key="sk-test-never-log",
            instructions="Direkt antworten",
            tools=[{"type": "function", "name": "desktop_system", "parameters": {"type": "object"}}],
            conversation_id=conversation_id,
            usage_event_id=1,
        ),
        user_id=user_id,
        http_client=None,
        herkunft="desktop",
        familie="familie-1",
    )
    sitzung._sideband = sideband
    return sitzung, panel, sideband


@pytest.mark.asyncio
async def test_realtime_uebergibt_und_reicht_das_bild_nach(
    db: Session, regular_user: User, angebot, monkeypatch
):
    """Der ganze Weg einer Sitzung: Werkzeug, Übergabe, Meldung mit Bild."""

    async def _unbekannt(*_args, **_kwargs):
        return None

    monkeypatch.setattr(desktop_auftraege, "modell_sieht", _unbekannt)
    sitzung, panel, sideband = _sitzung(regular_user.id, _gespraech(db, regular_user))

    await sitzung._tool_ausfuehren({
        "call_id": "call-blick",
        "name": "desktop_system",
        "arguments": '{"aktion":"bildschirm"}',
    })
    # Sofort beantwortet: Ergebnis des Werkzeugs plus die Bitte um Fortsetzung.
    ausgabe = json.loads(sideband.sent[0]["item"]["output"])
    assert "uebergeben" in json.dumps(ausgabe)
    offen = [r for r in panel.sent if r.get("art") == "desktop_auftrag"]
    assert offen and offen[0]["zustand"] == "offen"
    auftrag_id = offen[0]["auftrag_id"]

    # Die App meldet — auf einem anderen Weg als dieser Sitzung.
    db.expire_all()
    desktop_job_service.ergebnis_melden(
        db, job=db.get(DesktopJob, auftrag_id), ok=True, ergebnis={BILDFELD: BILD}
    )
    sideband.sent.clear()
    sitzung._response_aktiv = False

    lauf = asyncio.create_task(sitzung._desktop_ergebnisse_zustellen())
    try:
        for _ in range(50):
            if len(sideband.sent) >= 2:
                break
            await asyncio.sleep(0.1)
    finally:
        lauf.cancel()
        await asyncio.gather(lauf, return_exceptions=True)

    eintrag, antwort = sideband.sent[:2]
    assert eintrag["type"] == "conversation.item.create"
    inhalt = eintrag["item"]["content"]
    assert inhalt[0]["type"] == "input_text"
    assert inhalt[1] == {"type": "input_image", "image_url": f"data:image/jpeg;base64,{BILD}"}
    assert antwort == {"type": "response.create"}
    assert any(
        r.get("art") == "desktop_auftrag" and r.get("zustand") == "fertig" for r in panel.sent
    )


@pytest.mark.asyncio
async def test_die_nachreichung_unterbricht_keinen_satz(
    db: Session, regular_user: User, angebot, monkeypatch
):
    async def _unbekannt(*_args, **_kwargs):
        return None

    monkeypatch.setattr(desktop_auftraege, "modell_sieht", _unbekannt)
    sitzung, _panel, sideband = _sitzung(regular_user.id, _gespraech(db, regular_user))
    _, _, auftrag_id = sitzung._desktop.anlegen(_blick(), sieht=None)
    desktop_job_service.ergebnis_melden(
        db, job=db.get(DesktopJob, auftrag_id), ok=True, ergebnis={"ok": True}
    )
    sitzung._assistant_spricht = True

    lauf = asyncio.create_task(sitzung._desktop_ergebnisse_zustellen())
    try:
        await asyncio.sleep(1.5)
        assert sideband.sent == [], "mitten im Satz kam die Meldung"
        sitzung._assistant_spricht = False
        for _ in range(30):
            if sideband.sent:
                break
            await asyncio.sleep(0.1)
    finally:
        lauf.cancel()
        await asyncio.gather(lauf, return_exceptions=True)
    assert sideband.sent and sideband.sent[0]["type"] == "conversation.item.create"


def test_die_stimme_bietet_den_rechner_aus_der_app_an(db: Session, regular_user: User, monkeypatch):
    """B1: `keep_static` schnitt die Desktop-Werkzeuge bis zum 05.10.2026 weg."""
    from services.ai_tool_registry import GEHIRN_DESKTOP

    monkeypatch.setattr(
        realtime_session.ai_action_service,
        "angebotene_werkzeuge",
        lambda *_args: {"web_search", *GEHIRN_DESKTOP, "desktop_dateien"},
    )

    class _Zugang:
        provider_kind = "openai"
        realtime_model = "gpt-realtime"

    aus_der_app = {
        t["name"]
        for t in realtime_session.angebotene_werkzeuge(
            db, provider=_Zugang(), user=regular_user, herkunft="desktop"
        )
    }
    assert GEHIRN_DESKTOP <= aus_der_app
    aus_dem_panel = {
        t["name"]
        for t in realtime_session.angebotene_werkzeuge(
            db, provider=_Zugang(), user=regular_user, herkunft="panel"
        )
    }
    assert not (aus_dem_panel & GEHIRN_DESKTOP)
