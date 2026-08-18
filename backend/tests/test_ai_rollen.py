"""Die drei Rollen des Agentic Framework: voll, gehirn, worker.

docs/agentic-framework.md (§3, §5, §7). Diese Tests binden die Zusagen fest,
die sich nicht aus der Registry allein ergeben:

* Der **Prompt** je Rolle teilt Blockkonstanten statt Texte zu kopieren, und
  "voll" bleibt byteweise der heutige Ein-Modell-Betrieb.
* Die **Ableitung** der Rolle haengt an Fensterart, Zugang und
  Unbeaufsichtigt-Flag — und faellt bei unlesbarem Zustand in die engste
  Rolle, nie in die weiteste.
* Der **Katalogschnitt** und seine **Spiegelschranke** lassen ein Gehirn nie
  an Server-Werkzeuge, auch nicht ueber halluzinierte Aufrufe.
* `wait_until` parkt einen Worker-Lauf mit beantworteter Runde; ausserhalb
  eines Worker-Laufs existiert es nicht.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, AiProvider, AiRun, Role, RolePermission, User
from services import ai_prompt, ai_run_broker, ai_run_service, ai_stream_service
from services.openai_compatible_adapter import (
    ProviderToolCall,
    StreamChunk,
    StreamUsage,
)


# ── Prompt ────────────────────────────────────────────────────────────────


def test_voll_bleibt_byteweise_der_alte() -> None:
    """Der Ein-Modell-Betrieb ist der Fallback aus §5 — kein Hard-Stop.

    Wer die Rollen einbaut und dabei den heutigen Prompt auch nur um ein
    Zeichen verschiebt, entwertet den Anbieter-Zwischenspeicher aller
    Bestandsinstallationen in einem Rutsch.
    """
    assert ai_prompt.build() == ai_prompt.build(rolle="voll")


def test_die_rollen_teilen_bloecke_statt_sie_zu_kopieren() -> None:
    """Kein Blocktext existiert doppelt — die Invariante des build-Docstrings.

    Kopierte Texte veralten lautlos gegeneinander; die Rollen-Tupel duerfen
    deshalb nur Konstanten referenzieren, die es schon gibt, plus je einen
    eigenen Rollenblock.
    """
    bekannte = set(ai_prompt.BLOECKE) | {ai_prompt.GEHIRN, ai_prompt.WORKER}
    assert set(ai_prompt.GEHIRN_BLOECKE) <= bekannte
    assert set(ai_prompt.WORKER_BLOECKE) <= bekannte


def test_das_gehirn_liest_keine_werkzeugbloecke() -> None:
    prompt = ai_prompt.build(rolle="gehirn")

    assert ai_prompt.GEHIRN in prompt
    assert ai_prompt.GEDAECHTNIS in prompt
    assert ai_prompt.UNTRUSTED in prompt
    # Werkzeuge, die es nicht hat, sollen ihm auch nicht beschrieben werden —
    # jeder Versuch, sie zu benutzen, kostete eine Runde.
    assert ai_prompt.RUECKFRAGEN not in prompt
    assert ai_prompt.WERKZEUGE not in prompt
    assert ai_prompt.GUARDIAN not in prompt
    assert ai_prompt.AUFGABEN not in prompt
    assert ai_prompt.SKILLS not in prompt


def test_der_worker_fragt_nie_mit_ask_user() -> None:
    prompt = ai_prompt.build(rolle="worker")

    assert ai_prompt.WORKER in prompt
    assert ai_prompt.WERKZEUGE in prompt
    assert ai_prompt.SKILLS in prompt
    # RUECKFRAGEN verlangt woertlich `ask_user` — das hat der Worker nicht;
    # sein Ersatz steht im WORKER-Block (`worker_frage`).
    assert ai_prompt.RUECKFRAGEN not in prompt
    assert ai_prompt.GEDAECHTNIS not in prompt
    assert ai_prompt.EINZELCHAT not in prompt
    assert ai_prompt.GUARDIAN not in prompt


def test_unbekannte_rollen_und_gesprochene_worker_werfen() -> None:
    with pytest.raises(ValueError, match="Rolle"):
        ai_prompt.build(rolle="admin")
    with pytest.raises(ValueError, match="gesprochen"):
        ai_prompt.build(gesprochen=True, rolle="worker")


def test_gesprochen_gilt_auch_fuer_das_gehirn() -> None:
    """Die Stimme spricht ausschliesslich Gehirn-Ausgaben — derselbe Schalter.

    `NUR_GETIPPT` filtert, `GESPROCHEN` haengt hinten an: dieselbe Mechanik
    wie im Voll-Betrieb, damit ein Block, der gesprochen nicht gilt, das in
    genau einer Datei sagt.
    """
    prompt = ai_prompt.build(gesprochen=True, rolle="gehirn")

    assert ai_prompt.FORMAT not in prompt
    assert prompt.endswith(ai_prompt.GESPROCHEN)


# ── Ableitung und Einfrieren ──────────────────────────────────────────────


def _zugang(worker_model: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        provider_kind="openrouter",
        default_model="schnell",
        worker_model=worker_model,
    )


class TestRollenableitung:
    def test_ein_worker_fenster_ist_immer_worker(self) -> None:
        fenster = SimpleNamespace(kind="worker")
        assert ai_stream_service._rolle_ableiten(
            None, None, fenster, _zugang("m"), True
        ) == "worker"
        assert ai_stream_service._rolle_ableiten(
            None, None, fenster, _zugang(None), False
        ) == "worker"

    def test_der_dauerchat_wird_zum_gehirn_sobald_ein_arbeitsmodell_da_ist(
        self, db: Session
    ) -> None:
        user = _benutzer_mit_rechten(db, "gehirnrecht", "ai.background.use")
        fenster = SimpleNamespace(kind="primary")
        assert ai_stream_service._rolle_ableiten(
            db, user, fenster, _zugang("m"), False
        ) == "gehirn"
        # Fallback aus §5: ohne worker_model der heutige Ein-Modell-Betrieb.
        assert ai_stream_service._rolle_ableiten(
            db, user, fenster, _zugang(None), False
        ) == "voll"

    def test_ohne_hintergrundrecht_bleibt_der_chat_im_ein_modell_betrieb(
        self, db: Session
    ) -> None:
        """Ein Gehirn ohne worker_start haette gar keinen Arbeitsweg mehr.

        Wem `ai.background.use` fehlt, dessen Chat arbeitet wie bisher in
        einem Lauf — derselbe Fallback wie ohne Arbeitsmodell, sonst waere
        die Rollentrennung fuer diesen Benutzer eine Aussperrung.
        """
        user = _benutzer_mit_rechten(db, "ohnehintergrund")
        fenster = SimpleNamespace(kind="primary")
        assert ai_stream_service._rolle_ableiten(
            db, user, fenster, _zugang("m"), False
        ) == "voll"

    def test_unbeaufsichtigte_und_guardian_laeufe_bleiben_voll(
        self, db: Session
    ) -> None:
        # Faellige Auftraege behalten ihren eigenen Werkzeugschnitt.
        user = _benutzer_mit_rechten(db, "vollrollen", "ai.background.use")
        primary = SimpleNamespace(kind="primary")
        assert ai_stream_service._rolle_ableiten(
            db, user, primary, _zugang("m"), True
        ) == "voll"
        guardian = SimpleNamespace(kind="guardian")
        assert ai_stream_service._rolle_ableiten(
            db, user, guardian, _zugang("m"), False
        ) == "voll"

    def test_ein_unlesbarer_zustand_faellt_in_die_engste_rolle(self) -> None:
        """Der Verlust des Rahmens ist die gefaehrliche Richtung.

        Ein Tippfehler im gespeicherten Rollenwort darf nie den vollen
        Katalog oeffnen — dieselbe Ueberlegung wie bei `guardian_aus_zustand`.
        """
        assert ai_stream_service.rolle_aus_zustand({}) == "voll"
        assert ai_stream_service.rolle_aus_zustand({"rolle": "gehirn"}) == "gehirn"
        assert ai_stream_service.rolle_aus_zustand({"rolle": "quatsch"}) == "worker"

    def test_das_modell_der_rolle(self) -> None:
        assert ai_stream_service._modell_fuer(_zugang("gruendlich"), "worker") == "gruendlich"
        assert ai_stream_service._modell_fuer(_zugang(None), "worker") == "schnell"
        assert ai_stream_service._modell_fuer(_zugang("gruendlich"), "gehirn") == "schnell"
        assert ai_stream_service._modell_fuer(_zugang("gruendlich"), "voll") == "schnell"


# ── Katalogschnitt (Fuehrung) ─────────────────────────────────────────────


_ANGEBOT = frozenset({
    "remember", "search_memory", "forget_memory",
    "worker_start", "worker_cancel", "worker_antwort",
    "wait_until", "worker_frage", "ask_user",
    "list_my_servers", "read_server_status",
})


def _vorbereitung() -> ai_stream_service._Vorbereitung:
    return ai_stream_service._Vorbereitung(
        run_id="r1",
        user_id=1,
        conversation_id="c1",
        provider=_zugang("gruendlich"),
        api_key=None,
        message_id="m1",
        usage_event_id=1,
        request_id="req1",
        reasoning=False,
        reasoning_effort=None,
        token_price_micro_usd_per_million=None,
        zustand={},
        angebotene_werkzeuge=_ANGEBOT,
    )


def _katalog(rolle: str) -> set[str]:
    async def _kein_modell(*args, **kwargs):
        return None

    with patch.object(ai_stream_service.ai_model_catalog, "finde", _kein_modell):
        tools, *_ = asyncio.run(ai_stream_service._werkzeuge_und_grenze(
            client=None,
            vorbereitung=_vorbereitung(),
            guardian=None,
            aufgabe=None,
            rolle=rolle,
            zustand={},
        ))
    return {str(t["function"]["name"]) for t in tools}


class TestKatalogschnitt:
    def test_das_gehirn_bekommt_nur_gedaechtnis_und_steuerung(self) -> None:
        namen = _katalog("gehirn")
        assert namen == {
            "remember", "search_memory", "forget_memory",
            "worker_start", "worker_cancel", "worker_antwort",
        }

    def test_der_worker_verliert_gedaechtnis_steuerung_und_ask_user(self) -> None:
        namen = _katalog("worker")
        assert "read_server_status" in namen and "list_my_servers" in namen
        assert "wait_until" in namen and "worker_frage" in namen
        assert namen & {"remember", "search_memory", "forget_memory"} == set()
        assert namen & {"worker_start", "worker_cancel", "worker_antwort"} == set()
        assert "ask_user" not in namen

    def test_der_voll_betrieb_kennt_keinen_hintergrund(self) -> None:
        namen = _katalog("voll")
        assert "read_server_status" in namen and "ask_user" in namen
        assert "remember" in namen
        assert namen & {
            "worker_start", "worker_cancel", "worker_antwort",
            "wait_until", "worker_frage",
        } == set()


# ── Spiegelschranke (die Zusage hinter der Fuehrung) ──────────────────────


def _benutzer_mit_rechten(db: Session, name: str, *rechte: str) -> User:
    from services.role_service import set_user_roles

    user = User(
        username=name,
        email_encrypted="x",
        email_hash=name,
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.flush()
    role = Role(name=f"rollen-{name}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for recht in ("ai.chat.use", *rechte):
        db.add(RolePermission(role_id=role.id, permission_key=recht))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()
    return user


def _benutzer_mit_fenster(db: Session, name: str):
    user = _benutzer_mit_rechten(db, name)
    from services.ai_chat_service import get_or_create_primary_conversation

    fenster = get_or_create_primary_conversation(db, user)
    db.commit()
    return user, fenster


def test_die_spiegelschranke_haelt_das_gehirn_von_servern_fern(db: Session) -> None:
    """Der Katalog ist eine Bitte — die Schranke sitzt am Aufruf selbst.

    Ein Gehirn-Lauf, dessen Modell einen Server-Werkzeugnamen halluziniert,
    bekommt eine Absage als Werkzeugergebnis; ausgefuehrt wird nichts. Der
    Test laesst die Ausfuehrung absichtlich explodieren — sie darf nie
    erreicht werden.
    """
    user, fenster = _benutzer_mit_fenster(db, "spiegel")

    def _niemals(*args, **kwargs):
        raise AssertionError("Ein Gehirn-Lauf darf kein Werkzeug ausfuehren")

    with patch.object(ai_stream_service, "_werkzeug_ausfuehren", _niemals):
        followup, used, nachtrag = asyncio.run(
            ai_stream_service._tool_followup_messages(
                user_id=user.id,
                conversation_id=fenster.id,
                tool_calls=[ProviderToolCall(
                    id="call1", name="read_server_status",
                    arguments={"server_id": 1},
                )],
                rolle="gehirn",
                anlagenwissen_noetig=False,
            )
        )

    assert used == []
    assert nachtrag is None
    ergebnisse = [m for m in followup if m.get("role") == "tool"]
    assert len(ergebnisse) == 1
    assert "dem Gehirn nicht zur" in ergebnisse[0]["content"]
    assert "worker_start" in ergebnisse[0]["content"]


def test_die_spiegelschranke_haelt_worker_werkzeuge_aus_dem_voll_betrieb(
    db: Session,
) -> None:
    user, fenster = _benutzer_mit_fenster(db, "vollspiegel")

    def _niemals(*args, **kwargs):
        raise AssertionError("Der Aufruf haette aussortiert werden muessen")

    with patch.object(ai_stream_service, "_werkzeug_ausfuehren", _niemals):
        followup, used, _ = asyncio.run(
            ai_stream_service._tool_followup_messages(
                user_id=user.id,
                conversation_id=fenster.id,
                tool_calls=[ProviderToolCall(
                    id="call1", name="worker_start",
                    arguments={"auftrag": "x"},
                )],
                rolle="voll",
                anlagenwissen_noetig=False,
            )
        )

    assert used == []
    ergebnisse = [m for m in followup if m.get("role") == "tool"]
    assert len(ergebnisse) == 1
    assert "Hintergrund-Betrieb" in ergebnisse[0]["content"]


def test_das_gehirn_schreibt_nie(db: Session) -> None:
    """Der Spiegel im Vorschlagspfad: kein Proposal aus einem Gehirn-Lauf."""
    usage = StreamUsage()
    usage.tool_calls = [ProviderToolCall(
        id="w1", name="propose_server_lifecycle",
        arguments={"server_id": 1, "action": "restart"},
    )]
    provider_messages: list[dict] = []
    zustand: dict = {}

    def _niemals(*args, **kwargs):
        raise AssertionError("Ein Gehirn-Lauf darf keine Vorschlaege anlegen")

    with patch.object(ai_stream_service, "_lauf_status", lambda run_id: "running"), \
         patch.object(ai_stream_service, "_persist_write_proposals", _niemals):
        ergebnis = asyncio.run(ai_stream_service._schreibrunde_ausfuehren(
            run_id="r1",
            user_id=1,
            conversation_id="c1",
            vorbereitung=_vorbereitung(),
            guardian=None,
            aufgabe=None,
            unbeaufsichtigt=False,
            rolle="gehirn",
            rundendeckel=48,
            rundentext="",
            current_usage=usage,
            provider_messages=provider_messages,
            zustand=zustand,
            chunks=[],
            thoughts=[],
            denknaht="",
        ))

    assert ergebnis.geparkt is False and ergebnis.abgeloest is False
    assert zustand["rounds"] == 1
    ergebnisse = [m for m in provider_messages if m.get("role") == "tool"]
    assert len(ergebnisse) == 1
    assert "AI_GEHIRN_READONLY" in ergebnisse[0]["content"]


# ── wait_until: die dritte Parkstelle ─────────────────────────────────────


def _wartewunsch(minuten, extra_call: bool = False) -> StreamUsage:
    usage = StreamUsage()
    usage.tool_calls = [ProviderToolCall(
        id="w1", name="wait_until",
        arguments={"minuten": minuten, "grund": "Backup laeuft"},
    )]
    if extra_call:
        usage.tool_calls.append(ProviderToolCall(
            id="w2", name="read_server_status", arguments={"server_id": 1},
        ))
    return usage


class TestWarten:
    def test_ein_worker_parkt_mit_beantworteter_runde(self) -> None:
        provider_messages: list[dict] = []
        zustand: dict = {}

        ergebnis = ai_stream_service._warten_behandeln(
            current_usage=_wartewunsch(30, extra_call=True),
            rolle="worker",
            run_id="r1",
            provider_messages=provider_messages,
            zustand=zustand,
            rundentext="Ich warte auf das Backup.",
            rundendeckel=48,
        )

        assert ergebnis is not None and ergebnis.signal == "parken"
        erwartet = datetime.now(timezone.utc) + timedelta(minutes=30)
        assert ergebnis.wake_at is not None
        assert abs((ergebnis.wake_at - erwartet).total_seconds()) < 5
        # Die ganze Runde ist beantwortet: Aufrufnachricht plus je ein
        # Ergebnis — sonst waere die Anfrage nach dem Wecken formal kaputt.
        assert zustand["rounds"] == 1
        assert [m["role"] for m in provider_messages] == ["assistant", "tool", "tool"]
        inhalte = [m["content"] for m in provider_messages if m["role"] == "tool"]
        assert any("geparkt" in inhalt for inhalt in inhalte)
        assert any("AI_RUN_PARKED" in inhalt for inhalt in inhalte)

    def test_ein_formfehler_kostet_die_runde_nicht_den_lauf(self) -> None:
        provider_messages: list[dict] = []
        zustand: dict = {}

        ergebnis = ai_stream_service._warten_behandeln(
            current_usage=_wartewunsch("gleich"),
            rolle="worker",
            run_id="r1",
            provider_messages=provider_messages,
            zustand=zustand,
            rundentext="",
            rundendeckel=48,
        )

        assert ergebnis is not None and ergebnis.signal == "weiter"
        assert zustand["rounds"] == 1
        assert any(
            "AI_WAIT_INVALID" in m["content"]
            for m in provider_messages if m["role"] == "tool"
        )

    def test_ausserhalb_eines_workers_existiert_es_nicht(self) -> None:
        for rolle in ("voll", "gehirn"):
            assert ai_stream_service._warten_behandeln(
                current_usage=_wartewunsch(30),
                rolle=rolle,
                run_id="r1",
                provider_messages=[],
                zustand={},
                rundentext="",
                rundendeckel=48,
            ) is None


@pytest.mark.asyncio
async def test_wait_until_parkt_den_ganzen_lauf(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der ganze Weg der dritten Parkstelle, nicht nur der Helfer.

    Ein Worker-Lauf, dessen Modell `wait_until` ruft, endet auf
    ``waiting_wake`` mit gesetzter Frist — genau die Zeile, die der Takt
    (`faellige_wecken`) liest. Harness nach dem Muster von
    test_ai_werkzeug_ansage: echter Lauf, gefaelschter Anbieter.
    """
    from uuid import uuid4

    user = _benutzer_mit_rechten(db, "wartender", "ai.background.use")
    provider = AiProvider(
        name="Warte", provider_kind="openrouter", default_model="model-a",
        enabled=True, requires_api_key=False,
    )
    db.add(provider)
    db.flush()
    fenster = AiConversation(
        id=str(uuid4()), user_id=user.id, kind="worker", title="Warteauftrag"
    )
    db.add(fenster)
    db.commit()

    run, fehler = ai_stream_service.lauf_beginnen(
        db, user=user, conversation=fenster, provider=provider,
        request_id=uuid4(), content="Prüf in 30 Minuten die Backups.",
        reasoning=False, guardian_briefing_unterdruecken=True,
        unbeaufsichtigt=True,
    )
    assert run is not None, f"Lauf konnte nicht beginnen: {fehler}"
    zustand = ai_run_service.zustand_lesen(run)
    assert zustand.get("rolle") == "worker"
    zustand["worker"] = {
        "conversation_id": fenster.id, "titel": "Warteauftrag", "kanal": "chat",
    }
    ai_run_service.zustand_schreiben(run, zustand)
    db.commit()

    async def fake(_client, *, usage: StreamUsage, tool_choice=None, **kwargs):
        if tool_choice != "none":
            usage.tool_calls = [ProviderToolCall(
                id="w1", name="wait_until",
                arguments={"minuten": 30, "grund": "Backupfenster"},
            )]
        usage.total_tokens = 10
        yield StreamChunk("content", "Ich warte auf das Backupfenster.")

    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", fake)
    ai_run_broker.zuruecksetzen_fuer_tests()
    ai_run_broker.eroeffnen(run.id)

    await ai_stream_service.segment_ausfuehren(run.id, client=object())

    db.expire_all()
    lauf = db.get(AiRun, run.id)
    assert lauf.status == "waiting_wake"
    assert lauf.stop_reason == "wait_until"
    assert lauf.wake_at is not None
    erwartet = datetime.now(timezone.utc) + timedelta(minutes=30)
    frist = lauf.wake_at
    if frist.tzinfo is None:
        frist = frist.replace(tzinfo=timezone.utc)
    assert abs((frist - erwartet).total_seconds()) < 60


