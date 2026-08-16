"""Der Reparaturauftrag: ein Vorfall, viele Anlaeufe.

Die Zusage, um die es hier geht, laesst sich in einem Satz sagen: **ein
erschoepftes Rundenbudget ist kein Ergebnis.** Bis vor kurzem war es eines. Ein
Heilungslauf endete nach achtundvierzig Leserunden mit ``stop_reason='budget'``,
wurde als ``status='completed'`` verbucht, und die Notiz mit ``mode='healing'``
stand seit dem *Start* in der Datenbank — der Vorfall war damit fuer immer
versorgt. Der Server blieb stehen, die Mail sagte "nicht behoben", und nichts
fasste ihn je wieder an.

Die Tests hier pruefen die vier Stellen, an denen das gehalten wird:

* die **Leiter** — diagnose, eingriff, beobachtung, und keine Abkuerzung,
* die **Bremse** — Frist und Versuchsdeckel, damit aus "gibt nicht auf" kein
  "hoert nie auf" wird,
* der **Nachweis** — erledigt ist, was die Anlage zeigt, nicht was das Modell
  schreibt; und die Quarantaene faellt nur gegen einen ausgefuehrten Eingriff,
* die **Mail** — eine je Auftrag, nicht acht.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiAutonomyGrant,
    AiConversation,
    AiGuardianRepair,
    AiMessage,
    AiRun,
    Incident,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_guardian_repair_service as reparatur
from services import ai_guardian_service, ai_run_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


KI_RECHTE = ("ai.chat.use", "ai.autonomous.use")


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _utc(wert: datetime) -> datetime:
    """SQLite gibt zeitzonenlose Werte zurueck, PostgreSQL zeitzonenbehaftete.

    Ein Vergleich zwischen beiden wirft `TypeError` — und zwar erst hier in der
    Testsuite, nie im Dienst, der ueberall seine eigene Umrechnung hat.
    """
    return wert.replace(tzinfo=timezone.utc) if wert.tzinfo is None else wert


def _benutzer(db: Session, name: str = "freigeber") -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    db.commit()
    rolle = Role(name=f"ki-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in KI_RECHTE:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def _server(db: Session, name: str = "Reparaturserver", **felder) -> Server:
    server = Server(
        name=name,
        game_type="dayz",
        install_dir="/tmp/reparatur",
        container_name="msm-reparatur",
        status="stopped",
        **felder,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _freigabe(db: Session, user: User, server: Server) -> None:
    db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key="server.view"))
    db.add(AiAutonomyGrant(
        user_id=user.id, server_id=server.id, enabled=True, max_actions_per_hour=10
    ))
    db.commit()


def _vorfall(db: Session, server: Server, *, status: str = "open") -> Incident:
    vorfall = Incident(
        server_id=server.id,
        title="Autopilot: process_not_running",
        description="GameThread haengt",
        type="process_not_running",
        status=status,
        fingerprint=f"guardian:{server.id}:process_not_running",
        occurrences=3,
    )
    db.add(vorfall)
    db.commit()
    db.refresh(vorfall)
    return vorfall


def _auftrag(
    db: Session,
    vorfall: Incident,
    server: Server,
    user: User,
    *,
    phase: str = "diagnose",
    attempt: int = 0,
    faellig: datetime | None = None,
    frist_stunden: float = 6,
) -> AiGuardianRepair:
    zeile = AiGuardianRepair(
        id=str(uuid4()),
        incident_id=vorfall.id,
        server_id=server.id,
        user_id=user.id,
        phase=phase,
        attempt=attempt,
        next_run_at=faellig if faellig is not None else _jetzt(),
        deadline_at=_jetzt() + timedelta(hours=frist_stunden),
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return zeile


def _fenster(db: Session, user: User) -> AiConversation:
    """Die Guardian-Unterhaltung dieses Benutzers."""
    zeile = (
        db.query(AiConversation)
        .filter(AiConversation.user_id == user.id, AiConversation.kind == "guardian")
        .first()
    )
    if zeile is None:
        zeile = AiConversation(
            id=f"guardian-{user.id}", user_id=user.id, server_id=None,
            kind="guardian", title="Guardian-Reparaturen",
        )
        db.add(zeile)
        db.commit()
    return zeile


def _lauf(
    db: Session,
    user: User,
    auftrag: AiGuardianRepair,
    *,
    status: str = "completed",
    stop_reason: str = "budget",
    antwort: str | None = None,
) -> AiRun:
    """Ein beendeter Reparaturlauf mit Guardian-Rahmen — samt Antworttext.

    Der Rahmen ist hier kein Beiwerk: `lauf_beendet` findet den Auftrag
    ausschliesslich ueber `repair_id` darin.
    """
    fenster = _fenster(db, user)
    run = AiRun(
        id=str(uuid4()),
        user_id=user.id,
        conversation_id=fenster.id,
        status=status,
        stop_reason=stop_reason,
    )
    zustand = ai_run_service.leerer_zustand([], request_id=str(uuid4()))
    zustand["guardian"] = {
        "server_id": auftrag.server_id,
        "incident_id": auftrag.incident_id,
        "backup_anker": _jetzt().isoformat(),
        "repair_id": auftrag.id,
        "phase": auftrag.phase,
        "attempt": auftrag.attempt,
    }
    ai_run_service.zustand_schreiben(run, zustand)
    db.add(run)
    db.commit()
    if antwort is not None:
        db.add(AiMessage(
            id=str(uuid4()),
            conversation_id=fenster.id,
            role="assistant",
            content=antwort,
            status="complete",
        ))
        db.commit()
    auftrag.last_run_id = run.id
    db.commit()
    return run


def _zustand(run: AiRun) -> dict:
    return json.loads(run.state_json or "{}")


# ── Die Leiter ────────────────────────────────────────────────────────────


class TestPhasenleiter:
    def test_diagnose_fuehrt_zum_eingriff(self, db: Session):
        """Nach dem Untersuchen wird gehandelt — nicht noch einmal untersucht.

        Genau hier hat das Modell im Betrieb aufgehoert: ein paar Werkzeuge
        gelesen, einen Absatz geschrieben, fertig. Die Phase kommt deshalb von
        aussen.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="diagnose")
        run = _lauf(db, user, auftrag)

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "eingriff"
        assert auftrag.next_run_at is not None

    def test_eingriff_fuehrt_zur_beobachtung(self, db: Session):
        """Ein `docker start` ist kein Beweis. Zehn Minuten spaeter schon eher."""
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff")
        run = _lauf(db, user, auftrag)

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "beobachtung"
        # Und der naechste Weckruf liegt wirklich in der Zukunft — sonst waere
        # "beobachten" nur ein anderes Wort fuer "sofort noch einmal".
        assert _utc(auftrag.next_run_at) > _jetzt() + timedelta(minutes=5)

    def test_budget_beendet_den_auftrag_nicht(self, db: Session):
        """**Der Kern der ganzen Aenderung.**

        `stop_reason='budget'` heisst "die KI hatte noch etwas vor, durfte aber
        nicht mehr". Das ist ein Grund fuer den naechsten Anlauf und kein
        Ergebnis — und es wird als `status='completed'` verbucht, sah also
        frueher aus wie ein Erfolg.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff")
        run = _lauf(db, user, auftrag, status="completed", stop_reason="budget")

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "beobachtung"

    def test_ein_gescheiterter_lauf_beendet_den_auftrag_nicht(self, db: Session):
        """Auch ein Fehlschlag sagt nichts ueber den Server.

        Ein abgerissener Strom, ein Anbieterfehler, ein abgeloester Lauf — nichts
        davon ist eine Aussage darueber, ob der Server laeuft.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="diagnose")
        run = _lauf(db, user, auftrag, status="failed", stop_reason="AI_PROVIDER_ERROR")

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "eingriff"

    def test_beobachtung_ohne_beleg_geht_zurueck_in_den_eingriff(self, db: Session):
        """Nicht gehalten heisst: noch einmal, mit dem was wir jetzt wissen."""
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server, status="open")
        auftrag = _auftrag(db, vorfall, server, user, phase="beobachtung")
        run = _lauf(db, user, auftrag)

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "eingriff"

    def test_die_erkenntnisse_gehen_in_den_naechsten_anlauf(self, db: Session):
        """Der einzige Weg, auf dem etwas eine Laufgrenze ueberlebt.

        `arbeitsspeicher_leeren` wirft `provider_messages` bei jedem Endzustand
        weg — dort steht der entschluesselte Gedaechtnisblock des Benutzers im
        Klartext. Ohne diese Spalte faengt jeder Anlauf bei null an und liest
        dieselben Logs noch einmal.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="diagnose")
        run = _lauf(
            db, user, auftrag,
            antwort=(
                "Der Container startet und stirbt nach vier Sekunden. Im Log "
                "steht ein OOM-Kill des Kernels; auf der Node laufen zwoelf "
                "Server bei acht Gigabyte. Als Naechstes senke ich das "
                "Speicherlimit dieser Instanz."
            ),
        )

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.erkenntnisse is not None
        assert "OOM-Kill" in auftrag.erkenntnisse
        # Und der Auftragstext des naechsten Anlaufs traegt sie wirklich.
        db.refresh(vorfall)
        text = ai_guardian_service._auftragstext(server, vorfall, auftrag)
        assert "OOM-Kill" in text
        assert "Phase 2 von 3" in text


# ── Die Bremse ────────────────────────────────────────────────────────────


class TestBremse:
    def test_die_frist_beendet_den_auftrag(self, db: Session):
        """"Gibt nicht auf" darf nicht "hoert nie auf" heissen.

        Ohne Frist kann ein Auftrag, der bei jedem Anlauf ein bisschen
        weiterkommt, tagelang Kosten verursachen, ohne dass je eine Mail den
        Betreiber erreicht.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff")
        auftrag.deadline_at = _jetzt() - timedelta(minutes=1)
        db.commit()
        run = _lauf(db, user, auftrag)

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "aufgegeben"
        assert auftrag.next_run_at is None

    def test_der_versuchsdeckel_beendet_den_auftrag(self, db: Session):
        """Acht Anlaeufe ohne Wirkung sind kein Argument fuer einen neunten."""
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(
            db, vorfall, server, user, phase="eingriff", attempt=reparatur.MAX_VERSUCHE
        )
        run = _lauf(db, user, auftrag)

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "aufgegeben"

    def test_ein_belegter_erfolg_schlaegt_die_bremse(self, db: Session):
        """Erst die Frage, ob es gut ist — dann erst Frist und Deckel.

        Ein Auftrag, dessen Wirkung belegt ist, endet als `erledigt` und nicht
        als `aufgegeben`, auch wenn er im selben Moment gegen die Frist laeuft.
        Die umgekehrte Reihenfolge schriebe dem Betreiber "nicht behoben" ueber
        einen Server, der laeuft.
        """
        user = _benutzer(db)
        server = _server(db, desired_power_state="running", guardian_observed_state="healthy")
        vorfall = _vorfall(db, server, status="resolved")
        auftrag = _auftrag(db, vorfall, server, user, phase="beobachtung")
        auftrag.deadline_at = _jetzt() - timedelta(minutes=1)
        db.commit()
        run = _lauf(db, user, auftrag)

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "erledigt"


