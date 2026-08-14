"""Was der Bericht behaupten darf — und was nie hinausgeht.

Zwei Zusagen, die aeusserlich nichts miteinander zu tun haben und doch dieselbe
Wurzel: der Heilungslauf ist die einzige Stelle im Panel, an der niemand
mitliest. Was hier falsch ist, faellt keinem auf.

**Der Backupname in der Mail.** Der Betreiber liest den Satz "ein Backup liegt
vor" und prueft ihn nicht nach — es ist drei Uhr nachts, und genau dafuer hat er
die Autonomie eingeschaltet. Nannte die Mail irgendein junges Archiv des
Servers, nannte sie auf jeder Anlage mit stuendlichem Automatikbackup
regelmaessig ein **fremdes**: eines, das nach dem Eingriff der KI entstanden ist
und die Aenderung bereits enthaelt. Wer daraufhin zurueckrollt, macht sie nicht
rueckgaengig, sondern zementiert sie. Der Name darf deshalb nur aus einer Spur
kommen, die diesen Lauf bezeichnet, und aus keinem Zeitfenster.

**Die Schwaerzung strukturierter Werte.** Die Muster in `ai_redaction` sind auf
Zuweisungstext ausgelegt und brauchen Schluessel *und* Trennzeichen in derselben
Zeichenkette. Ein Werkzeugergebnis liefert aber ein Woerterbuch, und die
Rekursion zerlegt beides: weitergereicht wurde nur noch ``"hunter2"``, und
darauf passt kein Muster. `read_blueprint` schickte damit
``{"runtime": {"env": {"RCON_PASSWORD": "hunter2"}}}`` im Klartext an den
Modellanbieter und in `ai_tool_results`. Der als "einziger Ausgang" gebaute
Punkt hielt seine Zusage ausgerechnet fuer die haeufigste Form eines
Geheimnisses nicht.

Beides wird hier an der Aussenkante geprueft — am zugestellten Feld und am
Ergebnis der Schwaerzung —, nicht an Zwischenschritten. Ein Test, der die
Abfrage nachbaut, ist gruen, wenn die Abfrage falsch ist.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiConversation,
    AiMessage,
    AiRun,
    Backup,
    Incident,
    Server,
    User,
)
from services import ai_guardian_report
from services.ai_redaction import ist_geheimer_schluessel
from services.ai_stream_service import _ergebnis_schwaerzen, _FREITEXT_WERKZEUGE
from services.auth_service import AuthService
from services.email_service import EmailService


#: Der Zeitpunkt, um den herum der Ablauf aus dem Befund aufgebaut wird. Eine
#: Stunde zurueck, damit alle Marken in der Vergangenheit liegen und keine davon
#: durch die Laufzeit des Tests in die Zukunft rutscht.
BASIS = datetime.now(timezone.utc) - timedelta(hours=1)


# ── Aufbau ────────────────────────────────────────────────────────────────


def _benutzer(db: Session, name: str = "berichtsempfaenger") -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    user.email_notifications = True
    db.commit()
    db.refresh(user)
    return user


def _server(db: Session, name: str = "Guardian-Server") -> Server:
    server = Server(
        name=name,
        game_type="dayz",
        install_dir=f"/tmp/{uuid4().hex[:8]}",
        container_name=f"msm-{uuid4().hex[:8]}",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _vorfall(db: Session, server: Server, *, status: str = "resolved") -> Incident:
    vorfall = Incident(
        server_id=server.id,
        title="Autopilot: process_not_running",
        description="GameThread haengt",
        type="process_not_running",
        status=status,
        fingerprint=f"guardian:{server.id}:{uuid4().hex[:8]}",
        occurrences=3,
        created_at=BASIS,
    )
    db.add(vorfall)
    db.commit()
    db.refresh(vorfall)
    return vorfall


def _lauf(
    db: Session,
    user: User,
    *,
    status: str = "completed",
    antwort: str = "Die Konfiguration war unbrauchbar; ich habe sie berichtigt.",
) -> AiRun:
    """Ein abgeschlossener Heilungslauf samt Abschlusstext des Modells.

    Der Text gehoert dazu und ist kein Beiwerk: ohne eine fertige
    Assistentennachricht setzt `bericht_versenden` den Ersatztext ein, und dann
    bewiese ein gruener Test nur, dass der Rueckfall greift.

    Die Unterhaltung wird wiederverwendet, wenn es schon eine gibt. Es gibt
    genau **eine** je Benutzer — die Datenbank haelt das ueber einen eindeutigen
    Index —, und eine Heilung schreibt in dieselbe wie der Mensch. Zwei Laeufe
    desselben Benutzers teilen sie sich also, und genau das macht die Frage
    "welcher Lauf hat gesichert?" ueberhaupt erst schwierig.
    """
    conversation = (
        db.query(AiConversation).filter(AiConversation.user_id == user.id).first()
    )
    if conversation is None:
        conversation = AiConversation(
            id=str(uuid4()), user_id=user.id, server_id=None, title="Heilung"
        )
        db.add(conversation)
    db.flush()
    db.add(AiMessage(
        id=str(uuid4()),
        conversation_id=conversation.id,
        role="assistant",
        content=antwort,
        status="complete",
    ))
    run = AiRun(
        id=str(uuid4()),
        user_id=user.id,
        conversation_id=conversation.id,
        status=status,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _backup(
    db: Session, server: Server, *, name: str, wann: datetime, nachgewiesen: bool = True
) -> Backup:
    """Eine Backup-Zeile mit ausdruecklichem Namen, Zeitpunkt und Nachweis.

    ``size_mb=0`` ist Absicht: das ist der echte Wert jedes Archivs unter einem
    Megabyte, und der Nachweis haengt allein an `verified_at`.
    """
    zeile = Backup(
        server_id=server.id,
        name=name,
        filename=f"{uuid4().hex}.tar.gz",
        size_mb=0,
        created_at=wann,
        sha256="a" * 64 if nachgewiesen else None,
        verified_at=wann if nachgewiesen else None,
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return zeile


def _vorschlag(
    db: Session,
    *,
    run: AiRun,
    user: User,
    server: Server | None,
    wann: datetime,
    tool_name: str = "propose_backup",
    status: str = "succeeded",
    vorschau: dict | None = None,
) -> AiActionProposal:
    """Die Spur, die ein Werkzeugaufruf hinterlaesst.

    `run_id`, `tool_name`, `status` und `server_id` sind zusammen genau die
    Aussage "**dieser** Lauf hat auf **diesem** Server erfolgreich gesichert" —
    und nur sie darf den Namen in der Mail tragen.
    """
    vorschlag = AiActionProposal(
        id=str(uuid4()),
        conversation_id=run.conversation_id,
        user_id=user.id,
        server_id=None if server is None else server.id,
        tool_name=tool_name,
        payload_encrypted="test-enc-v1::",
        preview_json=json.dumps(vorschau if vorschau is not None else {}),
        requires_confirmation=False,
        autonomous=True,
        status=status,
        correlation_id=str(uuid4()),
        run_id=run.id,
        created_at=wann,
    )
    db.add(vorschlag)
    db.commit()
    db.refresh(vorschlag)
    return vorschlag


def _versenden(db: Session, *, run: AiRun, server: Server, vorfall: Incident) -> dict:
    """Schickt den Bericht ab und gibt zurueck, was zugestellt worden waere.

    Abgefangen wird `_zustellen` und nicht `EmailService.send_ai_healing_report`:
    der echte Weg startet dafuer einen Thread mit eigener Ereignisschleife, und
    ein Test, der auf einen Daemon-Thread wartet, ist ein Test, der irgendwann
    ohne Grund rot ist. Was `bericht_versenden` zusammentraegt, steht vollstaendig
    in diesen Feldern.
    """
    gesehen: dict = {}

    def _merken(**felder) -> None:
        gesehen.update(felder)

    zustand = {"guardian": {"server_id": server.id, "incident_id": vorfall.id}}
    with (
        patch.object(EmailService, "is_configured", staticmethod(lambda: True)),
        patch.object(ai_guardian_report, "_zustellen", _merken),
    ):
        ai_guardian_report.bericht_versenden(db, run=run, zustand=zustand)
    return gesehen


# ── Der Backupname ────────────────────────────────────────────────────────


class TestBackupnameImBericht:
    """Genannt wird nur, was dieser Lauf selbst angelegt und nachgewiesen hat."""

    def test_ohne_vorschlag_dieses_laufs_nennt_die_mail_kein_backup(
        self, db: Session
    ) -> None:
        """Der Kern: ein frisches Serverbackup ist kein Backup **der KI**.

        Der Scheduler legt stuendlich eines an; auf einer belebten Anlage gibt es
        also praktisch immer ein junges, verifiziertes Archiv. Ein Zeitfenster
        als Suchkriterium fand deshalb fast sicher etwas — und behauptete damit
        einen Rueckweg, den die KI nie hergestellt hat. Ein Neustart etwa
        verlangt gar kein Backup.

        Die ausgelassene Zeile ist die richtige Antwort: sie sagt nichts
        Falsches.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)
        _backup(db, server, name="Automatik 03:30", wann=BASIS + timedelta(minutes=25))

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] is None
        # Die Mail geht trotzdem hinaus — nur eben ohne die Zusage.
        assert felder["bericht"]

    def test_genannt_wird_das_backup_der_ki_und_nicht_das_des_schedulers(
        self, db: Session
    ) -> None:
        """Der Ablauf aus dem Befund, Minute fuer Minute.

        Vorfall 03:05, Backup der KI 03:15, Eingriff 03:20, Automatikbackup des
        Schedulers 03:30, Lauf endet 03:35. Die alte Abfrage sortierte absteigend
        ueber ein Zeitfenster und nannte deshalb bevorzugt das **juengste** —
        also das von 03:30, das die Aenderung der KI bereits enthaelt. Wer
        daraufhin zurueckrollt, zementiert sie.

        Der Vorschlag traegt hier bewusst **keinen** Namen in der Vorschau. So
        muss die Zeile aus der Backup-Tabelle kommen, und der Test belegt die
        Auswahl der Zeile statt nur das Durchreichen einer Zeichenkette.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)
        _vorschlag(
            db, run=run, user=user, server=server, wann=BASIS + timedelta(minutes=9)
        )
        _backup(db, server, name="KI-Sicherung 03:15", wann=BASIS + timedelta(minutes=10))
        _backup(db, server, name="Automatik 03:30", wann=BASIS + timedelta(minutes=25))

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] == "KI-Sicherung 03:15"

    def test_der_name_aus_der_vorschau_geht_vor(self, db: Session) -> None:
        """Was die KI vergeben hat, steht bereits im Vorschlag.

        `_execute_backup` legt ihn dort ab. Ihn aus der Backup-Tabelle zu raten
        waere ein zweiter Weg zur selben Angabe — und der Betreiber sucht in der
        Oberflaeche nach genau dem Namen, den er in der Mail gelesen hat.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)
        _vorschlag(
            db,
            run=run,
            user=user,
            server=server,
            wann=BASIS + timedelta(minutes=9),
            vorschau={"backup_name": "Vor der Heilung"},
        )
        _backup(db, server, name="anders benannt", wann=BASIS + timedelta(minutes=10))

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] == "Vor der Heilung"

    def test_ein_vorschlag_eines_anderen_laufs_zaehlt_nicht(self, db: Session) -> None:
        """Ein Lauf berichtet ueber sich, nicht ueber die Unterhaltung.

        Derselbe Benutzer, dieselbe Unterhaltung, derselbe Server: die KI hat
        heute Nachmittag schon einmal gesichert, damals mit einem Menschen davor.
        Fuer die Heilung von heute Nacht beweist das nichts — sie hat nichts
        gesichert, und die Datei, die sie angefasst hat, steht in keinem Archiv.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        frueher = _lauf(db, user)
        run = _lauf(db, user)
        _vorschlag(
            db, run=frueher, user=user, server=server, wann=BASIS + timedelta(minutes=9)
        )
        _backup(db, server, name="Sicherung des anderen Laufs",
                wann=BASIS + timedelta(minutes=10))

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] is None

    def test_ein_vorschlag_eines_anderen_servers_zaehlt_nicht(
        self, db: Session
    ) -> None:
        """Der Nachweis gehoert dem Server, nicht der Anlage.

        Eine Heilung darf mehrere Server beruehren — Lesewerkzeuge sind nicht an
        die Serverbindung gebunden. Ein Backup des Nachbarn holt die Weltdatei
        dieses Servers trotzdem nicht zurueck.
        """
        user = _benutzer(db)
        server = _server(db)
        nachbar = _server(db, "Nachbar")
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)
        _vorschlag(
            db, run=run, user=user, server=nachbar, wann=BASIS + timedelta(minutes=9)
        )
        _backup(db, server, name="Automatik", wann=BASIS + timedelta(minutes=10))
        _backup(db, nachbar, name="Sicherung des Nachbarn",
                wann=BASIS + timedelta(minutes=10))

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] is None

    @pytest.mark.parametrize("status", ["proposed", "confirmed", "executing", "failed"])
    def test_nur_ein_gelungener_vorschlag_zaehlt(
        self, db: Session, status: str
    ) -> None:
        """Vorgeschlagen ist nicht gesichert.

        Zwischen Vorschlag und Ausfuehrung liegt der ganze Weg, auf dem etwas
        schiefgehen kann — zu wenig Platz, ein abgebrochener Agent, ein
        entzogenes Recht. Zaehlte schon der Vorschlag, stuende der Satz in der
        Mail auch dann, wenn das Archiv nie entstanden ist.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)
        _vorschlag(
            db, run=run, user=user, server=server, wann=BASIS + timedelta(minutes=9),
            status=status,
        )
        _backup(db, server, name="Irgendein Archiv", wann=BASIS + timedelta(minutes=10))

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] is None

    def test_ein_anderes_werkzeug_desselben_laufs_zaehlt_nicht(
        self, db: Session
    ) -> None:
        """Gesucht wird `propose_backup` und nicht "irgendein Erfolg".

        Derselbe Lauf hat eine Konfiguration geschrieben — das ist der Eingriff,
        nicht seine Absicherung. Ohne die Bedingung auf den Werkzeugnamen wuerde
        ausgerechnet der Eingriff selbst den Rueckweg belegen.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)
        _vorschlag(
            db, run=run, user=user, server=server, wann=BASIS + timedelta(minutes=9),
            tool_name="propose_config_update",
        )
        _backup(db, server, name="Automatik", wann=BASIS + timedelta(minutes=10))

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] is None

    def test_ohne_verifiziertes_backup_ergibt_der_vorschlag_keinen_namen(
        self, db: Session
    ) -> None:
        """Der Nachweis bleibt Bedingung — auch mit Spur.

        Der Remote-Agent-Pfad legt die Backup-Zeile an, **bevor** der Agent
        gearbeitet hat; sie beweist damit nur, dass jemand angefangen hat.
        `verified_at` traegt die Nachmessung. Ein Name ohne sie waere genau die
        Behauptung, die diese Kopplung nicht erheben soll — und der Vorschlag
        stuende trotzdem auf `succeeded`, weil das Werkzeug seine Arbeit ja
        angestossen hat.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)
        _vorschlag(
            db,
            run=run,
            user=user,
            server=server,
            wann=BASIS + timedelta(minutes=9),
            vorschau={"backup_name": "Vor der Heilung"},
        )
        _backup(
            db, server, name="Vor der Heilung", wann=BASIS + timedelta(minutes=10),
            nachgewiesen=False,
        )

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] is None
        # Die Zeile ist da — genannt wird sie trotzdem nicht. Ohne diese
        # Zusicherung koennte der Test auch gruen sein, weil das Anlegen
        # fehlgeschlagen ist.
        assert db.query(Backup).count() == 1

    def test_ein_backup_von_vor_dem_vorschlag_zaehlt_nicht(self, db: Session) -> None:
        """Der Anker ist der Vorschlag, nicht der Vorfall.

        Ein Archiv, das schon lag, bevor die KI ueberhaupt zu sichern begann,
        beweist ueber ihre Sicherung nichts. Zaehlte es mit, waere die
        Zeitfenster-Vermutung durch die Hintertuer wieder da: irgendein altes
        Nachtbackup traegt dann den Namen in die Mail.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)
        _backup(db, server, name="Nachtbackup", wann=BASIS - timedelta(hours=6))
        _vorschlag(
            db, run=run, user=user, server=server, wann=BASIS + timedelta(minutes=9)
        )

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["backup_name"] is None

    def test_auch_der_fehlgeschlagene_lauf_berichtet(self, db: Session) -> None:
        """"Nicht geschafft" ist die wichtigere Nachricht von beiden.

        Sein Server laeuft nicht, und niemand sass davor. Ein Heilungslauf, der
        still scheitert, waere die schlechteste Eigenschaft dieser ganzen
        Kopplung — und der Backupname muss dann erst recht stimmen, denn genau
        jetzt rollt jemand zurueck.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server, status="open")
        run = _lauf(db, user, status="failed")
        _vorschlag(
            db, run=run, user=user, server=server, wann=BASIS + timedelta(minutes=9)
        )
        _backup(db, server, name="KI-Sicherung", wann=BASIS + timedelta(minutes=10))

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert felder["geheilt"] is False
        assert felder["backup_name"] == "KI-Sicherung"


