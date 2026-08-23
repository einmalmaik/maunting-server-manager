"""worker_start und worker_cancel: deklarieren, deckeln, einfangen.

docs/agentic-framework.md (Abschnitt 3): Das Gehirn fuehrt nichts aus — es
deklariert einen Auftrag, und der bekommt ein eigenes Fenster samt Lauf.
Diese Tests binden die drei Zusagen des Handlers fest:

* **Rechte**: ohne `ai.background.use` laeuft nichts — das Angebot ist eine
  Bitte, die Handler-Pruefung die Schranke (Muster der Memory-Werkzeuge).
* **Deckel**: der Betreiber-Deckel zaehlt auch geparkte Laeufe — ein
  wartender Langlaeufer ist ein offener Auftrag, kein freier Platz.
* **Eigentum**: eingefangen wird nur das eigene Fenster; ein fremdes
  existiert fuer den Aufrufer nicht.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Query, Session

from models import AiConversation, AiRun, Role, RolePermission, User
from services import (
    ai_provider_service,
    ai_run_service,
    ai_stream_service,
    ai_worker_service,
)
from services.ai_action_service import AiActionValidationError
from services.role_service import set_user_roles


def _benutzer(db: Session, name: str, *, mit_recht: bool = True) -> User:
    user = User(
        username=name,
        email_encrypted="x",
        email_hash=name,
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.flush()
    if mit_recht:
        role = Role(name=f"worker-{name}", description=None, is_system=False)
        db.add(role)
        db.flush()
        db.add(RolePermission(role_id=role.id, permission_key="ai.background.use"))
        db.commit()
        set_user_roles(db, user, [role.id])
    db.commit()
    return user


def _provider(db: Session, **extra):
    provider = ai_provider_service.create_provider(
        db,
        name=extra.pop("name", "Zugang"),
        provider_kind="openrouter",
        default_model="schnelles-modell",
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-or-v1-test",
        **extra,
    )
    db.commit()
    return provider


def _start(
    db: Session, user: User, *, herkunft: str = "panel", **argumente
) -> dict:
    """Ruft worker_start mit stillgelegtem Anlauf — kein Laufzeit-Segment."""
    with patch.object(ai_run_service, "anlauf", lambda db_, run: True):
        return ai_worker_service.worker_start(
            db, user=user, arguments={"auftrag": "Pruef die Backups", **argumente},
            herkunft=herkunft,
        )


class TestWorkerStart:
    def test_ohne_recht_laeuft_nichts(self, db: Session) -> None:
        user = _benutzer(db, "ohnerecht", mit_recht=False)
        _provider(db)

        with pytest.raises(AiActionValidationError, match="nicht erlaubt"):
            ai_worker_service.worker_start(
                db, user=user, arguments={"auftrag": "irgendwas"}
            )

    def test_ein_auftrag_wird_fenster_und_lauf(self, db: Session) -> None:
        user = _benutzer(db, "deklarant")
        anbieter = _provider(
            db, worker_model="gruendliches-modell", worker_reasoning_effort="high"
        )

        ergebnis = _start(db, user, titel="Backups", kanal="email")

        assert ergebnis["started"] is True
        fenster = db.get(AiConversation, ergebnis["worker_id"])
        assert fenster is not None and fenster.kind == "worker"
        assert fenster.title == "Backups"

        run = (
            db.query(AiRun).filter(AiRun.conversation_id == fenster.id).one()
        )
        assert run.provider_id == anbieter.id
        # Die feste Betreiber-Stufe ist im Lauf eingefroren; geklemmt wird
        # sie je Segment gegen das dann geltende Modell.
        assert run.reasoning is True
        assert run.reasoning_effort == "high"
        zustand = ai_run_service.zustand_lesen(run)
        assert zustand.get("worker") == {
            "conversation_id": fenster.id,
            "titel": "Backups",
            "kanal": "email",
        }
        # Die Rolle ist eingefroren, und gebucht wird das Arbeitsmodell des
        # Betreibers — nicht `default_model`.
        assert zustand.get("rolle") == "worker"
        from models import AiMessage

        antwortzeile = (
            db.query(AiMessage)
            .filter(
                AiMessage.conversation_id == fenster.id,
                AiMessage.role == "assistant",
            )
            .one()
        )
        assert antwortzeile.model == "gruendliches-modell"

    def test_ohne_worker_rolle_gilt_der_ein_modell_betrieb(self, db: Session) -> None:
        """Kein `worker_model` heisst: der Lauf denkt nicht, arbeitet aber."""
        user = _benutzer(db, "einmodell")
        _provider(db)

        ergebnis = _start(db, user)

        assert ergebnis["started"] is True
        run = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == ergebnis["worker_id"])
            .one()
        )
        assert run.reasoning is False
        assert run.reasoning_effort is None

    def test_der_deckel_zaehlt_auch_geparkte_laeufe(self, db: Session) -> None:
        user = _benutzer(db, "gedeckelt")
        _provider(db)

        erster = _start(db, user, titel="Erster")
        assert erster["started"] is True
        # Der erste Lauf parkt — er bleibt ein offener Auftrag.
        run = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == erster["worker_id"])
            .one()
        )
        run.status = "waiting_wake"
        db.commit()

        from services import ai_worker_limits

        with patch.object(ai_worker_limits, "max_worker_je_benutzer", lambda: 1):
            zweiter = _start(db, user, titel="Zweiter")

        assert zweiter["started"] is False
        assert zweiter["reason"] == "worker_limit"

    def test_zaehlung_und_anlegen_liegen_unter_einer_sperre(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Der Deckel muss auch dann halten, wenn zwei Aufträge zugleich starten.

        Das Gehirn ruft die Werkzeuge einer Welle nebenläufig auf, jedes in
        einer eigenen Sitzung: ohne Sperre sähen zwei `worker_start` derselben
        Runde denselben freien Platz und belegten ihn beide. Echte
        Nebenläufigkeit ist auf SQLite nicht herstellbar — dort ist
        ``FOR UPDATE`` eine leere Anweisung —, deshalb hält dieser Test die
        Invariante statt des Rennens: die Zeilensperre auf den Benutzer steht
        **vor** der Zählung, und zwischen Zählung und Lauf liegt kein Commit,
        der sie vorzeitig wieder lösen würde. Das echte Rennen bleibt eine
        PostgreSQL-Frage.
        """
        user = _benutzer(db, "gesperrt")
        _provider(db)

        ablauf: list[str] = []
        echte_sperre = Query.with_for_update
        echte_zaehlung = ai_worker_service.aktive_worker
        echter_lauf = ai_stream_service.lauf_beginnen
        echtes_commit = Session.commit

        def _sperre(self, *args, **kwargs):
            ablauf.append("sperre")
            return echte_sperre(self, *args, **kwargs)

        def _zaehlung(db_, *, user_id):
            ablauf.append("zaehlung")
            return echte_zaehlung(db_, user_id=user_id)

        def _lauf(*args, **kwargs):
            ablauf.append("lauf")
            return echter_lauf(*args, **kwargs)

        def _commit(self):
            ablauf.append("commit")
            return echtes_commit(self)

        monkeypatch.setattr(Query, "with_for_update", _sperre)
        monkeypatch.setattr(ai_worker_service, "aktive_worker", _zaehlung)
        monkeypatch.setattr(ai_stream_service, "lauf_beginnen", _lauf)
        monkeypatch.setattr(Session, "commit", _commit)

        assert _start(db, user)["started"] is True

        assert "sperre" in ablauf, "Die Zählung läuft ohne Benutzersperre"
        assert ablauf.index("sperre") < ablauf.index("zaehlung")
        dazwischen = ablauf[ablauf.index("zaehlung") : ablauf.index("lauf")]
        assert "commit" not in dazwischen, (
            "Ein Commit zwischen Zählung und Lauf gibt die Sperre frei, bevor "
            "der neue Auftrag sichtbar ist"
        )

    def test_ein_leerer_auftrag_kostet_nur_eine_runde(self, db: Session) -> None:
        user = _benutzer(db, "leerauftrag")
        _provider(db)

        with pytest.raises(AiActionValidationError, match="Auftragstext"):
            ai_worker_service.worker_start(db, user=user, arguments={"auftrag": "  "})

    def test_ohne_anbieter_kommt_eine_erklaerung_statt_eines_fehlers(
        self, db: Session
    ) -> None:
        user = _benutzer(db, "ohneanbieter")

        ergebnis = _start(db, user)

        assert ergebnis["started"] is False
        assert ergebnis["reason"] == "kein_anbieter"