# ── worker_frage: der Fragepfad des Workers ───────────────────────────────


class TestWorkerFrage:
    def _frage(self, name: str) -> StreamUsage:
        usage = StreamUsage()
        usage.tool_calls = [ProviderToolCall(
            id="f1", name=name,
            arguments={
                "question": "Variante A oder B?",
                "options": [{"label": "A"}, {"label": "B"}],
            },
        )]
        return usage

    def test_worker_frage_parkt_statt_abgewiesen_zu_werden(self) -> None:
        ergebnis = ai_stream_service._fragen_behandeln(
            current_usage=self._frage("worker_frage"),
            unbeaufsichtigt=True,
            run_id="r1",
            provider_messages=[],
            zustand={},
            rundentext="",
            rolle="worker",
            rundendeckel=48,
        )

        assert ergebnis is not None and ergebnis.signal == "frage"
        assert ergebnis.frage is not None
        assert ergebnis.frage.get("question") == "Variante A oder B?"

    def test_ask_user_bleibt_im_worker_abgewiesen(self) -> None:
        provider_messages: list[dict] = []
        zustand: dict = {}

        ergebnis = ai_stream_service._fragen_behandeln(
            current_usage=self._frage("ask_user"),
            unbeaufsichtigt=True,
            run_id="r1",
            provider_messages=provider_messages,
            zustand=zustand,
            rundentext="",
            rolle="worker",
            rundendeckel=48,
        )

        assert ergebnis is not None and ergebnis.signal == "weiter"
        assert zustand["rounds"] == 1