# ── Der Servername in den Fakten ──────────────────────────────────────────


class TestServernameImBericht:
    """Der Name geht an den Anbieter — also so geschwärzt wie überall sonst.

    Aus diesen Feldern baut `_zustellen` die Fakten des Ausgangskorbs, und der
    Arbeiter reicht sie an das Modell weiter, das die Mail verfasst. Derselbe
    Wert wird im Auftragstext desselben Laufs (`ai_guardian_service`) längst
    geschwärzt und auf 64 Zeichen gekürzt; der Berichtspfad war der einzige
    Ausreißer. Ein Servername ist Betreibertext, kann aber aus einer
    Shop-Bestellung stammen und dann tragen, was dort mitkam.
    """

    def test_ein_tokenmuster_im_servernamen_geht_nicht_an_den_anbieter(
        self, db: Session
    ) -> None:
        user = _benutzer(db)
        server = _server(db, name="DayZ sk-abcdefghijklmnopqrst1234 Livonia")
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert "sk-abcdefghijklmnopqrst1234" not in felder["server_name"]
        # Der Rest bleibt lesbar: der Betreiber soll seinen Server wiedererkennen.
        assert "DayZ" in felder["server_name"]

    def test_der_name_wird_wie_im_auftragstext_gekuerzt(self, db: Session) -> None:
        """64 Zeichen, dieselbe Grenze wie in `ai_guardian_service`.

        Ohne sie trägt der Bericht denselben Namen unbegrenzt lang, während der
        Auftragstext desselben Laufs ihn kürzt — zwei Längen für eine Angabe.
        """
        user = _benutzer(db)
        server = _server(db, name="A" * 200)
        vorfall = _vorfall(db, server)
        run = _lauf(db, user)

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert len(felder["server_name"]) == 64