class TestWorkerCancel:
    def _laufender_worker(self, db: Session, user: User) -> tuple[str, AiRun]:
        _provider(db, name=f"Zugang-{user.username}")
        ergebnis = _start(db, user)
        assert ergebnis["started"] is True
        run = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == ergebnis["worker_id"])
            .one()
        )
        return ergebnis["worker_id"], run

    def test_einfangen_beendet_alle_offenen_laeufe(self, db: Session) -> None:
        user = _benutzer(db, "einfaenger")
        worker_id, run = self._laufender_worker(db, user)
        run.status = "waiting_wake"
        run.wake_at = None
        db.commit()

        ergebnis = ai_worker_service.worker_cancel(
            db, user=user, arguments={"worker_id": worker_id}
        )

        assert ergebnis["cancelled"] is True
        db.refresh(run)
        assert run.status == "cancelled"
        assert run.stop_reason == "worker_cancel"
        assert run.wake_at is None

    def test_der_abbruch_nimmt_die_offene_karte_mit(self, db: Session) -> None:
        """„Brich den Auftrag ab" darf nicht heissen: „aber der Knopf gilt weiter".

        Seit ein Worker auf `waiting_confirmation` parkt statt zu enden,
        hinterlaesst jeder Abbruch sonst einen Vorschlag auf 'proposed' — und
        ein Klick Tage spaeter fuehrte den abgebrochenen Neustart doch noch
        aus. Gesetzt wird `expired`: die Gelegenheit ist vorbei, der Beleg
        bleibt.
        """
        from models import AiActionProposal

        user = _benutzer(db, "abbrecher")
        worker_id, run = self._laufender_worker(db, user)
        run.status = "waiting_confirmation"
        db.commit()
        karte = AiActionProposal(
            id=str(uuid4()),
            conversation_id=worker_id,
            user_id=user.id,
            server_id=None,
            tool_name="propose_server_lifecycle",
            payload_encrypted="test-enc-v1::7b7d",
            preview_json='{"operation":"restart"}',
            requires_confirmation=True,
            status="proposed",
            correlation_id=str(uuid4()),
            run_id=run.id,
            reason="Der Benutzer hat den Neustart verlangt.",
            expected_effect="Der Server startet neu.",
        )
        db.add(karte)
        db.commit()

        ergebnis = ai_worker_service.worker_cancel(
            db, user=user, arguments={"worker_id": worker_id}
        )

        assert ergebnis["cancelled"] is True
        db.refresh(karte)
        assert karte.status == "expired"
        assert karte.error_code == "worker_cancel"
        assert karte.confirmation_token_hash is None

    def test_ein_fremdes_fenster_existiert_nicht(self, db: Session) -> None:
        besitzer = _benutzer(db, "besitzer")
        fremder = _benutzer(db, "fremder")
        worker_id, _run = self._laufender_worker(db, besitzer)

        with pytest.raises(AiActionValidationError, match="nicht gefunden"):
            ai_worker_service.worker_cancel(
                db, user=fremder, arguments={"worker_id": worker_id}
            )

    def test_ein_beendeter_worker_meldet_das_ehrlich(self, db: Session) -> None:
        user = _benutzer(db, "spaetdran")
        worker_id, run = self._laufender_worker(db, user)
        run.status = "completed"
        db.commit()

        ergebnis = ai_worker_service.worker_cancel(
            db, user=user, arguments={"worker_id": worker_id}
        )

        assert ergebnis["cancelled"] is False
        assert ergebnis["reason"] == "schon_beendet"


