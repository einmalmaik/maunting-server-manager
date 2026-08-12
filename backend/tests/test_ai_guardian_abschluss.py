"""Ein Heilungslauf endet — und sagt Bescheid. Auch der, den jemand ueberholt hat.

Diese Datei prueft das Ende eines Guardian-Laufs, und zwar die drei Stellen, an
denen es frueher **still** war. Still ist hier das eigentliche Problem: bei einer
Heilung sitzt niemand am Panel. Was der Lauf nicht meldet, erfaehrt niemand — und
der Server steht weiter.

*Der abgeloeste Lauf.* Der haeufigste Weg in den Waechter fuer den bereits
beendeten Lauf ist ausgerechnet der Normalfall: der Freigeber tippt waehrend
einer laufenden Heilung etwas in den Chat, `vorgaenger_abloesen` setzt den Lauf
direkt in der Datenbank auf 'cancelled/superseded', und das Segment findet beim
Abschliessen einen bereits beendeten Lauf vor. Der Waechter sprang dabei an
`_guardian_nachbereiten` vorbei. Folge: keine Ergebnis-Mail, obwohl
`ai_guardian_report` bei **jedem** Endzustand zusagt — und weil die Notiz mit
`mode='healing'` laengst committet war, uebersprang der Ausloeser den Vorfall
von da an bei jedem Takt.

*Der Rahmen, der verlorengeht.* `guardian_aus_zustand` lieferte bei einem
vorhandenen, aber unlesbaren Rahmen `None` — und `None` heisst in diesem Code
"ein Mensch hat getippt". Aus einer Heilung wurde damit ein gewoehnlicher
Chatlauf: voller Werkzeugsatz, keine Serverbindung, keine Backup-Pflicht, und
niemand, der mitliest. Der Verlust des Rahmens ist die gefaehrliche Richtung,
nicht die sichere; deshalb wirft er hier.

*Die Vorschlaege, auf die niemand klickt.* Kippt das benutzerweite
Stundenkontingent mitten im Lauf, faellt `autonomy_allows` auf
Bestaetigungspflicht zurueck. Der Lauf parkte dann auf 'waiting_confirmation' —
kein Endzustand, also kein Bericht, und weil `aktiver_lauf` wartende Laeufe
mitzaehlt, blockierte er jede weitere Heilung dieses Freigebers auf allen seinen
Servern. Stattdessen werden die offenen Vorschlaege zurueckgenommen und der Lauf
beendet.

Gemessen wird durchgehend am Versand selbst (`bericht_versenden` als Attrappe)
und nicht an einem Nebeneffekt: die Zusage lautet "der Betreiber erfaehrt es",
und nur der Aufruf belegt sie.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiActionProposal, AiConversation, AiRun, Incident, Server, User
from services import ai_guardian_report, ai_run_service, ai_stream_service
from services.ai_stream_service import GuardianRahmenUnlesbar, guardian_aus_zustand


# ── Aufbau ────────────────────────────────────────────────────────────────


def _server(db: Session, name: str = "Heilung") -> Server:
    server = Server(
        name=name,
        game_type="dayz",
        install_dir=f"/tmp/{name}",
        container_name=f"msm-{name}-{uuid4().hex[:8]}",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _conversation(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Heilung"
    )
    db.add(conversation)
    db.commit()
    return conversation


def _vorfall(db: Session, server: Server) -> Incident:
    """Ein echter Vorfall und keine erfundene Nummer.

    Der Rahmen im Laufzustand nennt `incident_id`, der Bericht schlaegt sie nach.
    Eine Zuordnung ins Leere waere eine Zusicherung ueber einen Weg, den es so
    nicht gibt.
    """
    vorfall = Incident(
        server_id=server.id,
        title="Autopilot: process_not_running",
        description="GameThread haengt",
        type="process_not_running",
        status="open",
        fingerprint=f"guardian:{server.id}:process_not_running",
        occurrences=3,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db.add(vorfall)
    db.commit()
    db.refresh(vorfall)
    return vorfall


def _rahmen(server: Server, vorfall: Incident) -> dict:
    """Der Guardian-Rahmen, wie `heilungslauf_starten` ihn ablegt.

    Mit beiden Zeitangaben, weil der Betrieb beide schreibt: `backup_anker` ist
    der Beginn des Laufs und der ehrliche Nachweiszeitpunkt, `incident_created_at`
    bleibt fuer Laeufe aus der Zeit davor.
    """
    return {
        "server_id": server.id,
        "incident_id": vorfall.id,
        "incident_created_at": vorfall.created_at.isoformat(),
        "backup_anker": datetime.now(timezone.utc).isoformat(),
    }


def _lauf(
    db: Session,
    user: User,
    conversation: AiConversation,
    *,
    guardian: dict | None,
    status: str = "running",
    stop_reason: str | None = None,
) -> AiRun:
    """Ein Lauf mit vollstaendigem Arbeitsgedaechtnis.

    Der Zustand kommt aus `leerer_zustand` und nicht aus einem selbstgebauten
    Woerterbuch: `_guardian_nachbereiten` liest daraus `guardian_briefed` und
    `guardian_berichtet`, und ein Test, der nur die Schluessel setzt, die er
    gerade braucht, prueft eine Form, die im Betrieb nie vorkommt.
    """
    zustand = ai_run_service.leerer_zustand([], request_id=str(uuid4()))
    zustand["guardian"] = guardian
    run = AiRun(
        id=str(uuid4()),
        conversation_id=conversation.id,
        user_id=user.id,
        status=status,
        stop_reason=stop_reason,
    )
    ai_run_service.zustand_schreiben(run, zustand)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _zustand_aus_db(db: Session, run_id: str) -> dict:
    db.expire_all()
    zeile = db.get(AiRun, run_id)
    assert zeile is not None
    return json.loads(zeile.state_json or "{}")


@pytest.fixture
def versand() -> Mock:
    """Der Mailversand als Attrappe.

    `_guardian_nachbereiten` importiert `ai_guardian_report` verzoegert und ruft
    das Modulattribut — deshalb greift ein Patch am Modul und nicht an einem
    Namen im Stream-Modul.
    """
    with patch.object(ai_guardian_report, "bericht_versenden", Mock()) as attrappe:
        yield attrappe


# ── Der Lauf endet und berichtet ──────────────────────────────────────────


class TestAbschlussBerichtet:
    def test_ein_abgeloester_heilungslauf_berichtet_trotzdem(
        self, db: Session, regular_user: User, versand: Mock
    ) -> None:
        """Der schwerste Befund dieser Kopplung: der Waechter verschluckte die Mail.

        Der Ablauf aus dem Betrieb: die Heilung laeuft, der Freigeber schreibt
        etwas in den Chat, `vorgaenger_abloesen` setzt den Lauf auf
        'cancelled/superseded'. Sein Segment kommt danach an den Abschluss und
        findet einen bereits beendeten Lauf vor.

        Genau dieser Zweig sprang frueher an `_guardian_nachbereiten` vorbei. Der
        Betreiber erfuhr nie, dass die KI an seinem stehenden Server gearbeitet
        und mittendrin aufgehoert hat — und der Ausloeser uebersprang den Vorfall
        von da an, weil die Notiz mit `mode='healing'` beim Start committet wird.
        """
        server = _server(db)
        conversation = _conversation(db, regular_user)
        vorfall = _vorfall(db, server)
        run = _lauf(
            db, regular_user, conversation,
            guardian=_rahmen(server, vorfall),
            status="cancelled", stop_reason="superseded",
        )

        # Das Segment meldet seinen eigenen, laengst ueberholten Abschluss.
        ai_stream_service._lauf_abschliessen(
            run.id, status="completed", stop_reason="done"
        )

        assert versand.call_count == 1, "Der abgeloeste Heilungslauf blieb stumm"

        db.expire_all()
        ueberholt = db.get(AiRun, run.id)
        # Die zweite Zusage des Waechters gilt unveraendert: der tatsaechliche
        # Zustand wird gemeldet, nicht der gewuenschte. Ohne diese Zeile koennte
        # der Bericht auch dadurch entstanden sein, dass der Waechter gar nicht
        # mehr greift.
        assert ueberholt.status == "cancelled"
        assert ueberholt.stop_reason == "superseded"

    def test_zwei_abschluesse_ergeben_genau_eine_mail(
        self, db: Session, regular_user: User, versand: Mock
    ) -> None:
        """Einmal regulaer, einmal ueber den Waechter — und trotzdem eine Mail.

        Beide Wege koennen denselben Lauf treffen, sobald ein Mensch mitten in
        eine Heilung hineinschreibt. Zwei Mails zu demselben Vorgang waeren
        schlimmer als eine ausgebliebene Wiederholung: der Betreiber liest
        zweimal dasselbe und weiss nicht, ob zweimal eingegriffen wurde.

        Die Marke wird **vor** dem Versand gesetzt und committet. Deshalb wird
        sie hier auch in der Datenbank nachgesehen und nicht nur die Aufrufzahl
        gezaehlt: eine Marke, die nur im Speicher steht, ueberlebt den naechsten
        Prozessstart nicht — und der Waechter laeuft auch nach einem Neustart.
        """
        server = _server(db)
        conversation = _conversation(db, regular_user)
        vorfall = _vorfall(db, server)
        run = _lauf(db, regular_user, conversation, guardian=_rahmen(server, vorfall))

        # Der regulaere Abschluss.
        ai_stream_service._lauf_abschliessen(
            run.id, status="completed", stop_reason="done"
        )
        # Und derselbe Lauf noch einmal — jetzt ueber den Waechter, weil er
        # bereits in einem Endzustand steht.
        ai_stream_service._lauf_abschliessen(
            run.id, status="failed", stop_reason="AI_PROVIDER_ERROR"
        )

        assert versand.call_count == 1, "Der Betreiber bekam denselben Vorgang zweimal"
        assert _zustand_aus_db(db, run.id)["guardian_berichtet"] is True

    def test_ein_lauf_ohne_guardian_rahmen_loest_keinen_bericht_aus(
        self, db: Session, regular_user: User, versand: Mock
    ) -> None:
        """Die Gegenprobe — sonst bekaeme der Benutzer nach jedem Chat eine Mail.

        Beide Wege in einem Test, weil beide seit der Behebung nachbereiten: der
        regulaere Abschluss und der Waechter fuer den bereits beendeten Lauf.
        Der Waechter ist dabei der interessantere Fall, denn er trifft jeden
        abgeloesten Chatlauf — und davon gibt es viele, weil jede nachgeschobene
        Nachricht einen erzeugt.
        """
        conversation = _conversation(db, regular_user)
        gewoehnlich = _lauf(db, regular_user, conversation, guardian=None)
        abgeloest = _lauf(
            db, regular_user, conversation,
            guardian=None, status="cancelled", stop_reason="superseded",
        )

        ai_stream_service._lauf_abschliessen(
            gewoehnlich.id, status="completed", stop_reason="done"
        )
        ai_stream_service._lauf_abschliessen(
            abgeloest.id, status="completed", stop_reason="done"
        )

        assert versand.call_count == 0


# ── Der Rahmen im Laufzustand ─────────────────────────────────────────────


class TestGuardianRahmen:
    def test_kein_schluessel_heisst_gewoehnlicher_chatlauf(self) -> None:
        """`None` ist die Aussage "ein Mensch hat getippt" — und nur die.

        Sie muss weiterhin moeglich sein, sonst waere jeder Chatlauf eine
        Heilung ohne Rahmen und wuerde sofort abgebrochen.
        """
        assert guardian_aus_zustand({}) is None
        assert guardian_aus_zustand({"guardian": None}) is None

    @pytest.mark.parametrize(
        "rahmen",
        [
            pytest.param({"incident_id": 7, "incident_created_at": "2026-08-12T03:00:00+00:00"},
                         id="ohne-server-id"),
            pytest.param({"server_id": 3, "incident_created_at": "2026-08-12T03:00:00+00:00"},
                         id="ohne-incident-id"),
            pytest.param({"server_id": 3, "incident_id": 7}, id="ohne-zeitangabe"),
            pytest.param({"server_id": 3, "incident_id": 7, "incident_created_at": "gestern nacht"},
                         id="kaputtes-datum"),
            pytest.param({"server_id": 3, "incident_id": 7, "backup_anker": "irgendwann",
                          "incident_created_at": "2026-08-12T03:00:00+00:00"},
                         id="kaputter-anker"),
            pytest.param({"server_id": None, "incident_id": 7,
                          "incident_created_at": "2026-08-12T03:00:00+00:00"},
                         id="server-id-ist-none"),
            pytest.param({"server_id": "der erste", "incident_id": 7,
                          "incident_created_at": "2026-08-12T03:00:00+00:00"},
                         id="server-id-ist-text"),
            pytest.param([3, 7], id="rahmen-ist-liste"),
            pytest.param("server 3, vorfall 7", id="rahmen-ist-zeichenkette"),
            pytest.param(3, id="rahmen-ist-zahl"),
        ],
    )
    def test_ein_unlesbarer_rahmen_wirft_statt_zu_lockern(self, rahmen) -> None:
        """Vorhanden, aber unlesbar ist etwas anderes als nicht vorhanden.

        Hier stand zuerst `None` fuer beides, mit der Begruendung, ohne Rahmen
        greife eben keine Verschaerfung. Das war falsch herum gedacht: in einer
        Heilung ist die Werkzeugmenge **enger** als im Chat, der Server ist fest,
        und vor jedem Eingriff steht ein Backup-Nachweis. Faellt der Rahmen weg,
        faellt all das weg — in einem Lauf, in dem niemand mitliest, im Namen des
        Freigebers und mit dessen Rechten.

        Die Formen stammen aus dem, was `state_json` tatsaechlich enthalten kann:
        ein halb geschriebener Rahmen, ein Datum, das kein Datum ist, oder ein
        Wert, den eine aeltere Fassung des Codes anders abgelegt hat.
        """
        with pytest.raises(GuardianRahmenUnlesbar):
            guardian_aus_zustand({"guardian": rahmen})

    def test_der_backup_anker_schlaegt_das_vorfallsdatum(self) -> None:
        """Der Nachweis haengt am Beginn der Heilung, nicht am ersten Auftreten.

        `Incident.created_at` wird bei der Gruppierung nie aufgefrischt und
        stammt ungeprueft vom Agenten. Ein Vorfall, der seit Tagen offen steht,
        haette damit ein tagealtes Nachtbackup als Nachweis durchgehen lassen:
        die Schranke waere formal erfuellt, und ein Rollback landete auf einem
        Stand von vorgestern.

        Der Laufbeginn ist der ehrliche Anker — ein Backup, das juenger ist als
        er, kann nur waehrend dieser Heilung entstanden sein.
        """
        anker = datetime(2026, 8, 12, 3, 15, tzinfo=timezone.utc)
        erstes_auftreten = datetime(2026, 8, 9, 22, 40, tzinfo=timezone.utc)

        kontext = guardian_aus_zustand({
            "guardian": {
                "server_id": 3,
                "incident_id": 7,
                "incident_created_at": erstes_auftreten.isoformat(),
                "backup_anker": anker.isoformat(),
            }
        })

        assert kontext is not None
        assert kontext.server_id == 3
        assert kontext.incident_id == 7
        assert kontext.incident_created_at == anker

    def test_ohne_anker_gilt_weiterhin_das_vorfallsdatum(self) -> None:
        """Der Rueckfall fuer Laeufe, die vor dieser Aenderung angelegt wurden.

        Ein Lauf ueberlebt Prozessstarts und wartet notfalls Stunden auf eine
        Bestaetigung. Zum Zeitpunkt der Umstellung liegen also Zustaende in der
        Datenbank, die den Anker nicht kennen. Ohne den Rueckfall waeren sie ab
        dem naechsten Segment unlesbar — und wuerden nach der Regel oben sofort
        abgebrochen.
        """
        erstes_auftreten = datetime(2026, 8, 9, 22, 40, tzinfo=timezone.utc)

        kontext = guardian_aus_zustand({
            "guardian": {
                "server_id": 3,
                "incident_id": 7,
                "incident_created_at": erstes_auftreten.isoformat(),
            }
        })

        assert kontext is not None
        assert kontext.incident_created_at == erstes_auftreten


# ── Vorschlaege, auf die niemand mehr klickt ──────────────────────────────


def _vorschlag(
    db: Session,
    user: User,
    conversation: AiConversation,
    *,
    status: str,
) -> AiActionProposal:
    """Ein Vorschlag mit Bestaetigungsmarke, wie ihn eine Schreibrunde hinterlaesst."""
    zeile = AiActionProposal(
        id=str(uuid4()),
        conversation_id=conversation.id,
        user_id=user.id,
        server_id=None,
        tool_name="propose_server_restart",
        payload_encrypted="test-enc-v1::7b7d",
        preview_json="{}",
        requires_confirmation=True,
        autonomous=False,
        status=status,
        confirmation_token_hash="c" * 64,
        correlation_id=str(uuid4()),
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return zeile


class TestZurueckgenommeneVorschlaege:
    def test_offene_vorschlaege_laufen_ab_und_verlieren_ihre_marke(
        self, db: Session, regular_user: User
    ) -> None:
        """Eine Karte, auf die niemand mehr klicken soll, darf nicht offen bleiben.

        Bliebe der Vorschlag auf 'proposed' stehen, waere er eine Bitte an den
        naechsten Menschen, Stunden spaeter einen Eingriff freizugeben, dessen
        Anlass er nicht mitbekommen hat — und dessen Backup-Nachweis inzwischen
        von der Aufbewahrungsregel abgeraeumt sein kann.

        'confirmed' zaehlt mit: bestaetigt heisst hier nur "die Marke ist
        eingeloest", ausgefuehrt ist damit nichts. Ein solcher Vorschlag wartet
        auf denselben Lauf, den es nicht mehr gibt.

        Die Marke wird geloescht und nicht bloss der Status gesetzt. Sie ist das
        Einzige, was einen Eingriff noch ausloesen koennte; ein abgelaufener
        Vorschlag mit gueltiger Marke waere ein Schluessel zu einer Tuer, die
        niemand mehr bewacht.
        """
        conversation = _conversation(db, regular_user)
        offen = _vorschlag(db, regular_user, conversation, status="proposed")
        bestaetigt = _vorschlag(db, regular_user, conversation, status="confirmed")

        ai_stream_service._vorschlaege_zuruecknehmen(
            [offen.id, bestaetigt.id], grund="guardian_unattended"
        )

        db.expire_all()
        for zeile_id in (offen.id, bestaetigt.id):
            zeile = db.get(AiActionProposal, zeile_id)
            assert zeile.status == "expired"
            assert zeile.error_code == "guardian_unattended"
            assert zeile.confirmation_token_hash is None

    def test_ausgefuehrte_und_gescheiterte_bleiben_unberuehrt(
        self, db: Session, regular_user: User
    ) -> None:
        """Was schon geschehen ist, wird nicht nachtraeglich zu "abgelaufen".

        'succeeded' und 'failed' sind Protokoll: sie sagen, was auf dem Server
        passiert ist. Wuerde das Aufraeumen sie mitnehmen, stuende im Chatverlauf
        und im Bericht, es sei nichts geschehen — waehrend die Konfiguration
        geaendert und der Container neu gestartet ist. Das waere die
        gefaehrlichste Sorte falscher Auskunft, weil sie beruhigt.
        """
        conversation = _conversation(db, regular_user)
        gelaufen = _vorschlag(db, regular_user, conversation, status="succeeded")
        gescheitert = _vorschlag(db, regular_user, conversation, status="failed")

        ai_stream_service._vorschlaege_zuruecknehmen(
            [gelaufen.id, gescheitert.id], grund="guardian_unattended"
        )

        db.expire_all()
        assert db.get(AiActionProposal, gelaufen.id).status == "succeeded"
        assert db.get(AiActionProposal, gelaufen.id).error_code is None
        assert db.get(AiActionProposal, gescheitert.id).status == "failed"
        assert db.get(AiActionProposal, gescheitert.id).error_code is None

    def test_nur_die_genannten_vorschlaege_werden_angefasst(
        self, db: Session, regular_user: User
    ) -> None:
        """Zurueckgenommen wird die eigene Runde, nicht der Posteingang.

        Ein Benutzer kann gleichzeitig einen Chatlauf offen haben, dessen Karte
        auf seinen Klick wartet. Faende die Abfrage ueber den Status statt ueber
        die Kennungen, raeumte eine abbrechende Heilung ihm diese Karte weg — und
        er saehe nur noch "abgelaufen", ohne je gefragt worden zu sein.
        """
        conversation = _conversation(db, regular_user)
        aus_der_heilung = _vorschlag(db, regular_user, conversation, status="proposed")
        aus_dem_chat = _vorschlag(db, regular_user, conversation, status="proposed")

        ai_stream_service._vorschlaege_zuruecknehmen(
            [aus_der_heilung.id], grund="guardian_unattended"
        )

        db.expire_all()
        assert db.get(AiActionProposal, aus_der_heilung.id).status == "expired"
        fremd = db.get(AiActionProposal, aus_dem_chat.id)
        assert fremd.status == "proposed"
        assert fremd.confirmation_token_hash is not None

    def test_eine_leere_liste_ist_kein_fehler(self, db: Session, regular_user: User) -> None:
        """Der haeufigste Fall ueberhaupt: eine Runde ohne offene Vorschlaege.

        Die Funktion wird im Abbruchweg gerufen, also dort, wo ohnehin schon
        etwas schiefgelaufen ist. Eine Ausnahme an dieser Stelle nimmt dem Lauf
        sein Ende — und damit dem Betreiber seinen Bericht.
        """
        ai_stream_service._vorschlaege_zuruecknehmen([], grund="guardian_unattended")
        ai_stream_service._vorschlaege_zuruecknehmen(
            [str(uuid4())], grund="guardian_unattended"
        )