# ── Der Abschlusstext ─────────────────────────────────────────────────────


class TestAbschlusstextImBericht:
    """Was der Betreiber liest, ist der Schluss des Laufs — nicht sein Anfang."""

    def test_bei_langem_protokoll_steht_das_ergebnis_in_der_mail(
        self, db: Session
    ) -> None:
        """Von hinten schneiden, nicht von vorne.

        `content` ist das Protokoll des ganzen Laufs, und `MITREDEN` verlangt vor
        jedem Werkzeugaufruf einen Satz. Vorne stehen deshalb die Ankündigungen
        ("Ich sehe mir zuerst die Logs an"), hinten steht das Ergebnis. Diese
        Funktion schnitt hier `[:MAX_BERICHT_ZEICHEN]` — während die wortgleiche
        Kopie im Aufgabenbericht denselben Ausdruck längst als behobenen Fehler
        beschrieb. Ein Heilungslauf hat mehr Runden als ein Aufgabenlauf, also
        mehr Ansagen: die Mail traf ausgerechnet im wichtigeren Fall daneben.
        """
        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server)
        ansagen = "\n\n".join(
            f"Ich sehe mir jetzt Schritt {nummer} an: " + "die nächste Logdatei. " * 12
            for nummer in range(20)
        )
        ergebnis = (
            "Ergebnis: der Dienst horchte auf der falschen Adresse. Ich habe die "
            "Bindung berichtigt und den Server neu gestartet; er nimmt wieder "
            "Verbindungen an."
        )
        protokoll = ansagen + "\n\n" + ergebnis
        # Ohne diese Zusicherung wäre der Test auch grün, wenn das Protokoll
        # unter die Grenze rutscht und schlicht vollständig mitgeht.
        assert len(protokoll) > 4000
        run = _lauf(db, user, antwort=protokoll)

        felder = _versenden(db, run=run, server=server, vorfall=vorfall)

        assert ergebnis in felder["bericht"]
        assert "Schritt 0" not in felder["bericht"]

    def test_der_bericht_zitiert_keinen_fremden_zug(self, db: Session) -> None:
        """Es gibt genau **eine** Unterhaltung je Benutzer.

        Chat, Heilungen und fällige Aufträge landen alle darin
        (`uq_ai_conversations_user`). Endet ein Lauf, ohne selbst eine fertige
        Antwort geschrieben zu haben — erschöpftes Kontingent, abgebrochenes
        Segment —, fand die Abfrage die jüngste Antwort aus einem völlig anderen
        Zug. Beim Heilungsbericht heißt das: die Mail zum Vorfall trägt eine
        Schilderung einer Untersuchung, die dieser Lauf nie geführt hat.

        Der Anker ist die eigene Benutzernachricht des Laufs.
        """
        from services import ai_run_service

        user = _benutzer(db)
        server = _server(db)
        vorfall = _vorfall(db, server, status="open")
        # Der Zug von gestern: eine fertige Antwort in derselben Unterhaltung.
        vorlauf = _lauf(db, user, antwort="Ich habe Server 12 gelöscht.")
        # Und die Heilung von heute Nacht, die selbst nichts geschrieben hat.
        conversation_id = vorlauf.conversation_id
        anker_id = str(uuid4())
        db.add(AiMessage(
            id=anker_id,
            conversation_id=conversation_id,
            role="user",
            content="Guardian meldet: process_not_running.",
            status="complete",
        ))
        run = AiRun(
            id=str(uuid4()),
            user_id=user.id,
            conversation_id=conversation_id,
            status="failed",
        )
        db.add(run)
        db.flush()
        zustand = ai_run_service.leerer_zustand(
            [], request_id=str(uuid4()), user_message_id=anker_id
        )
        zustand["guardian"] = {"server_id": server.id, "incident_id": vorfall.id}
        ai_run_service.zustand_schreiben(run, zustand)
        db.commit()

        gesehen: dict = {}
        with (
            patch.object(EmailService, "is_configured", staticmethod(lambda: True)),
            patch.object(
                ai_guardian_report, "_zustellen", lambda **f: gesehen.update(f)
            ),
        ):
            ai_guardian_report.bericht_versenden(db, run=run, zustand=zustand)

        assert "Server 12" not in gesehen["bericht"]
        # Die Mail geht trotzdem hinaus — mit dem ehrlichen Ersatztext.
        assert "KI-Chat" in gesehen["bericht"]