class TestWorkerAntwort:
    def _fragender_worker(self, db: Session, user: User) -> tuple[str, AiRun]:
        _provider(db, name=f"Zugang-{user.username}")
        ergebnis = _start(db, user, titel="Kalender", kanal="email")
        assert ergebnis["started"] is True
        run = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == ergebnis["worker_id"])
            .one()
        )
        run.status = "waiting_user"
        run.stop_reason = "question"
        db.commit()
        return ergebnis["worker_id"], run

    def test_die_antwort_loest_den_wartenden_lauf_ab(self, db: Session) -> None:
        """Dieselbe Mechanik wie im Dauerchat: die Antwort ueberholt die Frage.

        Der geparkte Lauf gilt als beantwortet (`completed/answered`, wie im
        Dauerchat — er hat nicht aufgegeben, sondern gefragt) und meldet
        nichts; die Meldestelle uebergeht diesen Grund. Der Nachfolger traegt
        Rolle und Rahmen des Auftrags weiter, samt Kanal und Titel.
        """
        user = _benutzer(db, "antworter")
        worker_id, alter_lauf = self._fragender_worker(db, user)

        with patch.object(ai_run_service, "anlauf", lambda db_, run: True):
            ergebnis = ai_worker_service.worker_antwort(
                db, user=user,
                arguments={"worker_id": worker_id, "antwort": "Nimm Variante B."},
            )

        assert ergebnis["delivered"] is True
        db.refresh(alter_lauf)
        assert alter_lauf.status == "completed"
        assert alter_lauf.stop_reason == "answered"

        neuer = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == worker_id, AiRun.id != alter_lauf.id)
            .one()
        )
        zustand = ai_run_service.zustand_lesen(neuer)
        assert zustand.get("rolle") == "worker"
        assert zustand.get("worker") == {
            "conversation_id": worker_id,
            "titel": "Kalender",
            "kanal": "email",
        }

    def test_ein_beendeter_worker_bekommt_keine_antwort_mehr(
        self, db: Session
    ) -> None:
        user = _benutzer(db, "zuspaet")
        worker_id, run = self._fragender_worker(db, user)
        run.status = "completed"
        db.commit()

        ergebnis = ai_worker_service.worker_antwort(
            db, user=user,
            arguments={"worker_id": worker_id, "antwort": "egal"},
        )

        assert ergebnis["delivered"] is False
        assert ergebnis["reason"] == "schon_beendet"

    def test_ein_fremdes_fenster_existiert_nicht(self, db: Session) -> None:
        besitzer = _benutzer(db, "fragebesitzer")
        fremder = _benutzer(db, "fragefremder")
        worker_id, _run = self._fragender_worker(db, besitzer)

        with pytest.raises(AiActionValidationError, match="nicht gefunden"):
            ai_worker_service.worker_antwort(
                db, user=fremder,
                arguments={"worker_id": worker_id, "antwort": "egal"},
            )


