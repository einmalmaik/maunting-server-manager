"""Zwei Fenster, und warum das kein Ordnungsthema ist.

Bis hierher gab es genau eine Unterhaltung je Benutzer. Fuer einen Assistenten,
den man etwas fragt, war das richtig — und fuer eine Reparatur, die niemand
bestellt hat, war es der Grund, warum sie nicht stattfand:

* Eine Heilung startete nur, wenn der Benutzer gerade **gar nichts** laufen
  hatte. Eine offene Rueckfrage von gestern Abend genuegte, damit nachts kein
  Server mehr anlief.
* Schrieb der Mensch waehrend einer Heilung, loeste `vorgaenger_abloesen` sie ab
  und brach ihre asyncio-Aufgabe wirklich ab. Wer morgens eine Frage tippte,
  beendete damit die Reparatur, die seit vier Uhr lief.

Beides ist dieselbe Ursache, und beides verschwindet, wenn die Reparatur ihr
eigenes Fenster hat. Diese Datei prueft, dass es das tut — und dass es dabei
**nicht** zu einer Ablage geworden ist: mehr als eine Unterhaltung je Art gibt
es weiterhin nicht.

Die Zusagen, die die Datenbank selbst haelt (Eindeutigkeit ueber
``(user_id, kind)``, die Aufzaehlung der Arten, Auf- und Rueckbau der
Migration), stehen in `test_schema_constraints.py`. Hier steht, was der Dienst
daraus macht.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from models import (
    AiAutonomyGrant,
    AiConversation,
    AiProvider,
    AiRun,
    Incident,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import (
    ai_chat_service,
    ai_context_window,
    ai_guardian_service,
    ai_run_service,
)
from services.auth_service import AuthService
from services.role_service import set_user_roles


KI_RECHTE = ("ai.chat.use", "ai.autonomous.use")


def _benutzer(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    user.is_active = True
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


def _server(db: Session) -> Server:
    server = Server(
        name="Fenster-Server",
        game_type="dayz",
        install_dir="/tmp/fenster-server",
        container_name="msm-fenster",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _sichtbar(db: Session, user: User, server: Server) -> None:
    db.add(
        ServerPermission(
            user_id=user.id, server_id=server.id, permission_key="server.view"
        )
    )
    db.commit()


def _freigabe(db: Session, user: User, server: Server) -> None:
    from services.ai_guardian_settings import set_guardian_ai_enabled
    set_guardian_ai_enabled(True)
    db.add(
        AiAutonomyGrant(
            user_id=user.id,
            server_id=server.id,
            enabled=True,
            max_actions_per_hour=10,
        )
    )
    db.commit()


def _vorfall(db: Session, server: Server) -> Incident:
    vorfall = Incident(
        server_id=server.id,
        title="Autopilot: process_not_running",
        description="GameThread haengt",
        type="process_not_running",
        status="open",
        fingerprint=f"guardian:{server.id}:process_not_running",
        occurrences=3,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db.add(vorfall)
    db.commit()
    db.refresh(vorfall)
    return vorfall


def _anbieter(db: Session) -> AiProvider:
    anbieter = AiProvider(
        name="Fenster",
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

    Ersetzt wird ausschliesslich, was eine laufende Anwendung braucht. Alles
    dazwischen laeuft echt, vor allem die Wahl der Unterhaltung: sie ist genau
    das, was hier geprueft wird. Gleiche Bauart wie in
    `test_ai_guardian_service._echte_heilung`.
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


# ── Die Fabrik ────────────────────────────────────────────────────────────


class TestUnterhaltungenAnlegen:
    def test_zwei_arten_sind_zwei_zeilen(self, db: Session) -> None:
        user = _benutzer(db, "zweifenster")

        chat = ai_chat_service.get_or_create_conversation(db, user, "primary")
        guardian = ai_chat_service.get_or_create_conversation(db, user, "guardian")
        db.commit()

        assert chat.id != guardian.id
        assert chat.kind == "primary"
        assert guardian.kind == "guardian"
        assert db.query(AiConversation).filter_by(user_id=user.id).count() == 2

    def test_dieselbe_art_liefert_dieselbe_zeile(self, db: Session) -> None:
        """Sonst waere aus dem Fenster eine Ablage geworden."""
        user = _benutzer(db, "einmal")

        erste = ai_chat_service.get_or_create_conversation(db, user, "guardian")
        db.commit()
        zweite = ai_chat_service.get_or_create_conversation(db, user, "guardian")
        db.commit()

        assert erste.id == zweite.id
        assert db.query(AiConversation).filter_by(user_id=user.id).count() == 1

    def test_der_wrapper_meint_weiterhin_den_dauerchat(self, db: Session) -> None:
        """Ein Dutzend Aufrufer fragt nach "der" Unterhaltung und meint den Chat."""
        user = _benutzer(db, "wrapper")

        chat = ai_chat_service.get_or_create_primary_conversation(db, user)
        db.commit()

        assert chat.kind == "primary"

    def test_eine_unbekannte_art_faellt_hier_auf(self, db: Session) -> None:
        """Und nicht erst als Integritaetsverletzung in einer fremden Transaktion.

        Der CHECK in der Datenbank haelt denselben Wertebereich. Er wuerde aber
        erst beim Flush zuschlagen — mitten in der Transaktion eines Aufrufers,
        der von dieser Unterhaltung nur ein Nebenprodukt wollte.
        """
        user = _benutzer(db, "tippfehler")

        with pytest.raises(ValueError):
            ai_chat_service.get_or_create_conversation(db, user, "gardian")

    def test_worker_gehen_nicht_durch_die_einzelfenster_fabrik(
        self, db: Session
    ) -> None:
        """`worker` ist eine gueltige Art, aber kein Einzelfenster.

        Die Fabrik hier beantwortet "gib mir *die* Unterhaltung dieser Art" —
        fuer Worker gibt es kein "die". Ein Aufrufer, der hier mit `worker`
        ankommt, haette sonst stillschweigend ein einzelnes, wiederverwendetes
        Worker-Fenster bekommen und die Ein-Fenster-je-Auftrag-Zusage gebrochen.
        """
        user = _benutzer(db, "workerfabrik")

        with pytest.raises(ValueError, match="Einzelfenster"):
            ai_chat_service.get_or_create_conversation(db, user, "worker")

    def test_jeder_auftrag_bekommt_sein_eigenes_fenster(self, db: Session) -> None:
        user = _benutzer(db, "auftraege")

        erster = ai_chat_service.worker_unterhaltung_anlegen(db, user, "Backup pruefen")
        zweiter = ai_chat_service.worker_unterhaltung_anlegen(db, user, "Backup pruefen")
        db.commit()

        assert erster.id != zweiter.id
        assert erster.kind == "worker" and zweiter.kind == "worker"
        assert erster.title == "Backup pruefen"
        assert erster.server_id is None

    def test_ein_leerer_auftragstitel_wird_ersetzt(self, db: Session) -> None:
        """Das Fenster traegt den Namen in der Worker-Liste — leer geht nicht."""
        user = _benutzer(db, "ohnetitel")

        fenster = ai_chat_service.worker_unterhaltung_anlegen(db, user, "   ")
        db.commit()

        assert fenster.title == "Auftrag"

    def test_der_wiedereinstieg_nach_einem_rennen_filtert_auf_die_art(
        self, db: Session
    ) -> None:
        """Der Fall, der ohne den `kind`-Filter die falsche Antwort gaebe.

        Zwei Tabs, zwei Arten, dieselbe Luecke zwischen Pruefung und Insert. Der
        Verlierer landet im Ausnahmezweig und liest die Zeile des Gewinners
        nach — und dieses `.one()` sieht **alle** Zeilen des Benutzers.

        Beide Arten existieren hier deshalb schon. Filterte der Ausnahmezweig
        nur auf `user_id`, faende er zwei Zeilen und riefe `MultipleResultsFound`
        (oder, mit einem `.first()`, stillschweigend die falsche — ein
        Reparaturlauf, der in den Dauerchat schreibt).

        Nachgestellt wird das Rennen, indem allein die **Vorabpruefung** blind
        gemacht wird: ihr `.first()` liefert einmal `None`, als waere die Zeile
        noch nicht da. Alles danach — der Insert, die `IntegrityError`, das
        Nachlesen — laeuft mit den echten Abfragen des Dienstes. Nur so misst
        der Test dessen Filter und nicht den des Tests.
        """
        user = _benutzer(db, "rennen")
        ai_chat_service.get_or_create_conversation(db, user, "primary")
        ai_chat_service.get_or_create_conversation(db, user, "guardian")
        db.commit()

        echte_abfrage = db.query
        blind = {"offen": True}

        class _EinmalBlind:
            def __init__(self, query):
                self._query = query

            def filter(self, *args, **kwargs):
                return _EinmalBlind(self._query.filter(*args, **kwargs))

            def first(self):
                if blind["offen"]:
                    blind["offen"] = False
                    return None
                return self._query.first()

            def __getattr__(self, name):
                return getattr(self._query, name)

        def _mit_luecke(*args, **kwargs):
            ergebnis = echte_abfrage(*args, **kwargs)
            if args and args[0] is AiConversation:
                return _EinmalBlind(ergebnis)
            return ergebnis

        with patch.object(db, "query", _mit_luecke):
            gefunden = ai_chat_service.get_or_create_conversation(db, user, "primary")

        assert gefunden.kind == "primary"
        assert db.query(AiConversation).filter_by(user_id=user.id).count() == 2


# ── Wer laeuft wo ─────────────────────────────────────────────────────────


class TestAktiverLauf:
    def _lauf(self, db: Session, user: User, kind: str, lauf_id: str) -> AiRun:
        conversation = ai_chat_service.get_or_create_conversation(db, user, kind)
        db.commit()
        run = AiRun(
            id=lauf_id,
            user_id=user.id,
            conversation_id=conversation.id,
            status="running",
        )
        db.add(run)
        db.commit()
        return run

    def test_die_art_trennt_die_fenster(self, db: Session) -> None:
        user = _benutzer(db, "getrennt")
        self._lauf(db, user, "guardian", "run-guardian")

        assert ai_run_service.aktiver_lauf(db, user_id=user.id, kind="guardian") is not None
        # **Der Kern.** Ohne diese Zeile haengte sich der Chat an die Reparatur
        # und zeichnete sie in das Fenster des Menschen.
        assert ai_run_service.aktiver_lauf(db, user_id=user.id, kind="primary") is None

    def test_ohne_art_zaehlt_weiterhin_alles(self, db: Session) -> None:
        """Die Glocke fragt so — sie will wissen, ob ueberhaupt etwas laeuft."""
        user = _benutzer(db, "glocke")
        self._lauf(db, user, "guardian", "run-irgendwo")

        assert ai_run_service.aktiver_lauf(db, user_id=user.id) is not None

    def test_ein_fremder_lauf_zaehlt_nie(self, db: Session) -> None:
        """Der Benutzerfilter darf durch den Fensterfilter nicht verlorengehen."""
        einer = _benutzer(db, "einer")
        anderer = _benutzer(db, "anderer")
        self._lauf(db, einer, "guardian", "run-fremd")

        assert (
            ai_run_service.aktiver_lauf(db, user_id=anderer.id, kind="guardian") is None
        )


# ── Die Heilung landet im Hintergrund ─────────────────────────────────────


class TestHeilungImHintergrund:
    @pytest.mark.asyncio
    async def test_der_heilungslauf_schreibt_nicht_in_den_dauerchat(
        self, db: Session
    ) -> None:
        """Die eine Zeile, um die es in diesem Batch geht."""
        server = _server(db)
        user = _benutzer(db, "freigeber")
        _sichtbar(db, user, server)
        _freigabe(db, user, server)
        _anbieter(db)
        vorfall = _vorfall(db, server)

        run = await _echte_heilung(db, server, vorfall, user)

        assert run is not None
        conversation = db.get(AiConversation, run.conversation_id)
        assert conversation.kind == "guardian"

    @pytest.mark.asyncio
    async def test_ein_offener_chatlauf_vertagt_die_heilung_nicht_mehr(
        self, db: Session
    ) -> None:
        """Der Ausfall, der das hier ausgeloest hat.

        Eine Rueckfrage von gestern Abend steht noch offen — ein Lauf im Zustand
        ``waiting_user``, den `aktiver_lauf` mitzaehlt. Vorher hiess das: nachts
        laeuft kein Server mehr an, auf keiner Anlage dieses Benutzers, bis
        jemand die Frage beantwortet. Ohne Log, ohne Fehler.
        """
        server = _server(db)
        user = _benutzer(db, "vielbeschaeftigt")
        _sichtbar(db, user, server)
        _freigabe(db, user, server)
        _anbieter(db)
        vorfall = _vorfall(db, server)

        chat = ai_chat_service.get_or_create_conversation(db, user, "primary")
        db.commit()
        db.add(
            AiRun(
                id="run-mensch-wartet",
                user_id=user.id,
                conversation_id=chat.id,
                status="waiting_user",
            )
        )
        db.commit()

        run = await _echte_heilung(db, server, vorfall, user)

        assert run is not None, "ein offener Chatlauf darf keine Reparatur mehr sperren"
        assert db.get(AiConversation, run.conversation_id).kind == "guardian"

    @pytest.mark.asyncio
    async def test_eine_laufende_reparatur_vertagt_die_naechste(
        self, db: Session
    ) -> None:
        """Die Entdopplung bleibt — nur eben je Fenster statt je Mensch.

        Zwei Reparaturen im selben Fenster wuerden sich ueber
        `vorgaenger_abloesen` gegenseitig abloesen. Der Vorfall bleibt dann offen
        und ohne Notiz; der naechste Takt versucht es erneut.
        """
        server = _server(db)
        user = _benutzer(db, "schonambeit")
        _sichtbar(db, user, server)
        _freigabe(db, user, server)
        _anbieter(db)
        vorfall = _vorfall(db, server)

        guardian = ai_chat_service.get_or_create_conversation(db, user, "guardian")
        db.commit()
        db.add(
            AiRun(
                id="run-repariert-schon",
                user_id=user.id,
                conversation_id=guardian.id,
                status="running",
            )
        )
        db.commit()

        assert await _echte_heilung(db, server, vorfall, user) is None

    @pytest.mark.asyncio
    async def test_eine_getippte_nachricht_reicht_nicht_mehr_hinueber(
        self, db: Session
    ) -> None:
        """`vorgaenger_abloesen` greift je Unterhaltung — jetzt trennt das.

        Vorher war der Mensch, der morgens eine Frage tippte, der haeufigste
        Grund, warum eine Reparatur abbrach. Geprueft wird die Ablesung
        unmittelbar: der Reparaturlauf ist danach unveraendert `running`.
        """
        server = _server(db)
        user = _benutzer(db, "tippt")
        _sichtbar(db, user, server)
        _freigabe(db, user, server)
        _anbieter(db)
        vorfall = _vorfall(db, server)

        run = await _echte_heilung(db, server, vorfall, user)
        assert run is not None

        chat = ai_chat_service.get_or_create_conversation(db, user, "primary")
        db.commit()
        ai_run_service.vorgaenger_abloesen(db, conversation_id=chat.id)
        db.commit()

        db.refresh(run)
        assert run.status == "running"

    def test_stop_active_run_beendet_offenen_lauf(
        self, db: Session, client, regular_user: User, user_cookies: dict
    ) -> None:
        """Der Stop-Endpoint beendet den aktiven Lauf der Unterhaltung."""
        rolle = Role(name=f"ki-stop-{regular_user.id}", description=None, is_system=False)
        db.add(rolle)
        db.flush()
        db.add(RolePermission(role_id=rolle.id, permission_key="ai.chat.use"))
        db.commit()
        set_user_roles(db, regular_user, [rolle.id])

        chat = ai_chat_service.get_or_create_conversation(db, regular_user, "primary")
        run = AiRun(
            id="run-zu-stoppen",
            user_id=regular_user.id,
            conversation_id=chat.id,
            status="running",
        )
        db.add(run)
        db.commit()

        csrf = user_cookies.get("__Secure-csrf_token", "")
        res = client.post(
            "/api/ai/conversation/stop?kind=primary",
            cookies=user_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True, "stopped": True}

        db.refresh(run)
        assert run.status == "cancelled"