# ── Der Neustart mitten im Lauf ───────────────────────────────────────────


def test_ein_neustart_waehrend_der_heilung_berichtet_trotzdem(db: Session) -> None:
    """`unterbrochene_laeufe_abgleichen` ist ein Endzustand wie jeder andere.

    Hier stand nur der Statuswechsel auf 'failed'. Damit umging ausgerechnet
    dieser Weg die Stelle, an der die Berichtsmails hängen: fällt das Panel
    während einer Heilung aus — Deploy, Absturz, Host-Neustart —, sagt
    `ai_guardian_report` einen Bericht "bei jedem Endzustand" zu, und keiner ging
    hinaus. Der Server stand weiter, und der Betreiber erfuhr nichts davon.

    Gleichzeitig die Gegenprobe zum Arbeitsgedächtnis: `provider_messages` trägt
    den entschlüsselten Gedächtnisblock, und `state_json` ist eine gewöhnliche
    Textspalte, die nie wieder geleert wurde.
    """
    from services import ai_run_service

    user = _benutzer(db)
    server = _server(db)
    vorfall = _vorfall(db, server, status="open")
    run = _lauf(db, user, status="running")
    zustand = ai_run_service.leerer_zustand(
        [{"role": "user", "content": "Merkzettel: der Betreiber heißt Maik."}],
        request_id=str(uuid4()),
    )
    zustand["guardian"] = {"server_id": server.id, "incident_id": vorfall.id}
    ai_run_service.zustand_schreiben(run, zustand)
    db.commit()

    gesehen: dict = {}
    with (
        patch.object(EmailService, "is_configured", staticmethod(lambda: True)),
        patch.object(ai_guardian_report, "_zustellen", lambda **f: gesehen.update(f)),
    ):
        anzahl = ai_run_service.unterbrochene_laeufe_abgleichen(db)

    assert anzahl == 1
    db.refresh(run)
    assert run.status == "failed"
    assert run.stop_reason == "process_restart"
    assert gesehen["geheilt"] is False
    assert "Merkzettel" not in (run.state_json or "")