class TestHerkunft:
    """Ein Auftrag erbt die Welt, aus der er kam.

    Der Ausfall vom 22.08.2026: der Betreiber fragte in der Smart-System-App
    „wie voll ist meine C-Festplatte", und der Auftrag antwortete, er koenne
    auf den Rechner nicht zugreifen. `worker_start` legte den Lauf ohne
    Herkunft an, der Standardwert "panel" griff, `herkunft_schnitt` nahm ihm
    alle Desktop-Werkzeuge und `ai_prompt.build` den DESKTOP-Block dazu.

    Der Ausfall war damals vollstaendig: das Gehirn hatte selbst keine
    Desktop-Werkzeuge, also war im Gehirn/Worker-Betrieb **niemand** mehr da,
    der den Rechner des Benutzers sehen konnte. Seit dem 23.08.2026 koennte
    das Gehirn wenigstens nachsehen (`GEHIRN_DESKTOP`) — die Vererbung der
    Herkunft bleibt trotzdem tragend, denn ein Auftrag, der Dateien anfassen
    oder aufraeumen soll, kann das nur mit ihr.
    """

    def _zustand(self, db: Session, worker_id: str) -> dict:
        run = (
            db.query(AiRun).filter(AiRun.conversation_id == worker_id)
            .order_by(AiRun.created_at.desc(), AiRun.id.desc()).first()
        )
        assert run is not None
        return ai_run_service.zustand_lesen(run)

    def test_ein_auftrag_aus_der_app_bleibt_am_rechner(self, db: Session) -> None:
        user = _benutzer(db, "vomrechner")
        _provider(db)

        ergebnis = _start(db, user, herkunft="desktop")

        assert self._zustand(db, ergebnis["worker_id"]).get("herkunft") == "desktop"

    def test_ein_auftrag_aus_dem_panel_bekommt_keinen_rechner(
        self, db: Session
    ) -> None:
        """Die Gegenrichtung, und sie ist die Sicherheitsseite: der Standard
        ist die engere Welt, nicht die weitere."""
        user = _benutzer(db, "vompanel")
        _provider(db)

        ergebnis = _start(db, user)

        assert self._zustand(db, ergebnis["worker_id"]).get("herkunft") == "panel"

    def test_die_antwort_laesst_den_auftrag_in_seiner_welt(
        self, db: Session
    ) -> None:
        """Die Herkunft gehoert dem Fenster, nicht dem einzelnen Lauf.

        Sonst verloere ein Auftrag aus der App mitten im Vorgang seine
        Werkzeuge, nur weil der Mensch die Rueckfrage im Panel beantwortet
        hat.
        """
        user = _benutzer(db, "antwortwelt")
        _provider(db, name="Zugang-antwortwelt")
        ergebnis = _start(db, user, herkunft="desktop")
        alter_lauf = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == ergebnis["worker_id"]).one()
        )
        alter_lauf.status = "waiting_user"
        db.commit()

        with patch.object(ai_run_service, "anlauf", lambda db_, run: True):
            antwort = ai_worker_service.worker_antwort(
                db, user=user,
                arguments={
                    "worker_id": ergebnis["worker_id"], "antwort": "Ja, mach.",
                },
            )

        assert antwort["delivered"] is True
        assert self._zustand(db, ergebnis["worker_id"]).get("herkunft") == "desktop"

    def test_der_dispatch_reicht_die_herkunft_durch(self, db: Session) -> None:
        """Der Weg vom Lauf bis zum Handler — die Stelle, an der es fehlte.

        Geprueft wird die Verdrahtung, nicht `worker_start`: dass
        `execute_read_tool` die Herkunft ueberhaupt bis dorthin traegt. Sie
        kommt aus dem Laufzustand und darf **nie** aus den Argumenten
        stammen; ein Modell koennte sich sonst selbst einen Rechner
        zuschreiben.
        """
        from services.ai_action_service import execute_read_tool

        user = _benutzer(db, "durchgereicht")
        _provider(db)

        with patch.object(ai_run_service, "anlauf", lambda db_, run: True):
            ergebnis = execute_read_tool(
                db, user=user, tool_name="worker_start",
                arguments={"auftrag": "Sieh auf dem Rechner nach"},
                herkunft="desktop",
            )

        assert ergebnis["started"] is True
        assert self._zustand(db, ergebnis["worker_id"]).get("herkunft") == "desktop"


def test_der_dispatch_kennt_die_delegationen(db: Session) -> None:
    """`wait_until` hat einen erklaerenden Zweig statt des Durchfall-raise.

    Es wird im Rundenlauf abgefangen (Park auf `waiting_wake`) und darf im
    Werkzeug-Dispatch nie ausgefuehrt werden — aber ein Aufruf ausserhalb
    eines parkfaehigen Laufs soll eine Erklaerung bekommen, keinen
    „Kein Handler"-Fehler, der nach einem Verdrahtungsfehler aussieht.
    """
    from services.ai_action_service import _execute_global_read_tool

    user = _benutzer(db, "dispatch")

    with pytest.raises(AiActionValidationError, match="Rundenlauf"):
        _execute_global_read_tool(
            db, user=user, tool_name="wait_until", arguments={"minuten": 5}
        )
