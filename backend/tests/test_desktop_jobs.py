"""Die Bruecke zum Rechner des Benutzers: abholen, melden, verfallen.

Die Invarianten, die hier festgehalten sind:

1. Ein Auftrag gehoert genau einem Benutzer. Ein fremder ist nicht zu finden —
   404, nicht 403: wer keinen Zugriff hat, soll nicht erfahren, dass es ihn gibt.
2. Ohne `ai.desktop.use` gibt es weder Abholen noch Melden.
3. Argumente und Ergebnis liegen verschluesselt in der Datenbank. Ein Blick in
   die Zeile darf den Dateiinhalt nicht zeigen.
4. Ein Lauf wird erst geweckt, wenn **alle** Auftraege seiner Runde beisammen
   sind — sonst saehe das Modell halbe Ergebnisse und wuerde raten.
5. Ein Rechner, der ausgeht, laesst keinen Lauf haengen: die Frist macht aus
   dem Warten einen benannten Fehlschlag.
6. Die Bruecke gehoert der App. Eine Panel-Sitzung findet sie nicht — dieselbe
   Herkunftsgrenze, die schon den Werkzeugkatalog schneidet.
7. Ein abgeschlossener Auftrag bleibt nicht liegen: seine Zeile traegt
   Bildschirmfotos und Dateiinhalte vom Arbeitsplatz des Benutzers.
8. Ein Auftrag gehoert einem **Geraet**, nicht nur einem Benutzer. Wer zwei
   Rechner gekoppelt hat, soll den Blick auf "meinen Bildschirm" von dem
   bekommen, an dem er sitzt — nicht von dem, der zuerst nach Arbeit fragt.
   Und quittieren darf ihn nur derselbe: sonst holt der Laptop den Blick auf
   den Bildschirm, und der Buerorechner meldet sein eigenes Foto darauf.
"""

from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from models import AiConversation, AiRun, DesktopJob, Role, RolePermission, User
from services import desktop_job_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _app_kopf(user: User, jti: str = "desktop-1", familie: str | None = None) -> dict:
    """Der Zugang, den die gekoppelte App vorlegt: Bearer mit `geraet`.

    Genau die Ansprueche, die `session_service.issue_session` fuer ein
    gekoppeltes Geraet ausstellt. Kein Cookie — die Bruecke ist an die Herkunft
    gebunden, und ein Browser schickt einen Authorization-Header nie von
    selbst mit.

    ``familie`` ist die Refresh-Familie dieses Geraets. Ohne sie sieht das
    Token aus wie eines von vor dem Anspruch, und der Endpunkt schneidet nicht
    — genau der Rueckfall, den `naechster` "der Fragende nennt kein Geraet"
    nennt.
    """
    ansprueche: dict = {
        "sub": user.username, "user_id": user.id, "jti": jti, "geraet": "desktop",
    }
    if familie:
        ansprueche["familie"] = familie
    return {"Authorization": f"Bearer {AuthService.create_access_token(ansprueche)}"}


def _anfrage_mit_bearer(marke: str | None) -> Request:
    """Eine nackte Anfrage mit (oder ohne) Authorization-Header.

    `session_familie` liest nur Header und Cookies — ein echter Request-Aufbau
    waere hier Kulisse. Kein TestClient: die Kennung soll unabhaengig davon
    geprueft werden, welcher Endpunkt sie spaeter benutzt.
    """
    kopf = [(b"authorization", f"Bearer {marke}".encode())] if marke else []
    return Request({"type": "http", "headers": kopf})


