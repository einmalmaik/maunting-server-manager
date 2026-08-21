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
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiRun, DesktopJob, Role, RolePermission, User
from services import desktop_job_service
from services.role_service import set_user_roles


def _rolle_mit(db: Session, user: User, *rechte: str) -> None:
    role = Role(name=f"desktop-{'-'.join(rechte) or 'leer'}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for recht in rechte:
        db.add(RolePermission(role_id=role.id, permission_key=recht))
    db.commit()
    set_user_roles(db, user, [role.id])


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
            arguments={"aktion": "lesen", "pfad": "notiz.txt"},
        )
        db.commit()

        antwort = client.get("/api/desktop/jobs/next", cookies=user_cookies)
        assert antwort.status_code == 200
        daten = antwort.json()
        assert daten["id"] == job.id
        assert daten["tool_name"] == "desktop_dateien"
        assert daten["arguments"] == {"aktion": "lesen", "pfad": "notiz.txt"}

        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "taken"

        # Zweimal abholen liefert denselben Auftrag nicht doppelt.
        assert client.get("/api/desktop/jobs/next", cookies=user_cookies).status_code == 204

    def test_ohne_auftrag_kommt_204(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _rolle_mit(db, regular_user, "ai.desktop.use")
        assert client.get("/api/desktop/jobs/next", cookies=user_cookies).status_code == 204

    def test_ohne_recht_kein_zugriff(
        self, client: TestClient, db: Session, regular_user: User, user_cookies: dict
    ):
        _rolle_mit(db, regular_user, "ai.chat.use")
        assert client.get("/api/desktop/jobs/next", cookies=user_cookies).status_code == 403

    def test_fremder_auftrag_wird_nicht_ausgeliefert(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        owner_user: User,
        user_cookies: dict,
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

        assert client.get("/api/desktop/jobs/next", cookies=user_cookies).status_code == 204


class TestMelden:
    def test_ergebnis_schliesst_den_auftrag(
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
            arguments={"pfad": "notiz.txt"},
        )
        db.commit()

        antwort = client.post(
            f"/api/desktop/jobs/{job.id}/result",
            json={"ok": True, "ergebnis": {"inhalt": "Hallo"}},
            headers={"X-CSRF-Token": user_csrf_token},
            cookies=user_cookies,
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
        user_cookies: dict,
        user_csrf_token: str,
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
            headers={"X-CSRF-Token": user_csrf_token},
            cookies=user_cookies,
        )
        assert antwort.status_code == 404
        db.expire_all()
        assert db.get(DesktopJob, job.id).status == "pending"

    def test_zweite_meldung_ueberschreibt_die_erste_nicht(
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

        for inhalt, erwartet in (("erste", 204), ("zweite", 204)):
            assert client.post(
                f"/api/desktop/jobs/{job.id}/result",
                json={"ok": True, "ergebnis": {"inhalt": inhalt}},
                headers={"X-CSRF-Token": user_csrf_token},
                cookies=user_cookies,
            ).status_code == erwartet

        db.expire_all()
        ergebnis = desktop_job_service.ergebnisse(db, [job.id])[0]
        assert ergebnis["ergebnis"] == {"inhalt": "erste"}


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
