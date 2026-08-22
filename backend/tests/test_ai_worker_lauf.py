"""Der Weckpfad der Worker: parken, wecken, Rechte pruefen, wiederanlaufen.

docs/agentic-framework.md (Abschnitte 3 und 6). Alle Weckwege laufen durch
`lauf_fortsetzen` — deshalb sitzen die Zusagen dort und werden hier einzeln
festgehalten:

* ``waiting_wake`` ist weckbar, und die Frist wird beim Wecken geleert.
* Der no_runtime-Rueckfall stellt den **Vorzustand** wieder her — ein
  gewecktes ``waiting_wake``, das als ``waiting_confirmation`` liegen bliebe,
  waere ueber jeden Bestaetigungspfad faelschlich weckbar.
* Beim Wecken eines Workers werden die Rechte **neu** geprueft; Wegfall heisst
  ``cancelled`` mit benanntem Grund plus Meldung, nie stiller Schwund.
* Der Startabgleich saet je unterbrochenem Worker hoechstens **einen**
  automatischen Wiederanlauf.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, AiMeldung, AiMessage, AiRun, Role, RolePermission, User
from services import ai_provider_service, ai_run_service
from services.role_service import set_user_roles


def _benutzer(db: Session, name: str, *, rechte: tuple[str, ...] = (
    "ai.chat.use", "ai.background.use",
)) -> User:
    user = User(
        username=name,
        email_encrypted="x",
        email_hash=name,
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.flush()
    if rechte:
        role = Role(name=f"lauf-{name}", description=None, is_system=False)
        db.add(role)
        db.flush()
        for recht in rechte:
            db.add(RolePermission(role_id=role.id, permission_key=recht))
        db.commit()
        set_user_roles(db, user, [role.id])
    db.commit()
    return user


def _worker_lauf(
    db: Session, user: User, *, status: str = "waiting_wake",
    wake_at: datetime | None = None, stop_reason: str | None = None,
    rahmen: dict | None = None, lauf_id: str | None = None,
    fenster_id: str | None = None,
) -> AiRun:
    fenster_id = fenster_id or f"w-{uuid4().hex[:8]}"
    fenster = db.get(AiConversation, fenster_id)
    if fenster is None:
        fenster = AiConversation(
            id=fenster_id, user_id=user.id, kind="worker", title="Auftrag"
        )
        db.add(fenster)
        db.flush()
    run = AiRun(
        id=lauf_id or f"r-{uuid4().hex[:8]}",
        conversation_id=fenster.id,
        user_id=user.id,
        status=status,
        wake_at=wake_at,
        stop_reason=stop_reason,
    )
    db.add(run)
    db.flush()
    zustand = ai_run_service.zustand_lesen(run)
    zustand["worker"] = rahmen if rahmen is not None else {
        "conversation_id": fenster.id, "titel": "Auftrag", "kanal": "chat",
    }
    ai_run_service.zustand_schreiben(run, zustand)
    db.commit()
    return run


class TestWeckpfad:
    def test_waiting_wake_ist_weckbar(self, db: Session) -> None:
        user = _benutzer(db, "weckbar")
        run = _worker_lauf(db, user)

        assert ai_run_service.darf_fortsetzen(db, run) is True

    def test_wecken_leert_die_frist(self, db: Session) -> None:
        user = _benutzer(db, "fristlos")
        run = _worker_lauf(db, user, wake_at=datetime.now(timezone.utc))

        with patch.object(ai_run_service, "_aufgabe_planen", lambda run_id: True):
            assert ai_run_service.lauf_fortsetzen(db, run_id=run.id) is True

        db.refresh(run)
        assert run.status == "running"
        assert run.wake_at is None

    def test_der_rueckfall_stellt_den_vorzustand_wieder_her(self, db: Session) -> None:
        """Kein gewecktes waiting_wake darf als waiting_confirmation liegen bleiben."""
        user = _benutzer(db, "rueckfall")
        run = _worker_lauf(db, user)

        with patch.object(ai_run_service, "_aufgabe_planen", lambda run_id: False):
            assert ai_run_service.lauf_fortsetzen(db, run_id=run.id) is False

        db.refresh(run)
        assert run.status == "waiting_wake"

    def test_entzogenes_recht_beendet_den_lauf_mit_meldung(self, db: Session) -> None:
        user = _benutzer(db, "entzogen", rechte=("ai.chat.use",))
        run = _worker_lauf(db, user)

        assert ai_run_service.lauf_fortsetzen(db, run_id=run.id) is False

        db.refresh(run)
        assert run.status == "cancelled"
        assert run.stop_reason == "berechtigung_entzogen"
        meldung = db.query(AiMeldung).one()
        assert "angehalten" in meldung.text
        assert meldung.worker_id == run.conversation_id

    def test_ein_chat_lauf_wird_nicht_neu_geprueft(self, db: Session) -> None:
        """Die Neupruefung gilt Workern; Bestaetigungen pruefen ihre Rechte selbst."""
        user = _benutzer(db, "chatlauf", rechte=())
        fenster = AiConversation(
            id="chat-c1", user_id=user.id, kind="primary", title="Chat"
        )
        db.add(fenster)
        db.flush()
        run = AiRun(
            id="chat-r1", conversation_id=fenster.id, user_id=user.id,
            status="waiting_confirmation",
        )
        db.add(run)
        db.commit()

        with patch.object(ai_run_service, "_aufgabe_planen", lambda run_id: True):
            assert ai_run_service.lauf_fortsetzen(db, run_id=run.id) is True
        db.refresh(run)
        assert run.status == "running"


class TestTakt:
    def test_faellige_fristen_werden_geweckt(self, db: Session) -> None:
        user = _benutzer(db, "takt")
        faellig = _worker_lauf(
            db, user, wake_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        spaeter = _worker_lauf(
            db, user, wake_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        nur_ereignis = _worker_lauf(db, user, wake_at=None)

        with (
            patch.object(ai_run_service, "http_client", lambda: object()),
            patch.object(ai_run_service, "_aufgabe_planen", lambda run_id: True),
        ):
            geweckt = ai_run_service.faellige_wecken(db)

        assert geweckt == 1
        db.refresh(faellig)
        db.refresh(spaeter)
        db.refresh(nur_ereignis)
        assert faellig.status == "running"
        # Eine kuenftige Frist und ein Ereignis-Parker bleiben liegen.
        assert spaeter.status == "waiting_wake"
        assert nur_ereignis.status == "waiting_wake"

    def test_ohne_laufzeit_wird_niemand_angefasst(self, db: Session) -> None:
        user = _benutzer(db, "ohnelaufzeit")
        run = _worker_lauf(
            db, user, wake_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )

        with patch.object(ai_run_service, "http_client", lambda: None):
            assert ai_run_service.faellige_wecken(db) == 0

        db.refresh(run)
        assert run.status == "waiting_wake"


class TestAusfuehrungsWecken:
    def test_finish_lifecycle_task_weckt_den_wartenden_lauf(
        self, db: Session, tmp_path
    ) -> None:
        """Erfolg und Fehlschlag wecken — beides ist die erwartete Antwort."""
        from models import AiActionProposal
        from services.actor_context import ActorContext
        from services.operation_task_service import (
            create_or_reuse_task,
            finish_lifecycle_task,
            mark_running,
        )

        user = _benutzer(db, "lifecycle")
        run = _worker_lauf(db, user, wake_at=None)
        task, _created = create_or_reuse_task(
            db,
            actor=ActorContext.for_user(user),
            task_type="server.lifecycle.restart",
            request_hash="a" * 64,
            idempotency_key=f"wake-{run.id}",
        )
        mark_running(db, task, "queued")
        db.add(AiActionProposal(
            id=str(uuid4()),
            conversation_id=run.conversation_id,
            user_id=user.id,
            tool_name="propose_server_lifecycle",
            payload_encrypted="x",
            preview_json="{}",
            status="executing",
            task_id=task.id,
            run_id=run.id,
            correlation_id=str(uuid4()),
        ))
        db.commit()

        geweckt: list[str] = []
        with patch.object(
            ai_run_service, "lauf_fortsetzen",
            lambda db_, *, run_id: geweckt.append(run_id) or True,
        ):
            finish_lifecycle_task(db, task.id, succeeded=False)

        assert geweckt == [run.id]


class TestWiederanlauf:
    def _saehen(self, db: Session) -> int:
        anbieter = ai_provider_service.create_provider(
            db,
            name=f"Seed-{uuid4().hex[:6]}",
            provider_kind="openrouter",
            default_model="modell",
            enabled=True,
            requires_api_key=True,
            operator_api_key="sk-or-v1-test",
        )
        db.commit()
        from services.ai_context_window import unbekannt

        flug = ai_run_service.Vorflug(
            anbieter=anbieter, denken=False, stufe=None, fenster=unbekannt()
        )

        async def _vorflug(client, db_, user_):
            return flug, anbieter

        with (
            patch.object(ai_run_service, "http_client", lambda: object()),
            patch.object(ai_run_service, "vorflug", _vorflug),
            patch.object(ai_run_service, "anlauf", lambda db_, run: True),
        ):
            return asyncio.run(ai_run_service.worker_wiederanlauf_saehen(db))

    def test_ein_unterbrochener_worker_laeuft_genau_einmal_wieder_an(
        self, db: Session
    ) -> None:
        user = _benutzer(db, "reseed")
        alter = _worker_lauf(
            db, user, status="failed", stop_reason="process_restart",
            rahmen={"conversation_id": "seed-w1", "titel": "Backups",
                    "kanal": "chat", "anlauf": 0},
            fenster_id="seed-w1",
        )

        assert self._saehen(db) == 1

        laeufe = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == "seed-w1")
            .order_by(AiRun.created_at.asc())
            .all()
        )
        assert len(laeufe) == 2
        # Der alte Endzustand bleibt woertlich stehen — er ist der Beleg.
        assert laeufe[0].status == "failed"
        neuer = laeufe[1]
        rahmen = ai_run_service.zustand_lesen(neuer).get("worker")
        assert rahmen["anlauf"] == 1
        # Der Pruefauftrag ist der Inhalt des neuen Laufs.
        nachricht = (
            db.query(AiMessage)
            .filter(
                AiMessage.conversation_id == "seed-w1", AiMessage.role == "user"
            )
            .order_by(AiMessage.created_at.desc())
            .first()
        )
        assert "wiederhole nichts blind" in nachricht.content

    def test_die_rolle_des_gestorbenen_laufs_wandert_mit(
        self, db: Session
    ) -> None:
        """Ein Aufgabenlauf bleibt „voll", auch wenn er im Worker-Fenster lag.

        Stehende Aufgaben laufen seit dem 20.08.2026 in Fenstern mit
        ``kind='worker'``, und `_rolle_ableiten` liest genau daraus. Ohne die
        mitwandernde Rolle wuerde ein wiederangelaufener Aufgabenlauf zum
        Worker: er verloere den Aufgaben-Werkzeugschnitt, und seit dem
        22.08.2026 wuerde er in der Schreibrunde auf einen Klick parken, den
        um drei Uhr nachts niemand tut (`niemand_da`). Genau diese Ausnahme
        sagt der Kommentar dort zu — hier steht sie unter Test.
        """
        user = _benutzer(db, "rollenerbe")
        alter = _worker_lauf(
            db, user, status="failed", stop_reason="process_restart",
            rahmen={"conversation_id": "seed-w9", "titel": "Nachtlauf",
                    "kanal": "chat", "anlauf": 0},
            fenster_id="seed-w9",
        )
        zustand = ai_run_service.zustand_lesen(alter)
        zustand["rolle"] = "voll"
        ai_run_service.zustand_schreiben(alter, zustand)
        db.commit()

        assert self._saehen(db) == 1

        neuer = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == "seed-w9", AiRun.id != alter.id)
            .one()
        )
        assert ai_run_service.zustand_lesen(neuer).get("rolle") == "voll"

    def test_der_zweite_neustart_saet_nicht_mehr(self, db: Session) -> None:
        user = _benutzer(db, "zweiterneustart")
        _worker_lauf(
            db, user, status="failed", stop_reason="process_restart",
            rahmen={"conversation_id": "seed-w2", "titel": "Backups",
                    "kanal": "chat", "anlauf": 1},
            fenster_id="seed-w2",
        )

        assert self._saehen(db) == 0

        assert db.query(AiRun).filter(
            AiRun.conversation_id == "seed-w2"
        ).count() == 1
        # Stattdessen erfaehrt es der Mensch.
        meldung = db.query(AiMeldung).one()
        assert "nicht noch einmal" in meldung.text

    def test_nur_der_juengste_lauf_eines_fensters_zaehlt(self, db: Session) -> None:
        """Alte failed-Zeilen aus frueheren Neustarts werden nicht wiederbelebt."""
        user = _benutzer(db, "altlast")
        _worker_lauf(
            db, user, status="failed", stop_reason="process_restart",
            rahmen={"anlauf": 0}, fenster_id="seed-w3", lauf_id="seed-r-alt",
        )
        junger = _worker_lauf(
            db, user, status="completed", fenster_id="seed-w3", lauf_id="seed-r-neu",
        )
        junger.created_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        db.commit()

        assert self._saehen(db) == 0
        assert db.query(AiRun).filter(
            AiRun.conversation_id == "seed-w3"
        ).count() == 2
