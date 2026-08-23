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

    def test_der_meldekanal_wird_gegen_die_liste_der_meldestelle_geprueft(
        self,
    ) -> None:
        """Geprueft wird gegen die Liste, die der Konsument wirklich benutzt.

        `models/ai_meldung.KANAELE` ist ausdruecklich eine eigene Kopie der
        Aufgaben-Kanaele, „damit eine dortige Erweiterung nicht stillschweigend
        hier gilt". Der Worker-Kanal wandert in den Rahmen und wird am Ende von
        `ai_meldestelle.melden` verbraucht — nicht von den stehenden
        Auftraegen.

        Prueft `worker_start` gegen `ai_task.KANAELE`, dann kommt ein dort
        ergaenzter Kanal (etwa 'webhook') durch, das Gehirn sagt dem Benutzer
        die Zustellung darueber zu, und `melden` faellt bei unbekanntem Kanal
        still auf 'chat' zurueck: der Benutzer wartet auf eine Meldung, die nie
        auf dem versprochenen Weg kommt.

        Beide Tupel sind heute gleich. Deshalb prueft dieser Test die Herkunft
        und nicht den Inhalt — an der Gleichheit haengt sonst nichts.
        """
        from models.ai_meldung import KANAELE as MELDESTELLE

        assert ai_worker_service.KANAELE is MELDESTELLE

    def test_der_katalog_bietet_dieselben_kanaele_an_die_der_dienst_annimmt(
        self,
    ) -> None:
        """Die andere Haelfte derselben Zusage: was angeboten wird, muss gelten.

        Der Dienst gegen die richtige Liste zu pruefen genuegt nicht, solange
        das `worker_start`-Schema dem Modell die andere anbietet — dann waere
        der neue Kanal aus `ai_task.KANAELE` im Katalog sichtbar und am
        Handler verboten, und das Modell liefe hinein, weil der Katalog es
        eingeladen hat.

        Auch hier die Herkunft statt des Inhalts: gleich sind beide heute
        ohnehin.
        """
        from models.ai_meldung import KANAELE as MELDESTELLE
        from services import ai_action_service

        schema = next(
            eintrag["function"]
            for eintrag in ai_action_service._worker_tool_definitions()
            if eintrag["function"]["name"] == "worker_start"
        )
        angeboten = schema["parameters"]["properties"]["kanal"]["enum"]
        assert angeboten == list(MELDESTELLE)

    def test_ein_unbekannter_kanal_wird_abgelehnt(self, db: Session) -> None:
        """Kein stiller Rueckfall auf 'chat' im Handler.

        Ein Formfehler kostet eine Runde, nie die Antwort: die Meldung nennt
        die zulaessigen Werte, das Modell ruft noch einmal richtig auf.
        """
        user = _benutzer(db, "falscherkanal")
        _provider(db)

        with pytest.raises(AiActionValidationError, match="Meldekanal"):
            _start(db, user, kanal="webhook")

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