# ── Der Nachweis ──────────────────────────────────────────────────────────


class TestNachweis:
    def test_erledigt_ist_was_die_anlage_zeigt(self, db: Session):
        """Vorfall geloest, Server im gewollten Zustand, keine Quarantaene."""
        user = _benutzer(db)
        server = _server(db, desired_power_state="running", guardian_observed_state="healthy")
        vorfall = _vorfall(db, server, status="resolved")
        auftrag = _auftrag(db, vorfall, server, user, phase="beobachtung")
        run = _lauf(db, user, auftrag)

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "erledigt"
        assert auftrag.next_run_at is None

    def test_ein_geloester_vorfall_auf_einem_toten_server_zaehlt_nicht(self, db: Session):
        """Der Fall, den ein Modell am liebsten ueberspringt.

        `docker start` beendet den Vorfall — es sagt aber nichts darueber, ob
        der Dienst danach noch laeuft. `guardian_observed_state` ist die
        Auskunft des Agenten nach seiner naechsten Probe, und genau deshalb wird
        ueberhaupt beobachtet statt sofort geurteilt.
        """
        user = _benutzer(db)
        server = _server(db, desired_power_state="running", guardian_observed_state="stopped")
        vorfall = _vorfall(db, server, status="resolved")
        auftrag = _auftrag(db, vorfall, server, user, phase="beobachtung")

        belegt, grund = reparatur.wirkung_belegt(db, auftrag)

        assert belegt is False
        assert grund == "zustand_stopped"

    def test_ein_erfolg_ohne_beobachtung_wird_erst_beobachtet(self, db: Session):
        """Sieht erledigt aus, ist aber noch nicht beobachtet.

        Zehn Minuten Zusehen kosten nichts als Zeit — und genau in diesen zehn
        Minuten ist im Betrieb der Server wieder gestanden.
        """
        user = _benutzer(db)
        server = _server(db, desired_power_state="running", guardian_observed_state="healthy")
        vorfall = _vorfall(db, server, status="resolved")
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff")
        run = _lauf(db, user, auftrag)

        reparatur.lauf_beendet(db, run, _zustand(run))

        db.refresh(auftrag)
        assert auftrag.phase == "beobachtung"

    def test_die_quarantaene_faellt_nur_gegen_einen_ausgefuehrten_eingriff(
        self, db: Session
    ):
        """Ohne Nachweis nie.

        `quarantined` ist der Zustand, in dem Guardian aufgegeben hat. Ihn ohne
        Grund aufzuheben hiesse, den Agenten dieselbe Leiter noch einmal
        hochzuschicken, die er schon bis zum Ende gelaufen ist — und der Server
        landete Minuten spaeter wieder dort.
        """
        user = _benutzer(db)
        server = _server(db, guardian_quarantine_status="quarantined")
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff")

        assert reparatur._quarantaene_aufheben(db, auftrag, server) is False
        db.refresh(server)
        assert server.guardian_quarantine_control is None

    def test_mit_ausgefuehrtem_eingriff_faellt_die_quarantaene(self, db: Session):
        """Und die Gegenprobe: mit Beleg wird sie angefordert, mit Spur.

        Ohne diesen Weg bliebe ein reparierter Server fuer immer als tot
        verbucht — der Agent ruehrt ihn nicht mehr an, also kann der Auftrag
        auch nie belegen, dass sein Eingriff gewirkt hat.
        """
        user = _benutzer(db)
        server = _server(db, guardian_quarantine_status="quarantined")
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff")
        fenster = _fenster(db, user)
        db.add(AiActionProposal(
            id=str(uuid4()),
            conversation_id=fenster.id,
            user_id=user.id,
            server_id=server.id,
            tool_name="propose_config_update",
            payload_encrypted="test-enc-v1::7b7d",
            correlation_id=str(uuid4()),
            preview_json="{}",
            status="succeeded",
        ))
        db.commit()

        assert reparatur._quarantaene_aufheben(db, auftrag, server) is True

        db.refresh(server)
        steuerung = json.loads(server.guardian_quarantine_control or "{}")
        assert steuerung.get("clear") is True
        assert steuerung.get("operation_id") == auftrag.id

    def test_eine_fremde_aktion_ist_kein_nachweis(self, db: Session):
        """Was der Betreiber nebenher im Chat tut, gehoert nicht dem Auftrag.

        Ohne den Fensterfilter fiele die Quarantaene wegen einer Arbeit, die
        dieser Auftrag nie getan hat.
        """
        user = _benutzer(db)
        server = _server(db, guardian_quarantine_status="quarantined")
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff")
        chat = AiConversation(
            id=f"chat-{user.id}", user_id=user.id, server_id=None,
            kind="primary", title="KI-Assistent",
        )
        db.add(chat)
        db.commit()
        db.add(AiActionProposal(
            id=str(uuid4()),
            conversation_id=chat.id,
            user_id=user.id,
            server_id=server.id,
            tool_name="propose_config_update",
            payload_encrypted="test-enc-v1::7b7d",
            correlation_id=str(uuid4()),
            preview_json="{}",
            status="succeeded",
        ))
        db.commit()

        assert reparatur._eingriff_nachweisen(db, auftrag) is None