def _rolle_mit(db: Session, user: User, *rechte: str) -> None:
    role = Role(name=f"desktop-{'-'.join(rechte) or 'leer'}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for recht in rechte:
        db.add(RolePermission(role_id=role.id, permission_key=recht))
    db.commit()
    set_user_roles(db, user, [role.id])


def _geraeteauftrag(
    db: Session, user: User, run: AiRun, ruf: str, familie: str | None
) -> DesktopJob:
    """Ein Auftrag mit (oder ohne) Geraetekennung.

    Der Werkzeugname ist mit Bedacht `desktop_system` und nicht eines der
    beiden, die auf einen Menschen warten koennen: die bekaemen die lange
    Frist (`_wartet_auf_menschen`) und fallen nicht mehr in die Warteschlange
    zurueck — ein Verhalten, das mit der Geraetebindung nichts zu tun hat und
    die Tests hier nur truebte.
    """
    return desktop_job_service.anlegen(
        db,
        user_id=user.id,
        run_id=run.id,
        tool_call_id=ruf,
        tool_name="desktop_system",
        arguments={"aktion": "bildschirm"},
        familie=familie,
    )


def _lauf(db: Session, user: User) -> AiRun:
    conversation = AiConversation(
        id=f"konv-{user.id}", user_id=user.id, kind="primary", title="Test"
    )
    db.add(conversation)
    db.flush()
    run = AiRun(
        id=f"lauf-{user.id}",
        conversation_id=conversation.id,
        user_id=user.id,
        status="waiting_wake",
    )
    db.add(run)
    db.commit()
    return run


class TestAbholen:
    def test_auftrag_wird_geholt_und_gilt_danach_als_in_arbeit(
        self, client: TestClient, db: Session, regular_user: User
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={"aktion": "lesen", "pfad": "notiz.txt"},
        )
        db.commit()

        kopf = _app_kopf(regular_user)
        antwort = client.get("/api/desktop/jobs/next", headers=kopf)
        assert antwort.status_code == 200
        daten = antwort.json()
        assert daten["id"] == job.id
        assert daten["tool_name"] == "desktop_dateien"
        assert daten["arguments"] == {"aktion": "lesen", "pfad": "notiz.txt"}

        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "taken"

        # Zweimal abholen liefert denselben Auftrag nicht doppelt.
        assert client.get("/api/desktop/jobs/next", headers=kopf).status_code == 204

    def test_ohne_auftrag_kommt_204(
        self, client: TestClient, db: Session, regular_user: User
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        antwort = client.get("/api/desktop/jobs/next", headers=_app_kopf(regular_user))
        assert antwort.status_code == 204

    def test_ohne_recht_kein_zugriff(
        self, client: TestClient, db: Session, regular_user: User
    ):
        _rolle_mit(db, regular_user, "ai.chat.use")
        antwort = client.get("/api/desktop/jobs/next", headers=_app_kopf(regular_user))
        assert antwort.status_code == 403

    def test_fremder_auftrag_wird_nicht_ausgeliefert(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        owner_user: User,
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        fremder_lauf = _lauf(db, owner_user)
        desktop_job_service.anlegen(
            db,
            user_id=owner_user.id,
            run_id=fremder_lauf.id,
            tool_call_id="call-fremd",
            tool_name="desktop_dateien",
            arguments={"pfad": "geheim.txt"},
        )
        db.commit()

        antwort = client.get("/api/desktop/jobs/next", headers=_app_kopf(regular_user))
        assert antwort.status_code == 204

    def test_das_abholen_sperrt_die_zeile(self, db: Session, regular_user: User):
        """Zwischen Lesen und `taken` darf kein zweiter dieselbe Zeile sehen.

        Die App fragt im Sekundentakt, mehrere Arbeitsprozesse sind vorgesehen:
        ohne Sperre sehen zwei gleichzeitige Abfragen unter PostgreSQL/READ
        COMMITTED denselben Auftrag, und der Rechner fuehrt ihn zweimal aus.

        Geprueft wird das an der **erzeugten Abfrage** und nicht am Verhalten:
        SQLite kennt `FOR UPDATE` nicht und serialisiert Schreibzugriffe
        ohnehin — genau deshalb ist der Fehler in den Tests nie aufgefallen.
        """
        run = _lauf(db, regular_user)
        desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={},
        )
        db.commit()

        gesehen: list[str] = []

        def _mitschreiben(kontext):
            gesehen.append(str(kontext.statement.compile(dialect=postgresql.dialect())))

        event.listen(db, "do_orm_execute", _mitschreiben)
        try:
            assert desktop_job_service.naechster(db, user_id=regular_user.id) is not None
        finally:
            event.remove(db, "do_orm_execute", _mitschreiben)

        assert any("FOR UPDATE SKIP LOCKED" in befehl for befehl in gesehen)


class TestMelden:
    def test_ergebnis_schliesst_den_auftrag(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={"pfad": "notiz.txt"},
        )
        db.commit()

        antwort = client.post(
            f"/api/desktop/jobs/{job.id}/result",
            json={"ok": True, "ergebnis": {"inhalt": "Hallo"}},
            headers=_app_kopf(regular_user),
        )
        assert antwort.status_code == 204

        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "done"
        assert desktop_job_service.ergebnisse(db, [job.id]) == [
            {"tool_name": "desktop_dateien", "status": "done", "ergebnis": {"inhalt": "Hallo"}}
        ]

    def test_fremdes_ergebnis_ist_nicht_zu_finden(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        owner_user: User,
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        fremder_lauf = _lauf(db, owner_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=owner_user.id,
            run_id=fremder_lauf.id,
            tool_call_id="call-fremd",
            tool_name="desktop_dateien",
            arguments={},
        )
        db.commit()

        antwort = client.post(
            f"/api/desktop/jobs/{job.id}/result",
            json={"ok": True, "ergebnis": {}},
            headers=_app_kopf(regular_user),
        )
        assert antwort.status_code == 404
        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "pending"

    def test_zweite_meldung_ueberschreibt_die_erste_nicht(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={},
        )
        db.commit()

        kopf = _app_kopf(regular_user)
        for inhalt, erwartet in (("erste", 204), ("zweite", 204)):
            assert client.post(
                f"/api/desktop/jobs/{job.id}/result",
                json={"ok": True, "ergebnis": {"inhalt": inhalt}},
                headers=kopf,
            ).status_code == erwartet

        db.expire_all()
        ergebnis = desktop_job_service.ergebnisse(db, [job.id])[0]
        assert ergebnis["ergebnis"] == {"inhalt": "erste"}


class TestHerkunft:
    """Aus dem Panel erreicht nichts den Rechner des Benutzers.

    Bis zum 23.08.2026 verlangten beide Endpunkte nur `ai.desktop.use` und nie
    die Herkunft. Damit konnte jeder gewoehnliche Panel-Tab die Nutzlast eines
    Auftrags lesen — Pfade, zu tippenden Text, die Loeschliste vom Rechner des
    Benutzers — und ein erfundenes Ergebnis melden. Und weil das Abholen ein
    GET ist, das den Zustand veraendert, genuegte fuer den Diebstahl eines
    wartenden Auftrags sogar eine Top-Level-Navigation von einer fremden Seite
    (SameSite=Lax schickt das Access-Cookie dabei mit).
    """

    def test_eine_panel_sitzung_holt_keinen_auftrag_ab(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={"pfad": "notiz.txt"},
        )
        db.commit()

        antwort = client.get("/api/desktop/jobs/next", cookies=user_cookies)

        # 404 und nicht 403: wer nicht dazugehoert, soll aus der Antwort nicht
        # lernen, dass hier gerade etwas wartet.
        assert antwort.status_code == 404
        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "pending"

    def test_eine_panel_sitzung_meldet_kein_ergebnis(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
        user_csrf_token: str,
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={},
        )
        db.commit()

        antwort = client.post(
            f"/api/desktop/jobs/{job.id}/result",
            json={"ok": True, "ergebnis": {"inhalt": "erfunden"}},
            headers={"X-CSRF-Token": user_csrf_token},
            cookies=user_cookies,
        )

        assert antwort.status_code == 404
        db.expire_all()
        zeile = db.get(DesktopJob, job.id)
        assert zeile.status == "pending"
        assert zeile.result_encrypted is None


class TestGeraetebindung:
    """Zwei gekoppelte Rechner, und ein Auftrag gehoert genau einem davon.

    `user_id` reicht dafuer nicht. Mehrere Geraete je Benutzer sind
    ausdruecklich vorgesehen (`device_pairing_service.geraete`, Geraeteliste mit
    einzelnem Widerruf), und jedes fragt im Sekundentakt nach Arbeit. Wer den
    Auftrag bekam, entschied damit der Zufall des Taktes — fuer einen Blick auf
    den Bildschirm oder eine Uebernahme von Maus und Tastatur ist das der
    falsche Rechner.

    Die Kennung ist die Refresh-Familie der Sitzung: derselbe Wert, unter dem
    die Geraeteliste ein Geraet fuehrt, und der einzige, der die
    Token-Rotation ueberlebt.
    """

    def test_der_auftrag_geht_nur_an_sein_geraet(
        self, db: Session, regular_user: User
    ):
        """Der Buerorechner fragt zuerst und bekommt trotzdem nicht den
        Auftrag, der fuer den Laptop gemeint war."""
        run = _lauf(db, regular_user)
        fuer_laptop = _geraeteauftrag(db, regular_user, run, "call-laptop", "fam-laptop")
        fuer_buero = _geraeteauftrag(db, regular_user, run, "call-buero", "fam-buero")
        # Der Laptop-Auftrag ist der aeltere — ohne den Geraeteschnitt bekaeme
        # ihn jeder Fragende zuerst.
        fuer_laptop.created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

        geholt = desktop_job_service.naechster(
            db, user_id=regular_user.id, familie="fam-buero"
        )
        assert geholt is not None and geholt.id == fuer_buero.id

        # Und der Laptop bekommt seinen — nicht den, der schon unterwegs ist.
        auch = desktop_job_service.naechster(
            db, user_id=regular_user.id, familie="fam-laptop"
        )
        assert auch is not None and auch.id == fuer_laptop.id

    def test_ein_fremdes_geraet_sieht_den_auftrag_gar_nicht(
        self, db: Session, regular_user: User
    ):
        """Die Gegenprobe zum Test darueber: kein zweiter Auftrag, den der
        Fragende stattdessen bekommen koennte."""
        run = _lauf(db, regular_user)
        job = _geraeteauftrag(db, regular_user, run, "call-1", "fam-laptop")
        db.commit()

        assert desktop_job_service.naechster(
            db, user_id=regular_user.id, familie="fam-buero"
        ) is None
        db.expire_all()
        # Und er wartet weiter auf seinen Rechner, statt verbraucht zu sein.
        assert db.get(DesktopJob, job.id).status == "pending"

    def test_ein_auftrag_ohne_kennung_bleibt_fuer_alle_abholbar(
        self, db: Session, regular_user: User
    ):
        """Der Bestand aus der Zeit vor der Spalte.

        Ohne diese Ausnahme haetten im Moment des Deploys alle wartenden
        Auftraege bis zu ihrer Frist gehangen: sie tragen keine Kennung, und
        kein Geraet haette sie je wieder gesehen.
        """
        run = _lauf(db, regular_user)
        job = _geraeteauftrag(db, regular_user, run, "call-alt", None)
        db.commit()
        assert job.device_family is None

        geholt = desktop_job_service.naechster(
            db, user_id=regular_user.id, familie="fam-egal"
        )
        assert geholt is not None and geholt.id == job.id

    def test_ein_fragender_ohne_kennung_bekommt_weiterhin_alles(
        self, db: Session, regular_user: User
    ):
        """Die andere Richtung: ein Access-Token von vor dem Anspruch.

        Es laeuft in Minuten ab; eine Sitzung, die bis dahin gar keine Arbeit
        mehr bekommt, waere das schlechtere Ergebnis.
        """
        run = _lauf(db, regular_user)
        job = _geraeteauftrag(db, regular_user, run, "call-1", "fam-laptop")
        db.commit()

        geholt = desktop_job_service.naechster(db, user_id=regular_user.id)
        assert geholt is not None and geholt.id == job.id

    def test_der_auftrag_erbt_das_geraet_aus_dem_laufzustand(
        self, db: Session, regular_user: User
    ):
        """Der Weg vom Lauf zum Auftrag — die Stelle, an der die Kennung
        verlorenginge.

        Zwischen der Anfrage, die den Lauf begonnen hat, und dem Anlegen des
        Auftrags liegen beliebig viele Segmente: der Lauf schlaeft dazwischen
        in der Datenbank. Deshalb steht das Geraet im Laufzustand und wird von
        dort gelesen, genau wie die Herkunft daneben.
        """
        from services.ai_stream_service import _desktop_behandeln
        from services.openai_compatible_adapter import ProviderToolCall, StreamUsage

        run = _lauf(db, regular_user)
        usage = StreamUsage()
        usage.tool_calls = [
            ProviderToolCall(
                id="c1", name="desktop_dateien", arguments={"pfad": "notiz.txt"}
            )
        ]

        frist, budget = _desktop_behandeln(
            current_usage=usage,
            run_id=run.id,
            user_id=regular_user.id,
            herkunft="desktop",
            provider_messages=[],
            zustand={"rounds": 0, "herkunft": "desktop", "familie": "fam-laptop"},
            rundentext="",
            rundendeckel=8,
        )
        assert budget is False and frist is not None

        db.expire_all()
        zeile = db.query(DesktopJob).filter(DesktopJob.run_id == run.id).one()
        assert zeile.device_family == "fam-laptop"
        # Und der andere Rechner kommt nicht an ihn heran.
        assert desktop_job_service.naechster(
            db, user_id=regular_user.id, familie="fam-buero"
        ) is None


class TestGeraetebindungAmEndpunkt:
    """Dieselbe Bindung — aber auf dem Weg, den es in der Anwendung gibt.

    Die Tests darueber uebergeben `familie=` selbst und koennen deshalb genau
    das nicht sehen, woran die Bindung bis zum 23.08.2026 scheiterte: Spalte,
    Anspruch und Filter waren gebaut, und der einzige Produktionsaufrufer rief
    weiter `naechster(db, user_id=...)` ohne die Kennung. Der Zweig `if familie
    is not None` lief nie an, und der Auftrag ging wie zuvor an den Rechner,
    der zuerst fragte. Was hier steht, geht deshalb ueber HTTP.

    Zwei Endpunkte, zwei Haelften derselben Zusage: das Abholen darf nicht an
    den falschen Rechner gehen, und das Melden darf nicht vom falschen kommen.
    Ohne die zweite Haelfte holt der Laptop den Blick auf den Bildschirm, und
    der Buerorechner meldet sein eigenes Foto darauf.
    """

    def test_der_endpunkt_holt_nur_fuer_das_eigene_geraet(
        self, client: TestClient, db: Session, regular_user: User
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = _geraeteauftrag(db, regular_user, run, "call-1", "fam-laptop")
        db.commit()

        # Der Buerorechner fragt zuerst — und bekommt nichts.
        fremd = client.get(
            "/api/desktop/jobs/next",
            headers=_app_kopf(regular_user, "j-buero", familie="fam-buero"),
        )
        assert fremd.status_code == 204
        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "pending"

        # Und der Laptop bekommt ihn.
        eigen = client.get(
            "/api/desktop/jobs/next",
            headers=_app_kopf(regular_user, "j-laptop", familie="fam-laptop"),
        )
        assert eigen.status_code == 200
        assert eigen.json()["id"] == job.id

    def test_der_endpunkt_nimmt_kein_ergebnis_vom_fremden_geraet(
        self, client: TestClient, db: Session, regular_user: User
    ):
        """Der Buerorechner quittiert einen Auftrag, der dem Laptop gehoert.

        404 wie ueberall hier, und der Auftrag bleibt unberuehrt — sonst
        stuende ein Bildschirmfoto vom falschen Arbeitsplatz im Verlauf, und
        das Modell hielte es fuer das, wonach es gefragt hat.
        """
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = _geraeteauftrag(db, regular_user, run, "call-1", "fam-laptop")
        db.commit()

        antwort = client.post(
            f"/api/desktop/jobs/{job.id}/result",
            json={"ok": True, "ergebnis": {"bild_jpeg_base64": "fremdes-foto"}},
            headers=_app_kopf(regular_user, "j-buero", familie="fam-buero"),
        )

        assert antwort.status_code == 404
        db.expire_all()
        zeile = db.get(DesktopJob, job.id)
        assert zeile.status == "pending"
        assert zeile.result_encrypted is None

        # Und sein eigener Rechner kommt weiterhin durch.
        assert client.post(
            f"/api/desktop/jobs/{job.id}/result",
            json={"ok": True, "ergebnis": {"bild_jpeg_base64": "eigenes-foto"}},
            headers=_app_kopf(regular_user, "j-laptop", familie="fam-laptop"),
        ).status_code == 204

    def test_ein_auftrag_ohne_kennung_erreicht_am_endpunkt_jedes_geraet(
        self, client: TestClient, db: Session, regular_user: User
    ):
        """Der Bestand aus der Zeit vor der Spalte, ueber HTTP.

        Beide Haelften muessen ihn durchlassen: haetten wir hier geschnitten,
        waeren im Moment des Deploys alle wartenden Auftraege bis zu ihrer
        Frist haengengeblieben.
        """
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = _geraeteauftrag(db, regular_user, run, "call-alt", None)
        db.commit()

        kopf = _app_kopf(regular_user, "j-egal", familie="fam-egal")
        geholt = client.get("/api/desktop/jobs/next", headers=kopf)
        assert geholt.status_code == 200 and geholt.json()["id"] == job.id
        assert client.post(
            f"/api/desktop/jobs/{job.id}/result",
            json={"ok": True, "ergebnis": {}},
            headers=kopf,
        ).status_code == 204

    def test_ein_token_ohne_kennung_bekommt_am_endpunkt_weiterhin_alles(
        self, client: TestClient, db: Session, regular_user: User
    ):
        """Die andere Richtung: eine App, deren Access-Token aelter ist als
        der Anspruch. Es laeuft in Minuten ab; eine Sitzung, die bis dahin gar
        keine Arbeit mehr bekaeme, waere das schlechtere Ergebnis."""
        _rolle_mit(db, regular_user, "ai.desktop.use")
        run = _lauf(db, regular_user)
        job = _geraeteauftrag(db, regular_user, run, "call-1", "fam-laptop")
        db.commit()

        kopf = _app_kopf(regular_user, "j-alt")
        geholt = client.get("/api/desktop/jobs/next", headers=kopf)
        assert geholt.status_code == 200 and geholt.json()["id"] == job.id
        assert client.post(
            f"/api/desktop/jobs/{job.id}/result",
            json={"ok": True, "ergebnis": {}},
            headers=kopf,
        ).status_code == 204


class TestGeraetekennungImToken:
    """Die Kennung reist im Access-Token — und ueberlebt die Rotation.

    Sie muss aus derselben Ausstellung kommen wie die Identitaet, aus demselben
    Grund wie die Herkunft: alles, was der Client selbst erklaeren darf, kann
    er auch falsch erklaeren. Und sie muss dieselbe sein wie an der
    Refresh-Zeile, sonst zeigt die Geraeteliste einen anderen Wert als den, an
    dem ein Auftrag haengt.
    """

    def test_die_sitzung_traegt_ihre_familie_im_access_token(
        self, db: Session, regular_user: User
    ):
        from fastapi import Response

        from services.session_service import issue_session

        tokens = issue_session(Response(), db, regular_user, geraet="desktop")

        anspruch = AuthService.decode_token(tokens.access_token)["familie"]
        zeile = AuthService.validate_refresh_token(db, tokens.refresh_token)
        assert zeile is not None
        assert anspruch and anspruch == zeile.family

    def test_die_rotation_behaelt_dieselbe_familie(
        self, db: Session, regular_user: User
    ):
        """Sonst waere ein Geraet nach dem ersten Erneuern ein anderes, und
        seine wartenden Auftraege haetten niemanden mehr."""
        from fastapi import Response

        from services.session_service import issue_session

        erste = issue_session(Response(), db, regular_user, geraet="desktop")
        familie = AuthService.decode_token(erste.access_token)["familie"]

        zweite = issue_session(
            Response(), db, regular_user, family=familie, geraet="desktop"
        )

        assert AuthService.decode_token(zweite.access_token)["familie"] == familie

    def test_die_anfrage_liest_die_familie_aus_dem_bearer(self, regular_user: User):
        from dependencies import session_familie

        marke = AuthService.create_access_token({
            "sub": regular_user.username,
            "user_id": regular_user.id,
            "jti": "j-1",
            "geraet": "desktop",
            "familie": "fam-laptop",
        })

        assert session_familie(_anfrage_mit_bearer(marke)) == "fam-laptop"

    def test_ohne_anspruch_ist_die_familie_unbekannt(self, regular_user: User):
        """Und ``None`` heisst "unbekannt", nicht "irgendeins" — was daraus
        folgt, entscheidet `naechster`, nicht diese Funktion."""
        from dependencies import session_familie

        alt = AuthService.create_access_token({
            "sub": regular_user.username, "user_id": regular_user.id, "jti": "j-2",
        })

        assert session_familie(_anfrage_mit_bearer(alt)) is None
        assert session_familie(_anfrage_mit_bearer(None)) is None


class TestVertraulichkeit:
    def test_argumente_und_ergebnis_stehen_nicht_im_klartext(
        self, db: Session, regular_user: User
    ):
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={"pfad": "C:/Sandbox/passwoerter.txt"},
        )
        desktop_job_service.ergebnis_melden(
            db, job=job, ok=True, ergebnis={"inhalt": "hunter2"}
        )
        db.expire_all()

        zeile = db.get(DesktopJob, job.id)
        assert "passwoerter" not in zeile.payload_encrypted
        assert "hunter2" not in (zeile.result_encrypted or "")
        # Lesbar bleibt es trotzdem — sonst waere es kein Auftrag, sondern Datenmuell.
        assert desktop_job_service.argumente(zeile)["pfad"] == "C:/Sandbox/passwoerter.txt"


class TestFristen:
    def test_verfallener_auftrag_wird_zum_benannten_fehlschlag(
        self, db: Session, regular_user: User
    ):
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={},
        )
        job.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

        assert desktop_job_service.naechster(db, user_id=regular_user.id) is None
        db.expire_all()
        ergebnis = desktop_job_service.ergebnisse(db, [job.id])[0]
        assert ergebnis["status"] == "expired"
        assert ergebnis["error_code"] == "DESKTOP_JOB_EXPIRED"

    def test_ein_verpuffter_weckruf_wird_im_takt_nachgeholt(
        self, db: Session, regular_user: User
    ):
        """Der Rechner ist oft schneller als der Lauf.

        Zwischen dem Anlegen des Auftrags und dem Parken liegt das ganze
        `_finalize_stream`; die App fragt derweil im Sekundentakt. Meldet sie
        in dieser Spanne, steht der Lauf noch auf 'running', `darf_fortsetzen`
        weist den Weckruf ab, und niemand holt ihn nach — geweckt haette dann
        erst die 180-s-Frist des Auftrags. Der Betreiber wartete so bis zu
        vier Minuten auf Zahlen, die laengst dalagen.

        Nachgeholt wird es im Takt und nicht am Parken: dort laege zwischen
        Park-Commit und Aufgabenende ein zusaetzlicher `await`, und in genau
        dem Fenster faende ein Weckruf den Segmentplatz belegt.
        """
        from unittest.mock import patch

        from services import ai_run_service

        run = _lauf(db, regular_user)
        run.stop_reason = "desktop_jobs"
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_system",
            arguments={"aktion": "laufwerke"},
        )
        db.commit()
        # Der Rechner war schneller: das Ergebnis liegt schon da, der Weckruf
        # des Routers ist ins Leere gelaufen.
        desktop_job_service.ergebnis_melden(
            db, job=job, ok=True, ergebnis={"laufwerke": []},
        )

        geweckt: list[str] = []
        with patch.object(
            ai_run_service, "lauf_fortsetzen",
            lambda db_, *, run_id: geweckt.append(run_id) or True,
        ):
            desktop_job_service.verfallene_wecken(db)

        assert geweckt == [run.id]

    def test_ein_offener_auftrag_bleibt_liegen(
        self, db: Session, regular_user: User
    ):
        """Die Gegenrichtung: solange der Rechner arbeitet, wird nicht geweckt.

        Sonst saehe das Modell halbe Ergebnisse — dieselbe Regel wie bei den
        Vorschlaegen.
        """
        from unittest.mock import patch

        from services import ai_run_service

        run = _lauf(db, regular_user)
        run.stop_reason = "desktop_jobs"
        desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_system",
            arguments={"aktion": "laufwerke"},
        )
        db.commit()

        geweckt: list[str] = []
        with patch.object(
            ai_run_service, "lauf_fortsetzen",
            lambda db_, *, run_id: geweckt.append(run_id) or True,
        ):
            desktop_job_service.verfallene_wecken(db)

        assert geweckt == []

    def test_ein_fehlschlag_nennt_seinen_grund(
        self, db: Session, regular_user: User
    ):
        """Der Grund steht laengst da — gelesen wurde er nicht.

        Die App legt beim Scheitern `{"fehler": "..."}` ab. Die Bedingung
        verlangte aber `status == "done"`, also erfuhr das Modell nur *dass*
        etwas schiefging, nie *was*. Unter `grund` und nicht unter `ergebnis`:
        ein Fehlschlag darf nie aussehen wie ein Erfolg.
        """
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_system",
            arguments={"aktion": "laufwerke"},
        )
        db.commit()
        desktop_job_service.ergebnis_melden(
            db, job=job, ok=False,
            ergebnis={"fehler": "Laufwerke nicht abfragbar."},
            error_code="DESKTOP_TOOL_FAILED",
        )

        ergebnis = desktop_job_service.ergebnisse(db, [job.id])[0]

        assert ergebnis["status"] == "failed"
        assert ergebnis["error_code"] == "DESKTOP_TOOL_FAILED"
        assert ergebnis["grund"] == {"fehler": "Laufwerke nicht abfragbar."}
        # Nicht als Ergebnis: sonst laese ein Modell den Fehlertext als Auskunft.
        assert "ergebnis" not in ergebnis

    def test_haengender_auftrag_faellt_zurueck_in_die_schlange(
        self, db: Session, regular_user: User
    ):
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={},
        )
        db.commit()
        geholt = desktop_job_service.naechster(db, user_id=regular_user.id)
        assert geholt is not None

        # Die App ist zwischendurch abgestuerzt: das Abholen liegt zu lange zurueck.
        job.taken_at = datetime.now(timezone.utc) - timedelta(
            seconds=desktop_job_service.ABHOLFRIST_SEKUNDEN + 5
        )
        db.commit()

        erneut = desktop_job_service.naechster(db, user_id=regular_user.id)
        assert erneut is not None and erneut.id == job.id

    def test_ein_auftrag_der_auf_einen_menschen_wartet_faellt_nicht_zurueck(
        self, db: Session, regular_user: User
    ):
        """Wer liest, haengt nicht.

        Die Bestaetigungsfrist ist 600 s, die Abholfrist war 90 s — und galt
        auch fuer Auftraege, die auf eine Entscheidung warten. Der Auftrag fiel
        also mitten im Lesen zurueck in die Warteschlange und wurde ein zweites
        Mal ausgeliefert: die Karte mit den Loeschpfaden sprang alle
        90 Sekunden zurueck, und eine Bestaetigung im falschen Moment ging an
        einen Auftrag, den es so nicht mehr gab.
        """
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_aufraeumen",
            arguments={"zone": "frei", "autonom": False},
        )
        db.commit()
        assert desktop_job_service.naechster(db, user_id=regular_user.id) is not None

        # Der Mensch liest seit zwei Minuten die Liste. Fuer die Abholfrist
        # waere das laengst "abgestuerzt".
        job.taken_at = datetime.now(timezone.utc) - timedelta(
            seconds=desktop_job_service.ABHOLFRIST_SEKUNDEN + 30
        )
        db.commit()

        assert desktop_job_service.naechster(db, user_id=regular_user.id) is None
        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "taken"

    def test_der_takt_schliesst_verfallene_auch_ohne_laufende_app(
        self, db: Session, regular_user: User
    ):
        """Der Fall, in dem der Rechner **aus** ist — und genau der zaehlt.

        `_aufraeumen` laeuft nur, wenn jemand einen Auftrag abholt. Waere das
        der einzige Weg, bliebe ein Lauf ewig stehen, sobald der Rechner nicht
        mehr fragt. Der Takt (`scheduler_service`) ruft deshalb
        `verfallene_wecken`.
        """
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={},
        )
        job.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

        assert desktop_job_service.verfallene_wecken(db) == 1
        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "expired"
        # Zweimal aufrufen weckt nicht zweimal — der Auftrag ist geschlossen.
        assert desktop_job_service.verfallene_wecken(db) == 0

    def test_offene_zaehlt_nur_was_noch_unterwegs_ist(self, db: Session, regular_user: User):
        run = _lauf(db, regular_user)
        erster = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={},
        )
        desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-2",
            tool_name="desktop_launch_app",
            arguments={},
        )
        db.commit()
        assert desktop_job_service.offene(db, run_id=run.id) == 2

        desktop_job_service.ergebnis_melden(db, job=erster, ok=True, ergebnis={})
        assert desktop_job_service.offene(db, run_id=run.id) == 1