# ── Die Schwaerzung strukturierter Werte ──────────────────────────────────


#: Schreibweisen, die `_SECRET_KEY_RE` treffen soll — abgeleitet aus dem Muster
#: selbst und nicht geraten: ein optionaler Praefix aus Wortteilen, getrennt
#: durch Unterstrich, Punkt oder Bindestrich, davor eines der Schluesselwoerter
#: `password|passwd|secret|token|api[_-]?key|authorization|credential`.
#:
#: Die Praefixe sind der eigentliche Punkt. `\b` scheitert am Unterstrich, weil
#: der ein Wortzeichen ist — und `RCON_PASSWORD` ist nicht irgendeine
#: Schreibweise, sondern die uebliche fuer Umgebungsvariablen.
GEHEIME_SCHLUESSEL = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "authorization",
    "credential",
    "RCON_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "OPENAI_API_KEY",
    "DB_SECRET",
    "AUTH_TOKEN",
    "db.password",
    "x-api-key",
    "Authorization",
)

#: Und was unberuehrt bleiben muss. Ein zu gieriges Muster ist hier kein
#: harmloser Ueberschuss: das Modell arbeitet mit diesen Ergebnissen, und ein
#: `"port": "[REDACTED]"` macht die Diagnose einer falschen Bindung unmoeglich.
HARMLOSE_SCHLUESSEL = (
    "port",
    "size_mb",
    "name",
    "description",
    "status",
    "server_id",
    "game_type",
    "max_tokens",
)