# ── Der Takt ──────────────────────────────────────────────────────────────


class TestTakt:
    @pytest.mark.asyncio
    async def test_zwei_durchlaeufe_starten_einen_lauf(self, db: Session):
        """Der Anspruch ist atomar, und er wird **vor** dem Lauf genommen.

        Ohne ihn liefe der Takt in eine heisse Schleife: derselbe faellige
        Auftrag, jede Minute ein Anbieteraufruf.
        """
        user = _benutzer(db)
        server = _server(db)
        _freigabe(db, user, server)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user)

        starter = AsyncMock(side_effect=_lauf_vortaeuschen)
        with (
            patch.object(ai_run_service, "http_client", lambda: object()),
            patch.object(ai_guardian_service, "heilungslauf_starten", starter),
        ):
            erster = await reparatur.faellige_bearbeiten(db)
            zweiter = await reparatur.faellige_bearbeiten(db)

        assert erster == 1
        assert zweiter == 0
        assert starter.await_count == 1
        db.refresh(auftrag)
        assert auftrag.attempt == 1
        # Der Verweis auf den begonnenen Anlauf steht **sofort**. An ihm
        # erkennt der naechste Takt, dass noch gearbeitet wird, und der
        # Schlussbericht, ueber welchen Lauf er berichten soll.
        begonnen = db.query(AiRun).filter(AiRun.user_id == user.id).one()
        assert auftrag.last_run_id == begonnen.id

    @pytest.mark.asyncio
    async def test_ein_laufender_lauf_verlaengert_nur_die_leine(self, db: Session):
        """Ein langer Lauf darf nicht neben sich selbst noch einen bekommen."""
        user = _benutzer(db)
        server = _server(db)
        _freigabe(db, user, server)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user)
        _lauf(db, user, auftrag, status="running", stop_reason="")
        auftrag.next_run_at = _jetzt() - timedelta(seconds=1)
        db.commit()

        starter = AsyncMock(side_effect=_lauf_vortaeuschen)
        with (
            patch.object(ai_run_service, "http_client", lambda: object()),
            patch.object(ai_guardian_service, "heilungslauf_starten", starter),
        ):
            assert await reparatur.faellige_bearbeiten(db) == 0

        assert starter.await_count == 0
        db.refresh(auftrag)
        assert auftrag.phase == "diagnose"
        assert _utc(auftrag.next_run_at) > _jetzt()

    @pytest.mark.asyncio
    async def test_die_abgelaufene_frist_beendet_ohne_einen_versuch(self, db: Session):
        """Frist und Deckel **vor** dem Anspruch.

        Sonst verbrauchte ein Auftrag, der ohnehin nicht mehr laufen darf, noch
        einen Versuch — und saehe im Nachhinein aus, als haette er einen Anlauf
        mehr gehabt, als er hatte.
        """
        user = _benutzer(db)
        server = _server(db)
        _freigabe(db, user, server)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff", attempt=3)
        auftrag.deadline_at = _jetzt() - timedelta(minutes=1)
        db.commit()

        starter = AsyncMock(side_effect=_lauf_vortaeuschen)
        with (
            patch.object(ai_run_service, "http_client", lambda: object()),
            patch.object(ai_guardian_service, "heilungslauf_starten", starter),
        ):
            assert await reparatur.faellige_bearbeiten(db) == 0

        assert starter.await_count == 0
        db.refresh(auftrag)
        assert auftrag.phase == "aufgegeben"
        assert auftrag.attempt == 3

    @pytest.mark.asyncio
    async def test_eine_zurueckgenommene_freigabe_beendet_den_auftrag(self, db: Session):
        """Geprueft wird bei **jedem** Anlauf, nicht einmal beim Anlegen.

        Wer den Autonom-Schalter um drei Uhr umlegt, hat ab drei Uhr recht — und
        nicht erst, wenn der Auftrag von selbst ausgeht.
        """
        user = _benutzer(db)
        server = _server(db)
        _freigabe(db, user, server)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user)
        grant = db.query(AiAutonomyGrant).filter(AiAutonomyGrant.user_id == user.id).one()
        grant.enabled = False
        db.commit()

        starter = AsyncMock(side_effect=_lauf_vortaeuschen)
        with (
            patch.object(ai_run_service, "http_client", lambda: object()),
            patch.object(ai_guardian_service, "heilungslauf_starten", starter),
        ):
            assert await reparatur.faellige_bearbeiten(db) == 0

        assert starter.await_count == 0
        db.refresh(auftrag)
        assert auftrag.phase == "abgebrochen"

    @pytest.mark.asyncio
    async def test_ein_beendeter_auftrag_wird_nicht_mehr_geweckt(self, db: Session):
        """Eine Endphase ist endgueltig, auch mit einem alten Termin daran."""
        user = _benutzer(db)
        server = _server(db)
        _freigabe(db, user, server)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="erledigt")
        auftrag.next_run_at = _jetzt() - timedelta(minutes=5)
        db.commit()

        starter = AsyncMock(side_effect=_lauf_vortaeuschen)
        with (
            patch.object(ai_run_service, "http_client", lambda: object()),
            patch.object(ai_guardian_service, "heilungslauf_starten", starter),
        ):
            assert await reparatur.faellige_bearbeiten(db) == 0

        assert starter.await_count == 0

    @pytest.mark.asyncio
    async def test_ohne_laufzeit_passiert_gar_nichts(self, db: Session):
        """Keine Anwendung, keine Ereignisschleife, kein Lauf.

        Die Termine bleiben stehen. Ein Auftrag, der ohne Laufzeit "verbraucht"
        wuerde, haette einen Versuch fuer nichts bezahlt.
        """
        user = _benutzer(db)
        server = _server(db)
        _freigabe(db, user, server)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user)

        with patch.object(ai_run_service, "http_client", lambda: None):
            assert await reparatur.faellige_bearbeiten(db) == 0

        db.refresh(auftrag)
        assert auftrag.attempt == 0