class TestAufbewahrung:
    """Ein verbrauchtes Ergebnis ist Bestand, kein Besitz.

    Nichts im Backend hat `desktop_jobs` je geloescht — die Zustaende wurden
    nur weitergedreht. Damit sammelten sich vollstaendige Bildschirmfotos des
    Arbeitsplatzes (bis zu einer Million Zeichen) und gelesene Dateiinhalte
    dauerhaft in der Panel-Datenbank. Verschluesselt zwar, aber ein
    Datenbank-Backup traegt beides mit.
    """

    def _fertiger_auftrag(self, db: Session, user: User, vor_stunden: float) -> str:
        run = _lauf(db, user)
        job = desktop_job_service.anlegen(
            db,
            user_id=user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_bildschirm",
            arguments={},
        )
        desktop_job_service.ergebnis_melden(
            db, job=job, ok=True, ergebnis={"bild_jpeg_base64": "AAAA"}
        )
        job.finished_at = datetime.now(timezone.utc) - timedelta(hours=vor_stunden)
        db.commit()
        return job.id

    def test_ein_alter_auftrag_verschwindet_ganz(self, db: Session, regular_user: User):
        job_id = self._fertiger_auftrag(
            db, regular_user, desktop_job_service.AUFBEWAHRUNG_STUNDEN + 1
        )

        desktop_job_service.verfallene_wecken(db)

        db.expire_all()
        # Die ganze Zeile: auch `payload_encrypted` traegt Pfade vom Rechner.
        assert db.get(DesktopJob, job_id) is None

    def test_ein_frisches_ergebnis_bleibt_liegen(self, db: Session, regular_user: User):
        """Die Gegenrichtung — sonst waere die Regel ein Datenverlust.

        Der Lauf liest das Ergebnis genau einmal, und zwischen dem Melden und
        dem Wecken liegt ein ganzer Takt.
        """
        job_id = self._fertiger_auftrag(db, regular_user, 1)

        desktop_job_service.verfallene_wecken(db)

        db.expire_all()
        assert db.get(DesktopJob, job_id) is not None
        assert desktop_job_service.ergebnisse(db, [job_id])[0]["status"] == "done"


class TestSchema:
    def test_auftrag_stirbt_mit_seinem_lauf(self, db: Session, regular_user: User):
        """Ohne den Lauf koennte niemand das Ergebnis mehr entgegennehmen.

        Modell und Migration muessen dasselbe ON DELETE tragen — ohne diesen
        Test faellt ein Auseinanderlaufen erst im Betrieb auf.
        """
        run = _lauf(db, regular_user)
        job = desktop_job_service.anlegen(
            db,
            user_id=regular_user.id,
            run_id=run.id,
            tool_call_id="call-1",
            tool_name="desktop_dateien",
            arguments={},
        )
        job_id = job.id
        db.commit()

        db.delete(run)
        db.commit()
        db.expire_all()
        assert db.get(DesktopJob, job_id) is None