class TestGeheimeSchluessel:
    """Das Muster allein, ohne Datenstruktur darum herum."""

    @pytest.mark.parametrize("schluessel", GEHEIME_SCHLUESSEL)
    def test_ein_geheimer_schluessel_wird_erkannt(self, schluessel: str) -> None:
        assert ist_geheimer_schluessel(schluessel) is True

    @pytest.mark.parametrize("schluessel", HARMLOSE_SCHLUESSEL)
    def test_ein_harmloser_schluessel_bleibt_harmlos(self, schluessel: str) -> None:
        assert ist_geheimer_schluessel(schluessel) is False

    @pytest.mark.parametrize("wert", [None, 42, True, ("password",), {"password": 1}])
    def test_was_kein_text_ist_ist_kein_schluessel(self, wert: object) -> None:
        """JSON kennt nur Zeichenketten als Schluessel — Python nicht.

        Ein Werkzeugergebnis kann ein Woerterbuch mit Zahlenschluesseln liefern
        (eine Zuordnung nach Server-ID etwa). Ohne die `isinstance`-Pruefung
        flaeche `.strip()` dort mit `AttributeError` auf — und zwar im
        Schwaerzungspfad, also an der Stelle, an der ein Fehler bedeutet, dass
        gar nichts geschwaerzt wird.
        """
        assert ist_geheimer_schluessel(wert) is False