class TestHerkunftUndGeraet:
    """Ein Auftrag erbt die Welt, aus der er kam — und das Geraet dazu.

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

    Die Herkunft allein sagt nur "aus der App". **Welche** App, sagt die
    Refresh-Familie, und an ihr haengt, welcher von mehreren gekoppelten
    Rechnern einen Desktop-Auftrag abholen darf
    (`desktop_job_service.naechster`). Sie muss deshalb denselben Weg gehen:
    geht sie zwischen zwei Laeufen desselben Fensters verloren, ist der
    Auftrag wieder fuer jedes Geraet abholbar.
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

    def test_die_antwort_laesst_dem_auftrag_sein_geraet(self, db: Session) -> None:
        """Dieselbe Uebergabe wie bei der Herkunft, eine Zeile daneben.

        Der Zustand des Vorgaengers ist die einzige Quelle: die Anfrage, die
        das Fenster geoeffnet hat, gibt es zu diesem Zeitpunkt nicht mehr.
        Faellt die Kennung hier weg, verliert der naechste Desktop-Auftrag des
        Auftrags seine Bindung — und der Blick auf den Bildschirm geht wieder
        an den Rechner, der zuerst fragt.
        """
        user = _benutzer(db, "antwortgeraet")
        _provider(db, name="Zugang-antwortgeraet")
        ergebnis = _start(db, user, herkunft="desktop")
        alter_lauf = (
            db.query(AiRun)
            .filter(AiRun.conversation_id == ergebnis["worker_id"]).one()
        )
        zustand = ai_run_service.zustand_lesen(alter_lauf)
        zustand["familie"] = "fam-laptop"
        ai_run_service.zustand_schreiben(alter_lauf, zustand)
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
        assert self._zustand(db, ergebnis["worker_id"]).get("familie") == "fam-laptop"

    @pytest.mark.asyncio
    async def test_der_lauf_reicht_sein_geraet_bis_in_den_auftrag(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Vom Laufzustand bis in den neuen Lauf — **ohne** ein Argument von Hand.

        Alle Tests darüber übergeben die Kennung selbst: an `worker_start`,
        an `execute_read_tool` oder direkt in den Zustand des Vorgaengers. Sie
        können deshalb prinzipiell nicht sehen, woran die Bindung bis zum
        23.08.2026 scheiterte — die Vererbung in `worker_antwort` war da, die
        Spalte war da, der Filter war da, und trotzdem trug **jeder**
        Worker-Lauf `familie=None`. `worker_start` nahm die Kennung gar nicht
        erst entgegen, und niemand auf dem Weg dorthin holte sie aus dem
        Zustand. Weil `worker_antwort` nur weitergibt, was schon da ist,
        vererbte sich dauerhaft nichts.

        Hier geht deshalb ein **Zustand** hinein — der eines Gehirn-Laufs aus
        der App — und der Auftrag entsteht auf dem Weg, den es in der
        Anwendung gibt: Segment, Werkzeugrunde, Dispatch, Handler. Von Hand
        gesetzt wird nur, was `routers/ai_chat` beim Anlegen auch setzt.
        """
        from services import ai_run_broker
        from services.openai_compatible_adapter import (
            ProviderToolCall,
            StreamChunk,
            StreamUsage,
        )

        user = _benutzer(db, "geraetevererbung")
        # Mit Arbeitsmodell wird der Dauerchat zum Gehirn (`_rolle_ableiten`),
        # und nur ein Gehirn darf `worker_start` überhaupt rufen — ein
        # "voll"-Lauf sortiert es als Hintergrund-Werkzeug aus.
        anbieter = _provider(
            db, name="Zugang-geraetevererbung", worker_model="arbeitsmodell"
        )
        chat = AiConversation(
            id=str(uuid4()), user_id=user.id, kind="primary", title="Chat"
        )
        db.add(chat)
        db.commit()

        # Genau der Aufruf, den der Chat-Endpunkt aus der App macht: Herkunft
        # und Familie kommen aus der Sitzung, nicht aus dem Gespräch.
        lauf, fehler = ai_stream_service.lauf_beginnen(
            db, user=user, conversation=chat, provider=anbieter,
            request_id=uuid4(), content="Räum bitte meinen Download-Ordner auf.",
            reasoning=False, herkunft="desktop", familie="fam-laptop",
        )
        assert lauf is not None, f"Lauf konnte nicht beginnen: {fehler}"
        assert ai_run_service.zustand_lesen(lauf).get("rolle") == "gehirn"

        # Genau eine Werkzeugrunde: der Deckel für gleichzeitige Aufträge
        # liegt bei drei, und ein Modell, das in jeder Runde erneut startet,
        # liefe in ihn hinein statt in die Frage dieses Tests.
        runde = {"nr": 0}

        async def _fake(_client, *, usage: StreamUsage, tool_choice=None, **kwargs):
            if tool_choice != "none" and runde["nr"] == 0:
                usage.tool_calls = [ProviderToolCall(
                    id="ws1", name="worker_start",
                    arguments={
                        "auftrag": "Räum den Download-Ordner auf",
                        "titel": "Aufräumen",
                    },
                )]
            runde["nr"] += 1
            usage.total_tokens = 10
            yield StreamChunk("content", "Ich gebe das als Auftrag weiter.")

        monkeypatch.setattr(ai_stream_service, "stream_chat_completion", _fake)
        ai_run_broker.zuruecksetzen_fuer_tests()
        ai_run_broker.eroeffnen(lauf.id)

        # Der neue Auftrag soll entstehen, aber nicht selbst losfahren.
        with patch.object(ai_run_service, "anlauf", lambda db_, run: True):
            await ai_stream_service.segment_ausfuehren(lauf.id, client=object())

        db.expire_all()
        auftrag = (
            db.query(AiConversation)
            .filter(
                AiConversation.user_id == user.id,
                AiConversation.kind == "worker",
            )
            .one()
        )
        zustand = self._zustand(db, auftrag.id)
        # Beide zusammen adressieren erst einen Rechner: die Herkunft öffnet
        # dem Auftrag die Desktop-Werkzeuge, die Familie sagt ihm, an welches
        # der gekoppelten Geräte er sich damit wendet.
        assert zustand.get("herkunft") == "desktop"
        assert zustand.get("familie") == "fam-laptop"

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