async def _lauf_vortaeuschen(db, *, server, vorfall, user, auftrag=None):
    """Ersatz fuer `heilungslauf_starten` ohne Anbieter und ohne Netz.

    Traegt bewusst **nicht** `last_run_id` nach: das ist Sache des Takts, und
    der Test darunter prueft genau das.
    """
    fenster = _fenster(db, user)
    run = AiRun(
        id=str(uuid4()),
        user_id=user.id,
        conversation_id=fenster.id,
        status="running",
    )
    db.add(run)
    db.commit()
    return run


# ── Ein Mensch uebernimmt ─────────────────────────────────────────────────


class TestUebernehmen:
    def test_uebernehmen_beendet_den_auftrag_und_nicht_nur_den_lauf(self, db: Session):
        """Nur den Lauf zu beenden hiesse, dass der Takt gleich den naechsten startet.

        Der Mensch haette uebernommen, und die KI arbeitete weiter — genau der
        Zustand, den der Knopf beenden soll.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="eingriff")

        assert reparatur.uebernehmen(db, user=user) == 1

        db.refresh(auftrag)
        assert auftrag.phase == "abgebrochen"
        assert auftrag.next_run_at is None

    def test_uebernehmen_laesst_fremde_auftraege_stehen(self, db: Session):
        """Der Knopf gehoert einem Fenster, und ein Fenster gehoert einem Menschen."""
        einer = _benutzer(db, "einer")
        anderer = _benutzer(db, "anderer")
        server = _server(db)
        auftrag = _auftrag(db, _vorfall(db, server), server, anderer, phase="eingriff")

        assert reparatur.uebernehmen(db, user=einer) == 0

        db.refresh(auftrag)
        assert auftrag.phase == "eingriff"

    def test_der_endpunkt_beendet_auftrag_und_lauf(
        self, db: Session, client, owner_user, owner_cookies, csrf_token
    ):
        """Beides, und in dieser Reihenfolge.

        Nur den Auftrag zu beenden liesse den laufenden Anlauf weiterarbeiten;
        nur den Lauf zu beenden liesse den Takt neunzig Sekunden spaeter den
        naechsten starten. Und die Reihenfolge zaehlt: umgekehrt faende der Takt
        zwischen beiden Schritten einen Auftrag ohne laufenden Lauf und startete
        genau den naechsten Anlauf, den die Uebernahme verhindern soll.
        """
        server = _server(db)
        auftrag = _auftrag(db, _vorfall(db, server), server, owner_user, phase="eingriff")
        lauf = _lauf(db, owner_user, auftrag, status="running", stop_reason="")

        antwort = client.post(
            "/api/ai/conversation/guardian/takeover",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
        )

        assert antwort.status_code == 200, antwort.text
        assert antwort.json()["aborted"] == 1
        db.expire_all()
        assert db.get(AiGuardianRepair, auftrag.id).phase == "abgebrochen"
        assert db.get(AiRun, lauf.id).status == "cancelled"


# ── Die Mail ──────────────────────────────────────────────────────────────


class TestEineMailJeAuftrag:
    def test_ein_laufender_auftrag_haelt_die_mail_zurueck(self, db: Session):
        """Acht Anlaeufe waeren acht Mails, sieben davon "nicht behoben"."""
        from services import ai_guardian_report

        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        auftrag = _auftrag(db, vorfall, server, user, phase="diagnose")
        run = _lauf(db, user, auftrag)

        with patch.object(ai_guardian_report, "_zustellen") as versand:
            ai_guardian_report.bericht_versenden(db, run=run, zustand=_zustand(run))

        assert versand.call_count == 0

    def test_der_beendete_auftrag_schickt_genau_eine(self, db: Session):
        """Und am Ende kommt sie — mit dem Ergebnis des **Auftrags**.

        `geheilt` kommt aus der Phase und nicht aus dem Endzustand des letzten
        Laufs: der kann am Rundenbudget geendet sein, waehrend der Server laengst
        laeuft.
        """
        from services import ai_guardian_report

        user = _benutzer(db)
        server = _server(db, desired_power_state="running", guardian_observed_state="healthy")
        vorfall = _vorfall(db, server, status="resolved")
        auftrag = _auftrag(db, vorfall, server, user, phase="beobachtung")
        run = _lauf(db, user, auftrag, status="completed", stop_reason="budget")

        reparatur.lauf_beendet(db, run, _zustand(run))
        with (
            patch.object(ai_guardian_report, "_zustellen") as versand,
            patch("services.ai_mail.empfaenger", lambda db, user: "owner@test.de"),
        ):
            ai_guardian_report.bericht_versenden(db, run=run, zustand=_zustand(run))

        assert versand.call_count == 1
        assert versand.call_args.kwargs["geheilt"] is True