class TestSchwaerzungStrukturierterWerte:
    """Der Schluessel entscheidet ueber seinen Wert — auch tief im Baum."""

    def test_das_passwort_aus_dem_blueprint_kommt_geschwaerzt_heraus(self) -> None:
        """Der Fall aus dem Befund, unveraendert.

        `read_blueprint` liefert genau diese Form. Weitergereicht wurde bisher
        nur ``"hunter2"``, und darauf passt kein Zuweisungsmuster — es fehlen
        Schluessel und Trennzeichen, die auf der Ebene darueber stehen. Das
        Passwort ging im Klartext an den Modellanbieter und in
        `ai_tool_results`.
        """
        ergebnis = _ergebnis_schwaerzen(
            {"runtime": {"env": {"RCON_PASSWORD": "hunter2"}}}
        )

        assert ergebnis == {"runtime": {"env": {"RCON_PASSWORD": "[REDACTED]"}}}
        assert "hunter2" not in json.dumps(ergebnis)

    @pytest.mark.parametrize("schluessel", GEHEIME_SCHLUESSEL)
    def test_jede_schreibweise_wird_geschwaerzt(self, schluessel: str) -> None:
        """Parametrisiert ueber dieselbe Liste wie das Muster oben.

        Der Zusammenhang ist die Zusage: was `ist_geheimer_schluessel` erkennt,
        muss die Rekursion auch anwenden. Zwei Listen waeren zwei Wahrheiten —
        die eine wuerde irgendwann um einen Fall erweitert und die andere nicht.
        """
        ergebnis = _ergebnis_schwaerzen({"env": {schluessel: "hunter2"}})

        assert ergebnis["env"][schluessel] == "[REDACTED]"

    def test_der_schluessel_selbst_bleibt_lesbar(self) -> None:
        """Geschwaerzt wird der Wert, nie der Name.

        Die Namen stammen aus dem Code und nicht aus den Daten, und ein
        Ergebnis mit unkenntlichen Schluesseln waere fuer das Modell nicht mehr
        auswertbar — es koennte nicht einmal mehr sagen, *dass* dort ein
        Passwort steht.
        """
        ergebnis = _ergebnis_schwaerzen({"env": {"RCON_PASSWORD": "hunter2"}})

        assert list(ergebnis["env"]) == ["RCON_PASSWORD"]

    def test_ein_teilbaum_unter_einem_geheimen_schluessel_faellt_als_ganzes(
        self,
    ) -> None:
        """Nicht hineinsteigen, sondern ersetzen.

        Unter einem Schluessel wie `credential` stehen die eigentlichen Angaben
        oft unter voellig harmlosen Namen — `user`, `pass`, `host`. Wer weiter
        hineinsteigt, verlaesst sich auf diese inneren Namen; der aeussere hat
        aber schon gesagt, worum es geht.
        """
        ergebnis = _ergebnis_schwaerzen(
            {"credential": {"user": "msm", "pass": "hunter2", "host": "db"}}
        )

        assert ergebnis == {"credential": "[REDACTED]"}
        assert "hunter2" not in json.dumps(ergebnis)

    def test_auch_eine_liste_unter_einem_geheimen_schluessel_faellt(self) -> None:
        """Dieselbe Regel, andere Form.

        Eine Liste von Tokens ist kein Sonderfall, sondern derselbe Fall — und
        ohne diese Zusage waere die Schwaerzung durch die blosse Wahl der
        Datenstruktur zu umgehen.
        """
        ergebnis = _ergebnis_schwaerzen({"token": ["hunter2", "hunter3"]})

        assert ergebnis == {"token": "[REDACTED]"}
        assert "hunter2" not in json.dumps(ergebnis)

    def test_die_rekursion_erreicht_listen_von_woerterbuechern(self) -> None:
        """Werkzeugergebnisse sind verschachtelt, nicht flach.

        `read_guardian_incidents` liefert eine Liste von Vorfaellen,
        `list_my_servers` eine Liste von Servern. Ein Geheimnis, das erst in der
        dritten Ebene steht, ist kein exotischer Fall.
        """
        ergebnis = _ergebnis_schwaerzen(
            {"servers": [{"env": {"DB_SECRET": "hunter2"}}, {"env": {"port": 2302}}]}
        )

        assert ergebnis["servers"][0]["env"]["DB_SECRET"] == "[REDACTED]"
        assert ergebnis["servers"][1]["env"]["port"] == 2302

    @pytest.mark.parametrize("schluessel", HARMLOSE_SCHLUESSEL)
    def test_harmlose_schluessel_bleiben_unberuehrt(self, schluessel: str) -> None:
        """Die Gegenprobe — ohne sie waere die Zusage auch durch Totalsperre erfuellt.

        Ein zu gieriges Muster macht die Ergebnisse fuer das Modell unbrauchbar,
        und das faellt niemandem auf: die KI antwortet dann eben schlechter,
        ohne dass irgendwo ein Fehler steht.
        """
        ergebnis = _ergebnis_schwaerzen({schluessel: "sichtbar"})

        assert ergebnis[schluessel] == "sichtbar"

    def test_zahlen_und_wahrheitswerte_bleiben_was_sie_sind(self) -> None:
        """Ein Port ist kein Geheimnis und eine Groesse in Megabyte auch nicht.

        Wichtiger noch als der Wert ist der **Typ**: das Modell rechnet mit
        diesen Zahlen und vergleicht die Wahrheitswerte. Aus ``2302`` eine
        Zeichenkette zu machen, waere ein stiller Formatwechsel mitten im
        Ergebnis.
        """
        ergebnis = _ergebnis_schwaerzen(
            {"port": 2302, "size_mb": 0, "running": True, "verified": False,
             "expires_at": None}
        )

        assert ergebnis == {
            "port": 2302, "size_mb": 0, "running": True, "verified": False,
            "expires_at": None,
        }
        assert isinstance(ergebnis["port"], int)
        assert ergebnis["running"] is True
        assert ergebnis["verified"] is False

    def test_der_zuweisungstext_wird_weiterhin_geschwaerzt(self) -> None:
        """Die Muster fuer Fliesstext bleiben in Kraft.

        Die Schluesselpruefung kommt **zusaetzlich** und nicht an ihrer Stelle:
        eine Konfigurationszeile, die als Wert in einem Werkzeugergebnis steht,
        traegt Schluessel und Wert in derselben Zeichenkette und wird weiterhin
        vom Zuweisungsmuster erwischt.
        """
        ergebnis = _ergebnis_schwaerzen({"content": "RCON_PASSWORD=hunter2\nport=2302"})

        assert "hunter2" not in ergebnis["content"]
        assert "port=2302" in ergebnis["content"]

    def test_die_freitextmarke_erreicht_auch_verschachtelten_text(self) -> None:
        """`freitext=True` gilt fuer den ganzen Baum, nicht nur die oberste Ebene.

        Die Adresse eines Spielers steht in einer Logzeile, und Logzeilen kommen
        als Liste in einem Woerterbuch an. Reichte die Marke nicht mit hinunter,
        waere die IP-Schwaerzung genau dort wirkungslos, wo sie gebraucht wird.

        Die private Adresse bleibt bewusst stehen: sie ist die Bindeadresse des
        Dienstes und die Zeile, an der man "laeuft, aber niemand kommt drauf"
        erkennt.

        Die oeffentliche Adresse ist bewusst keine aus den Dokumentationsnetzen
        (`203.0.113.0/24` und Geschwister): Python zaehlt die seit 3.13 zu
        `is_private`, und ein Test damit waere aus dem falschen Grund rot.
        """
        ergebnis = _ergebnis_schwaerzen(
            {"lines": ["Spieler 93.184.216.34 verbunden", "bind 192.168.1.50"]},
            freitext=True,
        )

        assert "93.184.216.34" not in ergebnis["lines"][0]
        assert "192.168.1.50" in ergebnis["lines"][1]


