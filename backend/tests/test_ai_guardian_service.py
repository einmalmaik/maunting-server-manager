"""Wer heilt, und wie oft.

Zwei Fragen entscheiden ueber diese Kopplung, und beide werden hier gestellt.

**Wer.** Ein Heilungslauf laeuft ohne Menschen davor. Er handelt trotzdem im
Namen eines: desjenigen, der fuer diesen Server den Autonom-Schalter umgelegt
hat. Seine Rechte sind die Grenze, sein Kontingent wird verbraucht, er bekommt
die Mail. Waehlte man hier einen Dienstbenutzer oder den Owner, waere die
Rechtepruefung eine Pruefung gegen einen Account, den jemand aussuchen kann —
in diesem Projekt schon einmal als Befund aufgeschlagen.

**Wie oft.** Der Takt sieht alle sechzig Sekunden nach, und ein Vorfall bleibt
offen, bis ihn jemand loest. Ohne Entdopplung startete derselbe Vorfall jede
Minute einen weiteren Lauf; das Kontingent des Benutzers waere in einer
Viertelstunde aufgebraucht. Die Eindeutigkeit liegt deshalb in der Datenbank
und nicht in einer Pruefung davor.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from models import (
    AiAutonomyGrant,
    AiConversation,
    AiGuardianNotice,
    AiGuardianRepair,
    AiProvider,
    AiRun,
    Incident,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_context_window, ai_guardian_service, ai_run_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


KI_RECHTE = ("ai.chat.use", "ai.autonomous.use")


def _benutzer(db: Session, name: str, *, rechte=KI_RECHTE, aktiv: bool = True) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    user.is_active = aktiv
    db.commit()
    rolle = Role(name=f"ki-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in rechte:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def _server(db: Session, name: str = "Guardian-Server") -> Server:
    server = Server(
        name=name,
        game_type="dayz",
        install_dir="/tmp/guardian-server",
        container_name="msm-guardian",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _sichtbar(db: Session, user: User, server: Server) -> None:
    db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key="server.view"))
    db.commit()


def _freigabe(
    db: Session, user: User, *, server: Server | None, enabled: bool = True, budget: int = 10
) -> AiAutonomyGrant:
    grant = AiAutonomyGrant(
        user_id=user.id,
        server_id=None if server is None else server.id,
        enabled=enabled,
        max_actions_per_hour=budget,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return grant


def _vorfall(
    db: Session, server: Server, *, status: str = "open", alter_minuten: int = 0
) -> Incident:
    vorfall = Incident(
        server_id=server.id,
        title="Autopilot: process_not_running",
        description="GameThread haengt",
        type="process_not_running",
        status=status,
        fingerprint=f"guardian:{server.id}:process_not_running",
        occurrences=3,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=alter_minuten),
    )
    db.add(vorfall)
    db.commit()
    db.refresh(vorfall)
    return vorfall


# ── Wer ist zustaendig? ───────────────────────────────────────────────────


class TestFreigeberWahl:
    def test_no_grant_means_nobody(self, db: Session):
        """Ohne Freigabe ist niemand zustaendig — und damit passiert nichts.

        Kein Lauf, kein Anbieteraufruf, kein Token. Der Vorfall wird beim
        naechsten Chat erwaehnt, mehr nicht.
        """
        server = _server(db)
        user = _benutzer(db, "ohnefreigabe")
        _sichtbar(db, user, server)

        assert ai_guardian_service.zustaendiger_freigeber(db, server) is None

    def test_a_disabled_server_grant_beats_the_panel_wide_one(self, db: Session):
        """Der Fall, an dem eine eigene Abfrage gescheitert waere.

        `resolve_grant` filtert bewusst **nicht** auf `enabled`, damit eine
        gezielt abgeschaltete Server-Zeile die panelweite Freigabe ueberstimmt.
        Wer hier selbst abfragt und dabei `enabled == True` filtert, macht aus
        dem "auf diesem Server ausdruecklich nicht" ein "dann eben panelweit" —
        und heilt autonom genau den Server, den der Betreiber ausgenommen hat.
        """
        server = _server(db)
        user = _benutzer(db, "ausgenommen")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=None, enabled=True)
        _freigabe(db, user, server=server, enabled=False)

        assert ai_guardian_service.zustaendiger_freigeber(db, server) is None

    def test_a_server_grant_wins_over_a_panel_wide_one(self, db: Session):
        """Wer eigens fuer diesen Server zugestimmt hat, hat konkreter zugestimmt."""
        server = _server(db)
        panelweit = _benutzer(db, "panelweit")
        serverbezogen = _benutzer(db, "serverbezogen")
        for user in (panelweit, serverbezogen):
            _sichtbar(db, user, server)
        _freigabe(db, panelweit, server=None)
        _freigabe(db, serverbezogen, server=server)

        gewaehlt = ai_guardian_service.zustaendiger_freigeber(db, server)

        assert gewaehlt is not None
        assert gewaehlt.id == serverbezogen.id

    def test_an_inactive_user_is_no_actor(self, db: Session):
        """Ein gesperrter Account darf auch nicht mittelbar handeln.

        Die Sperre wirkt sonst nur beim Anmelden. Hier gibt es keine Anmeldung —
        deshalb wird sie ausdruecklich geprueft.
        """
        server = _server(db)
        user = _benutzer(db, "gesperrt", aktiv=False)
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)

        assert ai_guardian_service.zustaendiger_freigeber(db, server) is None

    def test_missing_chat_permission_disqualifies(self, db: Session):
        """`ai.chat.use` haengt sonst als `require_global` am Endpunkt.

        Ohne Request laeuft es nicht. Ein Lauf ohne dieses Recht waere ein
        Zugang zur KI an der Rechteverwaltung vorbei — jemand ohne Chatrecht
        haette einen Assistenten, den er sich nicht selbst oeffnen koennte.
        """
        server = _server(db)
        user = _benutzer(db, "ohnechat", rechte=("ai.autonomous.use",))
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)

        assert ai_guardian_service.zustaendiger_freigeber(db, server) is None

    def test_missing_server_view_disqualifies(self, db: Session):
        """Wer den Server nicht sehen darf, darf ihn nicht heilen lassen."""
        server = _server(db)
        user = _benutzer(db, "blind")
        _freigabe(db, user, server=None)

        assert ai_guardian_service.zustaendiger_freigeber(db, server) is None

    def test_a_zero_budget_disqualifies(self, db: Session):
        """Budget 0 heisst "frag mich" — und niemand ist da zum Fragen.

        Ein Lauf, der sofort auf eine Bestaetigung wartet, ist keine Heilung,
        sondern eine Zeile in der Datenbank, die einen Vorfall als versorgt
        markiert, ohne es zu sein.
        """
        server = _server(db)
        user = _benutzer(db, "kontingentlos")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server, budget=0)

        assert ai_guardian_service.zustaendiger_freigeber(db, server) is None


# ── Entdopplung ───────────────────────────────────────────────────────────


class TestEntdopplung:
    @pytest.mark.asyncio
    async def test_the_second_tick_does_not_create_a_second_repair(self, db: Session):
        """Zwei Takte, ein Auftrag.

        Ohne diese Zusage bekaeme derselbe offene Vorfall jede Minute einen
        weiteren Auftrag — und damit jede Minute einen weiteren Lauf, denn jeder
        Auftrag ist sofort faellig.
        """
        server = _server(db)
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)
        _vorfall(db, server)

        erster = await ai_guardian_service.vorfaelle_bearbeiten(db)
        zweiter = await ai_guardian_service.vorfaelle_bearbeiten(db)

        assert erster == 1
        assert zweiter == 0
        assert db.query(AiGuardianRepair).count() == 1

    @pytest.mark.asyncio
    async def test_the_tick_creates_the_order_but_starts_no_run(self, db: Session):
        """Angelegt, nicht gestartet — und das ist keine Kleinigkeit.

        Es gibt genau **einen** Weg, auf dem ein Reparaturlauf beginnt:
        `ai_guardian_repair_service.faellige_bearbeiten`, mit einer atomaren
        Anspruchnahme davor. Ein zweiter Weg hier waere eine zweite Stelle, an
        der jemand den Anspruch vergessen kann — und ein vergessener Anspruch
        ist eine heisse Schleife, die jede Minute einen Anbieteraufruf kostet.
        """
        server = _server(db)
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)
        _vorfall(db, server)

        starter = AsyncMock(return_value=None)
        with patch.object(ai_guardian_service, "heilungslauf_starten", starter):
            assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 1

        assert starter.await_count == 0
        auftrag = db.query(AiGuardianRepair).one()
        assert auftrag.phase == "diagnose"
        assert auftrag.attempt == 0
        # Sofort faellig: der zweite Durchgang desselben Takts holt ihn ab.
        assert auftrag.next_run_at is not None

    @pytest.mark.asyncio
    async def test_a_resolved_incident_is_left_alone(self, db: Session):
        """`resolved` ist erledigt, `verifying` laeuft gerade.

        Bei `verifying` prueft der Agent selbst nach, ob seine Massnahme
        gegriffen hat. Ein Eingriff mittendrin waere ein Rennen zwischen zwei
        Heilungen auf demselben Container.
        """
        server = _server(db)
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)
        _vorfall(db, server, status="resolved")
        _vorfall(db, server, status="verifying")

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 0
        assert db.query(AiGuardianRepair).count() == 0


# ── Briefing sperrt die Heilung nicht ─────────────────────────────────────


def _notiz(
    db: Session, vorfall: Incident, user: User, *, mode: str, run_id: str | None = None
) -> AiGuardianNotice:
    """Eine Notiz von Hand — der Zustand, den der Ausloeser vorfindet."""
    zeile = AiGuardianNotice(
        incident_id=vorfall.id, user_id=user.id, mode=mode, run_id=run_id
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return zeile


def _auftrag(
    db: Session,
    vorfall: Incident,
    user: User,
    server: Server,
    *,
    phase: str = "diagnose",
) -> AiGuardianRepair:
    """Ein Reparaturauftrag von Hand — der Zustand, den der Takt vorfindet."""
    from uuid import uuid4

    zeile = AiGuardianRepair(
        id=str(uuid4()),
        incident_id=vorfall.id,
        server_id=server.id,
        user_id=user.id,
        phase=phase,
        attempt=0,
        next_run_at=None,
        deadline_at=datetime.now(timezone.utc) + timedelta(hours=6),
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return zeile


def _lauf(db: Session, user: User, *, lauf_id: str) -> AiRun:
    """Ein festgeschriebener Lauf, auf den eine `run_id` zeigen darf.

    `ai_guardian_notices.run_id` ist ein echter Fremdschluessel, und die
    Testsuite stellt Fremdschluessel scharf (`PRAGMA foreign_keys=ON`). Eine
    erfundene Kennung faellt hier also sofort auf statt erst im Betrieb.
    """
    conversation = (
        db.query(AiConversation).filter(AiConversation.user_id == user.id).first()
    )
    if conversation is None:
        conversation = AiConversation(
            id=f"conv-{user.id}", user_id=user.id, server_id=None, title="Guardian"
        )
        db.add(conversation)
        db.commit()
    run = AiRun(
        id=lauf_id, user_id=user.id, conversation_id=conversation.id, status="running"
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _anbieter(db: Session) -> AiProvider:
    """Der eine aktivierte Anbieter, den `_anbieter_waehlen` findet.

    Genau einer: bei mehreren und ohne juengsten Lauf waehlt der Dienst bewusst
    gar keinen, und der Test pruefte dann eine Absage statt einer Heilung.
    """
    anbieter = AiProvider(
        name="Heilung",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )
    db.add(anbieter)
    db.commit()
    db.refresh(anbieter)
    return anbieter


async def _echte_heilung(db: Session, server: Server, vorfall: Incident, user: User):
    """Ruft `heilungslauf_starten` wirklich auf — nur ohne Anwendung darum herum.

    Ersetzt wird ausschliesslich, was eine laufende Anwendung braucht: der
    HTTP-Client, die beiden Abfragen an den Modellkatalog und das Anwerfen des
    Segments. Alles dazwischen laeuft echt — vor allem `lauf_beginnen`, denn
    dessen Commit ist die eine Eigenschaft, an der die Tests unten haengen: der
    Lauf steht fest, **bevor** der Guardian-Rahmen in seinen Zustand geschrieben
    und die Notiz angelegt wird. Ein Ersatz an dieser Stelle koennte genau diese
    Reihenfolge falsch nachstellen und den Befund verdecken.
    """
    with (
        patch.object(ai_run_service, "http_client", lambda: object()),
        patch.object(
            ai_guardian_service.ai_reasoning,
            "vorgabe",
            AsyncMock(return_value=(False, None)),
        ),
        patch.object(
            ai_guardian_service.ai_context_window,
            "ermitteln",
            AsyncMock(return_value=ai_context_window.unbekannt()),
        ),
        patch("services.ai_run_broker.eroeffnen", lambda run_id: None),
        patch.object(ai_run_service, "lauf_starten", lambda run_id: True),
    ):
        return await ai_guardian_service.heilungslauf_starten(
            db, server=server, vorfall=vorfall, user=user
        )


class TestBriefingUndHeilung:
    """Eine Erwaehnung im Chat ist keine Behandlung.

    Der Briefingpfad kennt die Freigabe nicht: `briefing_nachricht` haengt jeden
    offenen Vorfall an, den der Benutzer sehen darf, und `briefings_abschliessen`
    vermerkt ihn danach mit `mode='briefed'`. Solange der Ausloeser nur nachsah,
    *ob* es eine Notiz gibt, entschied damit ein Zufall von sechzig Sekunden
    darueber, ob ein Server nachts wieder hochkommt: schrieb der Freigeber vor
    dem naechsten Takt irgendetwas in den Chat, war der Vorfall vermerkt — und
    blieb fuer immer liegen, obwohl die Autonomie eingeschaltet war.

    Die Entdopplung selbst bleibt davon unberuehrt. Sie ist der Grund, warum es
    diese Tabelle gibt, und liegt seit `20260816_12` beim Reparaturauftrag —
    die Notiz sperrt nur noch den Altbestand.
    """

    @pytest.mark.asyncio
    async def test_a_briefing_notice_does_not_block_the_repair(self, db: Session):
        """Der Kern der Behebung: gebrieft ist nicht behandelt.

        Der Vorfall ist erwaehnt worden, mehr nicht — kein Lauf, kein Eingriff,
        `run_id` ist NULL. Wer daraus ableitet, es sei etwas veranlasst worden,
        laesst den Server stehen.
        """
        server = _server(db)
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)
        vorfall = _vorfall(db, server)
        _notiz(db, vorfall, user, mode="briefed")

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 1

        auftrag = db.query(AiGuardianRepair).one()
        assert auftrag.incident_id == vorfall.id
        # Die Briefingzeile bleibt unangetastet — hochgestuft wird sie erst,
        # wenn der erste Anlauf wirklich laeuft.
        zeilen = db.query(AiGuardianNotice).all()
        assert len(zeilen) == 1
        assert zeilen[0].mode == "briefed"
        # Und der Vorfall wird kein zweites Mal gebrieft: die Zeile steht ja.
        assert ai_guardian_service.offene_briefings(db, user) == []

    @pytest.mark.asyncio
    async def test_an_existing_repair_blocks_the_tick(self, db: Session):
        """Die Entdopplung bleibt — sonst waere die Behebung ein Ruecksturz.

        Ohne sie bekaeme derselbe offene Vorfall jede Minute einen weiteren
        Auftrag, und das Kontingent des Freigebers waere in einer Viertelstunde
        aufgebraucht. Geprueft wird auch der **beendete** Auftrag: eine Zeile in
        einer Endphase heisst "dieser Vorfall ist durch", nicht "der Platz ist
        wieder frei".
        """
        server = _server(db)
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)
        vorfall = _vorfall(db, server)

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 1
        auftrag = db.query(AiGuardianRepair).one()
        auftrag.phase = "aufgegeben"
        auftrag.next_run_at = None
        db.commit()

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 0
        assert db.query(AiGuardianRepair).count() == 1
        assert vorfall.status == "open"

    @pytest.mark.asyncio
    async def test_a_legacy_healing_notice_still_blocks_the_tick(self, db: Session):
        """Altbestand bekommt keine Rechnung nachgereicht.

        Vor dieser Aenderung war die Heilungsnotiz die Sperre: ein Vorfall, der
        seinen einen Lauf hatte, blieb liegen. Faellt sie als Filter weg,
        bekaeme beim ersten Takt nach dem Update *jeder* noch offene Vorfall
        einen frischen Auftrag mit bis zu acht Anlaeufen — auf einem Panel mit
        zwanzig Dauerbelegern hundertsechzig Anbieteraufrufe, die niemand
        bestellt hat.

        Fuer alles Neue ist die Zeile bedeutungslos: seit es Auftraege gibt,
        entsteht eine Heilungsnotiz nur noch **mit** einem Auftrag daneben.
        """
        server = _server(db)
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)
        vorfall = _vorfall(db, server)
        lauf = _lauf(db, user, lauf_id="run-laeuft-schon")
        _notiz(db, vorfall, user, mode="healing", run_id=lauf.id)

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 0
        assert db.query(AiGuardianRepair).count() == 0

    def test_notiz_anlegen_upgrades_a_briefing_to_a_healing(self, db: Session):
        """Hochgestuft wird die vorhandene Zeile, nicht eine zweite angelegt.

        Beides gehoert zusammen: `mode` und `run_id` muessen danach die Heilung
        bezeichnen, und die Eindeutigkeit je Paar aus Vorfall und Benutzer muss
        halten. Entstuende eine zweite Zeile, waere die Bedingung umgangen, und
        der naechste Takt saehe zwei Wahrheiten zu einem Vorfall.
        """
        server = _server(db)
        user = _benutzer(db, "hochstufer")
        vorfall = _vorfall(db, server)
        lauf = _lauf(db, user, lauf_id="run-hochgestuft")
        _notiz(db, vorfall, user, mode="briefed")

        angelegt = ai_guardian_service._notiz_anlegen(
            db, incident_id=vorfall.id, user_id=user.id, mode="healing", run_id=lauf.id
        )
        db.commit()

        assert angelegt is True
        zeilen = db.query(AiGuardianNotice).all()
        assert len(zeilen) == 1
        assert zeilen[0].mode == "healing"
        assert zeilen[0].run_id == lauf.id

    def test_a_healing_is_never_downgraded_by_a_later_briefing(self, db: Session):
        """Nur in eine Richtung — sonst faengt der Takt von vorne an.

        `briefings_abschliessen` laeuft am Ende **jedes** Chatlaufs und kennt
        den Unterschied nicht. Stufte es eine begonnene Heilung auf `briefed`
        zurueck, saehe der naechste Takt keine Heilungsnotiz mehr und startete
        einen zweiten Lauf auf denselben Vorfall — waehrend der erste noch
        arbeitet.

        Gerufen wird bewusst der echte Weg und nicht `_notiz_anlegen` von Hand:
        die Rueckstufung kaeme im Betrieb genau von dort.
        """
        server = _server(db)
        user = _benutzer(db, "nichtzurueck")
        vorfall = _vorfall(db, server)
        lauf = _lauf(db, user, lauf_id="run-heilt")
        _notiz(db, vorfall, user, mode="healing", run_id=lauf.id)

        ai_guardian_service.briefings_abschliessen(
            db, user_id=user.id, incident_ids=[vorfall.id]
        )
        db.commit()

        zeilen = db.query(AiGuardianNotice).all()
        assert len(zeilen) == 1
        assert zeilen[0].mode == "healing"
        # Der Verweis auf den laufenden Lauf bleibt stehen. Ohne ihn waere im
        # Nachhinein nicht mehr zu sagen, was zu diesem Vorfall geschehen ist.
        assert zeilen[0].run_id == lauf.id

    def test_a_second_healing_notice_is_refused(self, db: Session):
        """Zweimal `healing` bleibt eine Absage — die Entdopplung im Kleinen.

        Die Hochstufung ist eine Ausnahme fuer `briefed` und keine allgemeine
        Erlaubnis, die Zeile zu ueberschreiben. Duerfte ein zweiter Aufruf die
        `run_id` umbiegen, uebernaehme der spaetere Lauf die Zeile des frueheren
        — und der frueher gestartete liefe unbemerkt weiter.
        """
        server = _server(db)
        user = _benutzer(db, "zweimal")
        vorfall = _vorfall(db, server)
        erster = _lauf(db, user, lauf_id="run-erster")
        zweiter = _lauf(db, user, lauf_id="run-zweiter")
        _notiz(db, vorfall, user, mode="healing", run_id=erster.id)

        angelegt = ai_guardian_service._notiz_anlegen(
            db,
            incident_id=vorfall.id,
            user_id=user.id,
            mode="healing",
            run_id=zweiter.id,
        )
        db.commit()

        assert angelegt is False
        zeilen = db.query(AiGuardianNotice).all()
        assert len(zeilen) == 1
        assert zeilen[0].run_id == erster.id

    def test_a_briefing_of_another_user_does_not_upgrade(self, db: Session):
        """Die Notiz gehoert dem Paar aus Vorfall **und** Benutzer.

        Zwei Menschen koennen denselben Server sehen. Erwaehnt die KI den
        Vorfall gegenueber dem einen, sagt das nichts darueber, was fuer den
        anderen veranlasst wurde — und umgekehrt darf die Heilung des einen
        nicht die Briefingzeile des anderen umschreiben.
        """
        server = _server(db)
        freigeber = _benutzer(db, "handelnder")
        zuschauer = _benutzer(db, "zuschauer")
        vorfall = _vorfall(db, server)
        lauf = _lauf(db, freigeber, lauf_id="run-fremd")
        _notiz(db, vorfall, zuschauer, mode="briefed")

        angelegt = ai_guardian_service._notiz_anlegen(
            db,
            incident_id=vorfall.id,
            user_id=freigeber.id,
            mode="healing",
            run_id=lauf.id,
        )
        db.commit()

        assert angelegt is True
        assert db.query(AiGuardianNotice).count() == 2
        fremde = (
            db.query(AiGuardianNotice)
            .filter(AiGuardianNotice.user_id == zuschauer.id)
            .one()
        )
        assert fremde.mode == "briefed"
        assert fremde.run_id is None


class TestRahmenDerHochgestuftenHeilung:
    """Was in der hochgestuften Heilung noch gilt.

    Der Guardian-Rahmen im Laufzustand ist keine Beschreibung, sondern die
    Sicherung selbst: aus ihm kommen die eingeschraenkte Werkzeugmenge, die
    feste Serverbindung und der Backup-Nachweis vor jedem Eingriff. Ein
    Heilungslauf ohne ihn ist ein gewoehnlicher Chatlauf mit vollem Werkzeugsatz
    — nur dass niemand mitliest.
    """

    @pytest.mark.asyncio
    async def test_a_fresh_healing_carries_the_guardian_frame(self, db: Session):
        """Die Gegenprobe zuerst: ohne Vorgeschichte steht der Rahmen im Lauf.

        Ohne diesen Test bewiese der naechste nichts — er koennte auch dann rot
        sein, wenn der Rahmen ueberhaupt nie geschrieben wuerde.
        """
        server = _server(db)
        user = _benutzer(db, "frisch")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)
        vorfall = _vorfall(db, server)
        _anbieter(db)

        run = await _echte_heilung(db, server, vorfall, user)

        assert run is not None
        db.refresh(run)
        rahmen = json.loads(run.state_json or "{}").get("guardian") or {}
        assert rahmen.get("server_id") == server.id
        assert rahmen.get("incident_id") == vorfall.id
        assert rahmen.get("backup_anker")

    @pytest.mark.asyncio
    async def test_an_upgraded_healing_keeps_the_guardian_frame(self, db: Session):
        """Derselbe Lauf, nur mit einer Briefingzeile davor.

        Der Weg dorthin ist genau der, den die Behebung erst geoeffnet hat:
        Guardian legt den Vorfall an, der Freigeber schreibt vor dem naechsten
        Takt etwas in den Chat, `briefings_abschliessen` vermerkt ihn als
        `briefed`. Der naechste Takt heilt jetzt — er soll dabei aber nicht die
        Verschaerfungen verlieren, deretwegen eine Heilung ueberhaupt zu
        verantworten ist.

        Geprueft wird der Rahmen und nicht die Notiz: dass die Heilung anlaeuft,
        steht schon oben. Hier geht es darum, **wie** sie laeuft.

        Die Hochstufung laeuft ueber eine `IntegrityError` mit `db.rollback()`.
        Stand der Rahmen davor schon ungespeichert am Lauf, nahm das Rollback
        ihn mit — und die Heilung startete unbeaufsichtigt als gewoehnlicher
        Chatlauf. Deshalb schreibt `heilungslauf_starten` ihn erst danach.
        """
        server = _server(db)
        user = _benutzer(db, "hochgestuft")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)
        vorfall = _vorfall(db, server)
        _anbieter(db)
        _notiz(db, vorfall, user, mode="briefed")

        run = await _echte_heilung(db, server, vorfall, user)

        assert run is not None
        db.refresh(run)
        rahmen = json.loads(run.state_json or "{}").get("guardian") or {}
        assert rahmen.get("server_id") == server.id
        assert rahmen.get("incident_id") == vorfall.id
        assert rahmen.get("backup_anker")


# ── Das Fenster darf nicht zuwachsen ──────────────────────────────────────


class TestFensterVerstopfung:
    """Ein Vorfall, den niemand heilen wird, darf keinen Platz belegen.

    Der Takt nimmt die zwanzig ältesten offenen Vorfälle. Offen bleibt ein
    Vorfall aber auch dann, wenn er nie behandelt werden kann: `quarantined` ist
    der Zustand, in dem die Guardian-Engine aufgegeben hat, und von allein
    wechselt er nie. Zwanzig davon — nach einem Node-Ausfall keine künstliche
    Zahl — schlossen das Fenster für immer. Jeder neue Vorfall stand dahinter,
    der Autonom-Schalter stand auf an, das Panel zeigte ihn als an, und es
    passierte nichts.
    """

    @pytest.mark.asyncio
    async def test_a_young_incident_is_healed_behind_twenty_without_a_grant(
        self, db: Session
    ):
        """Einundzwanzig ältere ohne Freigabe, ein jüngerer mit — er wird geheilt."""
        ohne = _server(db, "ohne-freigabe")
        mit = _server(db, "mit-freigabe")
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, mit)
        # Ausdrücklich **serverbezogen**: eine panelweite Freigabe deckte auch
        # den anderen Server, und dann wäre nichts verstopft.
        _freigabe(db, user, server=mit)

        for nummer in range(21):
            _vorfall(db, ohne, status="quarantined", alter_minuten=100 + nummer)
        jung = _vorfall(db, mit)

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 1

        auftrag = db.query(AiGuardianRepair).one()
        assert auftrag.incident_id == jung.id

    @pytest.mark.asyncio
    async def test_a_young_incident_is_healed_behind_twenty_already_handled(
        self, db: Session
    ):
        """Der häufigere Dauerbeleger: schon übernommen, aber weiter offen.

        Ein Auftrag verschwindet nie, auch nicht in seiner Endphase, und
        `_einen_vorfall_bearbeiten` steigt bei ihm jedes Mal wieder aus — der
        Vorfall bleibt im Fenster stehen und nimmt einem jüngeren den Platz weg.
        """
        server = _server(db)
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=server)

        for nummer in range(21):
            alt = _vorfall(db, server, alter_minuten=100 + nummer)
            _auftrag(db, alt, user, server, phase="aufgegeben")
        jung = _vorfall(db, server)

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 1

        neu = (
            db.query(AiGuardianRepair)
            .filter(AiGuardianRepair.incident_id == jung.id)
            .one()
        )
        assert neu.phase == "diagnose"

    @pytest.mark.asyncio
    async def test_the_panel_wide_grant_still_reaches_every_server(self, db: Session):
        """Der Vorfilter darf die panelweite Freigabe nicht wegfiltern.

        Sie steht mit `server_id IS NULL` in der Tabelle. Wer sie mit
        `IN (None, id)` sucht, findet sie in SQL nicht — derselbe Fehler, der in
        der Kandidatenabfrage schon einmal dafür sorgte, dass panelweit
        freigegebene Server nie geheilt wurden.
        """
        server = _server(db)
        user = _benutzer(db, "panelweit")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=None)
        _vorfall(db, server)

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 1

    @pytest.mark.asyncio
    async def test_the_prefilter_does_not_decide_the_grant_itself(self, db: Session):
        """Panelweit erlaubt, auf diesem Server ausdrücklich nicht.

        Der Vorfilter lässt diesen Vorfall durch — er sieht nur die panelweite
        Zeile. Entscheiden muss weiterhin `resolve_grant`, und das findet die
        abgeschaltete serverbezogene Zeile und sagt Nein. Würde der Vorfilter
        die Entscheidung übernehmen, wäre aus einer Beschleunigung eine stille
        Rechteänderung geworden.
        """
        server = _server(db)
        user = _benutzer(db, "ausgenommen")
        _sichtbar(db, user, server)
        _freigabe(db, user, server=None)
        _freigabe(db, user, server=server, enabled=False)
        _vorfall(db, server)

        assert await ai_guardian_service.vorfaelle_bearbeiten(db) == 0
        assert db.query(AiGuardianRepair).count() == 0