class TestWelcheWerkzeugeFreitextLiefern:
    """Welches Werkzeug es war, darf über den Datenschutz nicht entscheiden.

    Genau das war der Zustand: `read_server_logs` schwärzte fremde Adressen,
    `read_config` nicht — und der Prompt führt das Modell für eine
    Absturzanalyse ausdrücklich über `search_server_files` nach `read_config`.
    Dieselbe Logzeile ging damit je nach Werkzeugwahl des Modells geschwärzt
    oder im Klartext an den Anbieter.

    Geprüft wird über die echte Menge und nicht über eine Kopie der Namen: ein
    Test mit eigener Liste bliebe grün, wenn jemand die Menge im Code
    zurückdreht.
    """

    #: Werkzeuge, deren Ergebnis Text ist, den der Server oder ein Spieler
    #: geschrieben hat. Dateiinhalte gehören dazu — `read_config` liest jede
    #: Textdatei, nicht nur Konfigurationen.
    FREITEXT = (
        "read_server_logs",
        "read_guardian_incidents",
        "read_config",
        "search_server_files",
    )

    @pytest.mark.parametrize("werkzeug", FREITEXT)
    def test_die_adresse_eines_spielers_geht_nicht_hinaus(self, werkzeug: str) -> None:
        assert werkzeug in _FREITEXT_WERKZEUGE

        ergebnis = _ergebnis_schwaerzen(
            {"content": "[12:34:56] Anna joined from 93.184.216.34"},
            freitext=werkzeug in _FREITEXT_WERKZEUGE,
        )

        assert "93.184.216.34" not in ergebnis["content"]

    def test_die_netzwerkangaben_bleiben_die_ausnahme(self) -> None:
        """`read_server_network` bleibt draußen — und muss draußen bleiben.

        Es liefert die Bind-Adresse als Betriebsangabe, und ohne sie kann die KI
        eine falsche Bindung weder erkennen noch mit `propose_bind_ip_update`
        berichtigen. Das ist die eine Stelle, an der eine öffentliche Adresse
        keine Person bezeichnet, sondern eine Einstellung.
        """
        werkzeug = "read_server_network"
        assert werkzeug not in _FREITEXT_WERKZEUGE

        ergebnis = _ergebnis_schwaerzen(
            {"bind_address": "93.184.216.34", "port": 2302},
            freitext=werkzeug in _FREITEXT_WERKZEUGE,
        )

        assert ergebnis["bind_address"] == "93.184.216.34"
