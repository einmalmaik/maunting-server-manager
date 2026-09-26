from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, UserCalendar, CalendarEvent
from services.calendar_service import CalendarService


def test_caldav_read_adds_time_range_and_reuses_short_cache(monkeypatch, db_session, test_user):
    sent: list[str] = []
    calendar = SimpleNamespace(
        id=77, provider_type="caldav", caldav_url="https://calendar.invalid/dav/",
        caldav_username="user", name="Test", get_credentials=lambda: "synthetic-secret",
    )

    class FakeClient:
        def request(self, _method, _url, **kwargs):
            sent.append(kwargs["content"])
            return SimpleNamespace(status_code=207, text="")

    monkeypatch.setattr(CalendarService, "get_calendar", lambda *_args, **_kwargs: calendar)
    monkeypatch.setattr("services.calendar_service._caldav_http_client", lambda: FakeClient())

    assert CalendarService.get_events(db_session, test_user, start_date="2026-08-01", end_date="2026-08-31") == []
    assert CalendarService.get_events(db_session, test_user, start_date="2026-08-01", end_date="2026-08-31") == []

    assert len(sent) == 1
    assert '<C:time-range start="20260801T000000Z" end="20260831T000000Z" />' in sent[0]


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def test_user(db_session):
    user = User(
        username="testowner",
        email="owner@example.com",
        password_hash="fakehash",
        is_owner=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_native_calendar_auto_creation(db_session, test_user):
    cal = CalendarService.get_calendar(db_session, test_user)
    assert cal is not None
    assert cal.provider_type == "native"
    assert cal.name == "Persönlicher Kalender"
    assert cal.is_default is True


def test_native_calendar_crud_and_export(db_session, test_user):
    # 1. Create Event
    ev_data = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Team Standup",
        start_time="2026-08-26 10:00",
        end_time="2026-08-26 10:30",
        description="Daily sync",
        location="Meeting Room 1",
        color="emerald",
    )
    assert ev_data["status"] == "created"
    assert ev_data["title"] == "Team Standup"
    event_id = ev_data["event_id"]

    # 2. Get Events
    events = CalendarService.get_events(
        db=db_session,
        user=test_user,
        start_date="2026-08-26 00:00",
        end_date="2026-08-26 23:59",
    )
    assert len(events) == 1
    assert events[0]["title"] == "Team Standup"
    assert events[0]["location"] == "Meeting Room 1"
    assert events[0]["color"] == "emerald"

    # 3. Update Event (Reschedule & change title)
    updated = CalendarService.update_event(
        db=db_session,
        user=test_user,
        event_id=event_id,
        title="Weekly Team Standup",
        start_time="2026-08-26 14:00",
        end_time="2026-08-26 15:00",
        location="Online / Zoom",
    )
    assert updated["status"] == "updated"
    assert updated["title"] == "Weekly Team Standup"
    assert updated["location"] == "Online / Zoom"
    assert "14:00" in updated["start"]

    # 4. iCal Export
    ics_text = CalendarService.export_ical(db_session, test_user)
    assert "BEGIN:VCALENDAR" in ics_text
    assert "SUMMARY:Weekly Team Standup" in ics_text
    assert "LOCATION:Online / Zoom" in ics_text
    assert "END:VCALENDAR" in ics_text

    # 5. Delete Event
    del_res = CalendarService.delete_event(
        db=db_session,
        user=test_user,
        event_id=event_id,
    )
    assert del_res["status"] == "deleted"

    events_after = CalendarService.get_events(db_session, test_user)
    assert len(events_after) == 0


def test_calendar_events_stored_encrypted_in_database(db_session, test_user):
    """Beweist, dass in der calendar_events Tabelle absolut KEIN Klartext fuer title, description, location existiert."""
    from sqlalchemy import text

    secret_title = "Geheimer_Vorstandstermin_999"
    secret_desc = "Streng_geheime_Finanzthemen_XYZ"
    secret_loc = "Geheimer_Bunker_Raum_42"

    ev = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title=secret_title,
        description=secret_desc,
        location=secret_loc,
        start_time="2026-08-26 10:00",
        end_time="2026-08-26 11:00",
    )

    # Direkte RAW SQL Abfrage der DB-Tabelle
    row = db_session.execute(
        text("SELECT title, description, location FROM calendar_events WHERE event_uid = :uid"),
        {"uid": ev["event_id"]},
    ).fetchone()

    raw_title, raw_desc, raw_loc = row[0], row[1], row[2]

    # Der Klartext darf NIEMALS in der Datenbank stehen!
    assert secret_title not in raw_title
    assert secret_desc not in raw_desc
    assert secret_loc not in raw_loc
    # Es muessen Base64-DIS-Ciphertexte sein
    assert len(raw_title) > 20
    assert len(raw_desc) > 20
    assert len(raw_loc) > 20

    # Aber fuer den berechtigten Nutzer wird es sauber entschluesselt
    events = CalendarService.get_events(db_session, test_user)
    assert len(events) == 1
    assert events[0]["title"] == secret_title
    assert events[0]["description"] == secret_desc
    assert events[0]["location"] == secret_loc


def test_calendar_events_automatic_migration_of_legacy_plaintext(db_session, test_user):
    """Beweist, dass unverschluesselte Altdaten beim ersten Aufruf automatisch in der DB verschluesselt werden."""
    from sqlalchemy import text
    import uuid

    cal = CalendarService.get_calendar(db_session, test_user)
    legacy_uid = str(uuid.uuid4())
    legacy_title = "Alter_Unverschluesselter_Termin"
    legacy_desc = "Alte_Terminbeschreibung_Klartext"
    legacy_loc = "Alter_Ort_Klartext"

    # Altdaten direkt unverschluesselt in die DB einschleusen
    db_session.execute(
        text(
            "INSERT INTO calendar_events (calendar_id, user_id, event_uid, title, description, location, start_time, end_time, all_day, color, event_type, created_at, updated_at) "
            "VALUES (:cal_id, :uid, :euid, :title, :desc, :loc, datetime('now'), datetime('now', '+1 hour'), 0, 'primary', 'personal', datetime('now'), datetime('now'))"
        ),
        {
            "cal_id": cal.id,
            "uid": test_user.id,
            "euid": legacy_uid,
            "title": legacy_title,
            "desc": legacy_desc,
            "loc": legacy_loc,
        },
    )
    db_session.commit()

    # Vor dem Aufruf: In der DB steht Klartext
    before_row = db_session.execute(
        text("SELECT title, description, location FROM calendar_events WHERE event_uid = :uid"),
        {"uid": legacy_uid},
    ).fetchone()
    assert before_row[0] == legacy_title
    assert before_row[1] == legacy_desc
    assert before_row[2] == legacy_loc

    # Nutzer ruft get_events() auf
    events = CalendarService.get_events(db_session, test_user)
    migrated_ev = next(e for e in events if e["event_id"] == legacy_uid)
    assert migrated_ev["title"] == legacy_title
    assert migrated_ev["description"] == legacy_desc
    assert migrated_ev["location"] == legacy_loc

    # Nach dem Aufruf: In der Datenbank MUSS jetzt Ciphertext stehen!
    after_row = db_session.execute(
        text("SELECT title, description, location FROM calendar_events WHERE event_uid = :uid"),
        {"uid": legacy_uid},
    ).fetchone()
    assert after_row[0] != legacy_title
    assert legacy_title not in after_row[0]
    assert after_row[1] != legacy_desc
    assert legacy_desc not in after_row[1]
    assert after_row[2] != legacy_loc
    assert legacy_loc not in after_row[2]



@pytest.mark.asyncio
async def test_calendar_reminders_and_test_dispatch(db_session, test_user):
    from datetime import datetime, timedelta, timezone

    test_user.email_notifications = True
    test_user.device_notifications = True
    db_session.commit()

    # 1. Test-Reminder
    res = await CalendarService.send_test_reminder(db_session, test_user)
    assert res["status"] == "success"
    assert "Test-Termin" in res["title"]
    assert res["time_hint"] == "in 1 Tag"

    # 2. Due Reminders check (Event in 24 hours)
    tomorrow = datetime.now(timezone.utc) + timedelta(hours=24)
    start_str = tomorrow.strftime("%Y-%m-%d %H:%M")
    end_str = (tomorrow + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")

    CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Dringendes Review",
        start_time=start_str,
        end_time=end_str,
    )

    sent = await CalendarService.check_and_send_due_reminders(db_session)
    assert sent >= 1

    # Dedup: 2. Aufruf darf nichts doppelt senden
    sent_again = await CalendarService.check_and_send_due_reminders(db_session)
    assert sent_again == 0


def test_calendar_timezone_handling(db_session, test_user):
    # 1. Benutzer in Europe/Berlin (Sommerzeit CEST = UTC+2)
    test_user.time_zone = "Europe/Berlin"
    db_session.commit()

    ev = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Sport am Mittag",
        start_time="2026-08-27 12:00",
        end_time="2026-08-27 13:00",
    )
    assert ev["status"] == "created"
    # 12:00 Berlin (UTC+2) muss zu 10:00 UTC konvertiert werden
    assert ev["start"] == "2026-08-27T10:00:00Z"
    assert ev["end"] == "2026-08-27T11:00:00Z"

    # 2. Benutzer in America/New_York (Sommerzeit EDT = UTC-4)
    test_user.time_zone = "America/New_York"
    db_session.commit()

    ev_ny = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="NY Meeting",
        start_time="2026-08-27 12:00",
        end_time="2026-08-27 13:00",
    )
    assert ev_ny["status"] == "created"
    # 12:00 New York (UTC-4) muss zu 16:00 UTC konvertiert werden
    assert ev_ny["start"] == "2026-08-27T16:00:00Z"
    assert ev_ny["end"] == "2026-08-27T17:00:00Z"


def test_calendar_categories_and_visibility(db_session, test_user):
    from models import Team, TeamMember, Server

    # 1. Zweiter Benutzer (normales Teammitglied)
    member_user = User(
        username="teammember",
        email="member@example.com",
        password_hash="fakehash",
        is_owner=False,
    )
    db_session.add(member_user)

    # Dritter Benutzer (Fremder, kein Teammitglied)
    stranger_user = User(
        username="stranger",
        email="stranger@example.com",
        password_hash="fakehash",
        is_owner=False,
    )
    db_session.add(stranger_user)

    # Team anlegen
    team = Team(name="DevOps Core", owner_user_id=test_user.id)
    db_session.add(team)
    db_session.flush()

    # Team-Mitgliedschaft hinzufügen
    tm = TeamMember(team_id=team.id, user_id=member_user.id, role="member")
    db_session.add(tm)

    # Server anlegen
    server = Server(
        name="Production-Node-1",
        game_type="custom",
        install_dir="/data/srv1",
        status="running",
    )
    db_session.add(server)
    db_session.commit()

    # A) Persönlicher Termin von test_user
    ev_personal = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Privater Zahnarzt",
        start_time="2026-08-27 09:00",
        end_time="2026-08-27 10:00",
        event_type="personal",
    )
    assert ev_personal["event_type"] == "personal"
    assert ev_personal["color"] == "blue"

    # B) Team-Termin von test_user
    ev_team = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Sprint Planning",
        start_time="2026-08-27 11:00",
        end_time="2026-08-27 12:00",
        event_type="team",
        team_id=team.id,
    )
    assert ev_team["event_type"] == "team"
    assert ev_team["color"] == "green"
    assert ev_team["team_name"] == "DevOps Core"

    # C) Server-Wartung von test_user
    ev_server = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Kernel Patching",
        start_time="2026-08-27 14:00",
        end_time="2026-08-27 15:00",
        event_type="server",
        server_id=server.id,
    )
    assert ev_server["event_type"] == "server"
    assert ev_server["color"] == "purple"
    assert ev_server["server_name"] == "Production-Node-1"

    # D) Node-Termin
    ev_node = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Node Cluster Upgrade",
        start_time="2026-08-27 16:00",
        end_time="2026-08-27 17:00",
        event_type="node",
    )
    assert ev_node["event_type"] == "node"
    assert ev_node["color"] == "amber"

    # PRÜFUNG: Sichtbarkeit für test_user (Owner)
    owner_events = CalendarService.get_events(db_session, test_user)
    assert len(owner_events) == 4

    # PRÜFUNG: Filter nach Kategorie für test_user
    personal_only = CalendarService.get_events(db_session, test_user, event_type="personal")
    assert len(personal_only) == 1
    assert personal_only[0]["title"] == "Privater Zahnarzt"

    team_only = CalendarService.get_events(db_session, test_user, event_type="team")
    assert len(team_only) == 1
    assert team_only[0]["title"] == "Sprint Planning"

    server_only = CalendarService.get_events(db_session, test_user, event_type="server")
    assert len(server_only) == 1
    assert server_only[0]["title"] == "Kernel Patching"

    node_only = CalendarService.get_events(db_session, test_user, event_type="node")
    assert len(node_only) == 1
    assert node_only[0]["title"] == "Node Cluster Upgrade"

    # PRÜFUNG: Sichtbarkeit für member_user
    # member_user sieht:
    # - KEINE privaten Termine von test_user
    # - Den Team-Termin "Sprint Planning"
    # - (Keine Server-Termine, falls keine ServerPermission besteht)
    member_events = CalendarService.get_events(db_session, member_user)
    member_titles = [e["title"] for e in member_events]
    assert "Sprint Planning" in member_titles
    assert "Privater Zahnarzt" not in member_titles
    assert "Kernel Patching" not in member_titles

    # PRÜFUNG: Sichtbarkeit für stranger_user (kein Teammitglied)
    stranger_events = CalendarService.get_events(db_session, stranger_user)
    assert len(stranger_events) == 0

    # PRÜFUNG: Fremde Termine bearbeiten schlägt fehl
    with pytest.raises(Exception):
        CalendarService.update_event(
            db=db_session,
            user=stranger_user,
            event_id=ev_team["event_id"],
            title="Gehacktes Meeting",
        )

    # PRÜFUNG: Kategorie-Wechsel von team auf personal bereinigt team_id
    upd_res = CalendarService.update_event(
        db=db_session,
        user=test_user,
        event_id=ev_team["event_id"],
        event_type="personal",
    )
    assert upd_res["event_type"] == "personal"
    assert upd_res["team_name"] is None

    # PRÜFUNG: Zuweisung eines nicht existierenden / unberechtigten Teams durch Nicht-Owner schlägt fehl
    member_pers_ev = CalendarService.create_event(
        db=db_session,
        user=member_user,
        title="Member Task",
        start_time="2026-08-27 18:00",
        end_time="2026-08-27 19:00",
    )
    with pytest.raises(ValueError, match="kein Mitglied"):
        CalendarService.update_event(
            db=db_session,
            user=member_user,
            event_id=member_pers_ev["event_id"],
            team_id=99999,
        )


def test_calendar_client_e2ee_opaque_storage(db_session, test_user):
    """Beweist, dass client-seitig verschluesselte Daten (sv-cal-v1:) vom Server nicht angefasst werden."""
    from sqlalchemy import text

    client_cipher_title = "sv-cal-v1:abcdef1234567890base64title"
    client_cipher_desc = "sv-cal-v1:fedcba0987654321base64desc"
    client_cipher_loc = "sv-cal-v1:1122334455667788base64loc"

    res = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title=client_cipher_title,
        start_time="2026-08-27 10:00",
        end_time="2026-08-27 11:00",
        description=client_cipher_desc,
        location=client_cipher_loc,
    )

    # In der DB muss exakt der Client-Ciphertext stehen
    row = db_session.execute(
        text("SELECT title, description, location FROM calendar_events WHERE event_uid = :uid"),
        {"uid": res["event_id"]},
    ).fetchone()
    assert row[0] == client_cipher_title
    assert row[1] == client_cipher_desc
    assert row[2] == client_cipher_loc

    # Beim Abruf erhaelt der Client den Ciphertext unveraendert zur client-seitigen Entschluesselung
    events = CalendarService.get_events(db_session, test_user)
    ev = next(e for e in events if e["event_id"] == res["event_id"])
    assert ev["title"] == client_cipher_title
    assert ev["description"] == client_cipher_desc
    assert ev["location"] == client_cipher_loc

    # Update mit neuem Client-Ciphertext
    new_cipher_title = "sv-cal-v1:new9876543210title"
    upd = CalendarService.update_event(
        db=db_session,
        user=test_user,
        event_id=res["event_id"],
        title=new_cipher_title,
    )
    assert upd["title"] == new_cipher_title

    upd_row = db_session.execute(
        text("SELECT title FROM calendar_events WHERE event_uid = :uid"),
        {"uid": res["event_id"]},
    ).fetchone()
    assert upd_row[0] == new_cipher_title

    client_event_uid = "client-uuid-98765-cal-event"
    ev_with_uid = CalendarService.create_event(
        db=db_session,
        user=test_user,
        event_uid=client_event_uid,
        title=client_cipher_title,
        start_time="2026-08-27 14:00",
        end_time="2026-08-27 15:00",
        description=client_cipher_desc,
        location=client_cipher_loc,
    )
    assert ev_with_uid["event_id"] == client_event_uid

    # Idempotenter Replay mit gleicher event_uid
    replay_ev = CalendarService.create_event(
        db=db_session,
        user=test_user,
        event_uid=client_event_uid,
        title=client_cipher_title,
        start_time="2026-08-27 14:00",
        end_time="2026-08-27 15:00",
        description=client_cipher_desc,
        location=client_cipher_loc,
    )
    assert replay_ev["event_id"] == client_event_uid
    assert replay_ev["id"] == ev_with_uid["id"]


# ── Serientermine ─────────────────────────────────────────────────────────


def _serie(rrule, **rest) -> str:
    """Klartext-Wiederholungsdokument, wie es ueber die REST-Schnittstelle kommt."""
    import json

    return json.dumps({"rrule": rrule, **rest})


def test_wiederholung_steht_nicht_im_klartext_in_der_datenbank(db_session, test_user):
    """Der Betreiberentscheid vom 22.09.2026, als Nachweis.

    Wer in die Datenbank sieht, soll nicht erkennen koennen, was fuer
    Serientermine jemand hat — weder den Titel noch den Takt.
    """
    from sqlalchemy import text

    ev = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Geburtstag_Lisa_4711",
        start_time="2026-03-14 00:00",
        end_time="2026-03-15 00:00",
        all_day=True,
        recurrence=_serie("FREQ=YEARLY", ausnahmen=["2027-03-14"]),
    )

    roh = db_session.execute(
        text("SELECT title, recurrence FROM calendar_events WHERE event_uid = :uid"),
        {"uid": ev["event_id"]},
    ).fetchone()
    roh_title, roh_recurrence = roh[0], roh[1]

    assert "Geburtstag_Lisa_4711" not in roh_title
    # Weder der Takt ...
    for verraeterisch in ("FREQ", "YEARLY", "RRULE", "rrule", "jaehrlich"):
        assert verraeterisch not in roh_recurrence
    # ... noch die Ausnahme, die sonst das Datum preisgaebe.
    assert "2027" not in roh_recurrence
    assert "ausnahmen" not in roh_recurrence
    assert len(roh_recurrence) > 20

    # Fuer den berechtigten Benutzer kommt alles sauber zurueck.
    events = CalendarService.get_events(db_session, test_user)
    assert len(events) == 1
    assert events[0]["title"] == "Geburtstag_Lisa_4711"
    assert "FREQ=YEARLY" in events[0]["recurrence"]


def test_einzeltermin_und_serie_sind_in_der_datenbank_nicht_zu_unterscheiden(db_session, test_user):
    """Der eigentliche Kern: nicht nur der Inhalt, auch die Tatsache ist verborgen.

    Waere das Feld bei Einzelterminen leer, verriete schon `recurrence = ''`
    die Antwort auf "wer hat hier Serientermine" — ohne einen einzigen
    Entschluesselungsvorgang.
    """
    from sqlalchemy import text

    einzeln = CalendarService.create_event(
        db=db_session, user=test_user,
        title="Zahnarzt", start_time="2026-03-14 10:00", end_time="2026-03-14 11:00",
    )
    serie = CalendarService.create_event(
        db=db_session, user=test_user,
        title="Geburtstag", start_time="2026-03-14 00:00", end_time="2026-03-15 00:00",
        all_day=True, recurrence=_serie("FREQ=YEARLY"),
    )

    zeilen = db_session.execute(
        text("SELECT event_uid, recurrence FROM calendar_events WHERE event_uid IN (:a, :b)"),
        {"a": einzeln["event_id"], "b": serie["event_id"]},
    ).fetchall()
    assert len(zeilen) == 2

    for _uid, recurrence in zeilen:
        assert recurrence, "jede Zeile traegt ein Dokument, auch die ohne Serie"
        assert recurrence.strip() != ""
        assert "rrule" not in recurrence
        assert len(recurrence) > 20


def test_altbestand_bekommt_sein_wiederholungsfeld_nachgetragen(db_session, test_user):
    """Zeilen aus der Zeit vor der Migration duerfen nicht leer bleiben."""
    from sqlalchemy import text
    import uuid

    uid = str(uuid.uuid4())
    kalender = CalendarService.get_or_create_native_calendar(db_session, test_user)
    db_session.execute(
        text(
            "INSERT INTO calendar_events "
            "(calendar_id, user_id, event_uid, title, start_time, end_time, all_day, "
            " event_type, color, recurrence, created_at, updated_at) "
            "VALUES (:cid, :uid_user, :uid, :title, :start, :end, 0, 'personal', 'blue', '', "
            " :now, :now)"
        ),
        {
            "cid": kalender.id,
            "uid_user": test_user.id,
            "uid": uid,
            "title": "Alter Termin",
            "start": "2026-08-26 10:00:00",
            "end": "2026-08-26 11:00:00",
            "now": "2026-08-01 00:00:00",
        },
    )
    db_session.commit()

    events = CalendarService.get_events(db_session, test_user)
    assert len(events) == 1
    assert events[0]["recurrence"]

    nachher = db_session.execute(
        text("SELECT recurrence FROM calendar_events WHERE event_uid = :uid"), {"uid": uid}
    ).fetchone()[0]
    assert nachher != "", "das Feld darf nicht leer bleiben"
    assert len(nachher) > 20, "und es muss verschluesselt sein, nicht roh"


def test_vorkommen_im_fenster_breitet_den_geburtstag_von_1995_aus(db_session, test_user):
    """Der Fall, der die Funktion ueberhaupt noetig macht."""
    CalendarService.create_event(
        db=db_session, user=test_user,
        title="Geburtstag", start_time="1995-03-14 00:00", end_time="1995-03-15 00:00",
        all_day=True, recurrence=_serie("FREQ=YEARLY"),
    )

    # Der Serienkopf selbst faellt aus einer Abfrage fuer 2026 heraus ...
    koepfe = CalendarService.get_events(
        db_session, test_user, start_date="2026-03-01", end_date="2026-04-01"
    )
    assert koepfe == []

    # ... das Vorkommen aber nicht.
    vorkommen = CalendarService.vorkommen_im_fenster(
        db_session, test_user,
        von=datetime(2026, 3, 1, tzinfo=timezone.utc),
        bis=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    assert len(vorkommen) == 1
    assert vorkommen[0]["vorkommen"] == "2026-03-14"
    assert vorkommen[0]["ist_serie"] is True
    assert vorkommen[0]["title"] == "Geburtstag"


def test_e2ee_serie_bleibt_fuer_den_server_ein_einzeltermin(db_session, test_user):
    """Was der Server nicht lesen kann, breitet er auch nicht aus — und erfindet nichts."""
    CalendarService.create_event(
        db=db_session, user=test_user,
        title="sv-cal-v1:AAAAAAAAAAAAAAAAAAAA",
        start_time="2026-03-14 10:00", end_time="2026-03-14 11:00",
        recurrence="sv-cal-v1:BBBBBBBBBBBBBBBBBBBB",
    )

    vorkommen = CalendarService.vorkommen_im_fenster(
        db_session, test_user,
        von=datetime(2026, 1, 1, tzinfo=timezone.utc),
        bis=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    assert len(vorkommen) == 1, "keine erfundenen Wiederholungen"
    assert vorkommen[0]["ist_serie"] is False


def test_serie_bleibt_wenn_nur_der_titel_geaendert_wird(db_session, test_user):
    """recurrence=None heisst 'nicht anfassen', nicht 'aufloesen'."""
    ev = CalendarService.create_event(
        db=db_session, user=test_user,
        title="Geburtstag", start_time="2026-03-14 00:00", end_time="2026-03-15 00:00",
        all_day=True, recurrence=_serie("FREQ=YEARLY"),
    )
    geaendert = CalendarService.update_event(
        db=db_session, user=test_user, event_id=ev["event_id"], title="Geburtstag Lisa"
    )
    assert geaendert["title"] == "Geburtstag Lisa"
    assert "FREQ=YEARLY" in geaendert["recurrence"]


def test_serie_laesst_sich_ausdruecklich_aufloesen(db_session, test_user):
    ev = CalendarService.create_event(
        db=db_session, user=test_user,
        title="Geburtstag", start_time="2026-03-14 00:00", end_time="2026-03-15 00:00",
        all_day=True, recurrence=_serie("FREQ=YEARLY"),
    )
    geaendert = CalendarService.update_event(
        db=db_session, user=test_user, event_id=ev["event_id"],
        recurrence=_serie(None),
    )
    assert "FREQ" not in geaendert["recurrence"]


def test_ical_export_schreibt_die_regel_statt_aller_vorkommen(db_session, test_user):
    """Das abonnierende Programm rechnet selbst.

    Ein Geburtstag ohne Ende braucht so keine kuenstliche Grenze, und der Feed
    bleibt kurz.
    """
    CalendarService.create_event(
        db=db_session, user=test_user,
        title="Geburtstag Lisa", start_time="2026-03-14 00:00", end_time="2026-03-15 00:00",
        all_day=True, recurrence=_serie("FREQ=YEARLY", ausnahmen=["2027-03-14"]),
    )
    ics = CalendarService.export_ical(db_session, test_user)
    assert "RRULE:FREQ=YEARLY" in ics
    assert "EXDATE;VALUE=DATE:20270314" in ics
    assert ics.count("BEGIN:VEVENT") == 1, "ein VEVENT mit Regel, keine Liste von Kopien"


def test_ical_export_ganztag_behaelt_sein_datum(db_session, test_user):
    """Der Export schrieb das UTC-Datum eines ganzen Tages.

    Berliner Mitternacht ist 23:00 UTC des Vortags, und `_parse_datetime`
    liefert immer UTC. Aus dem Geburtstag am 14.03. wurde im abonnierten
    Google-Kalender `DTSTART;VALUE=DATE:20260313` — ein Tag zu frueh, und mit
    einer jaehrlichen Regel jedes Jahr aufs Neue.

    Das Gegenstueck zu `test_caldav_ganztag_behaelt_sein_datum_oestlich_von_greenwich`:
    dort kommt der Tag herein, hier geht er hinaus.
    """
    for zone in ("Europe/Berlin", "Asia/Tokyo", "Pacific/Kiritimati", "America/Los_Angeles"):
        test_user.time_zone = zone
        db_session.commit()
        ev = CalendarService.create_event(
            db=db_session, user=test_user,
            title="Geburtstag", start_time="2026-03-14 00:00", end_time="2026-03-15 00:00",
            all_day=True, recurrence=_serie("FREQ=YEARLY"),
        )
        ics = CalendarService.export_ical(db_session, test_user)
        assert "DTSTART;VALUE=DATE:20260314" in ics, f"{zone} verschiebt den Tag"
        assert "DTEND;VALUE=DATE:20260315" in ics, f"{zone} verschiebt das Ende"
        CalendarService.delete_event(db=db_session, user=test_user, event_id=ev["event_id"])


def test_ical_export_ausnahme_trifft_das_vorkommen(db_session, test_user):
    """Ein abgesagtes Vorkommen muss der Abonnent auch finden koennen.

    Der Ausnahmetag ist ein lokales Datum, die Uhrzeit stand in UTC. Beides
    aneinandergeklebt ergibt ueber die Sommerzeit einen Zeitpunkt, den es in
    der Serie nicht gibt — der abgesagte Termin bliebe im fremden Kalender
    stehen. Der Termin hier liegt im Winter (09:00 Berlin = 08:00Z), die
    Ausnahme im Sommer (09:00 Berlin = 07:00Z).
    """
    test_user.time_zone = "Europe/Berlin"
    db_session.commit()
    CalendarService.create_event(
        db=db_session, user=test_user,
        title="Wochentermin", start_time="2026-01-05 09:00", end_time="2026-01-05 10:00",
        recurrence=_serie("FREQ=WEEKLY", ausnahmen=["2026-07-06"]),
    )
    ics = CalendarService.export_ical(db_session, test_user)
    assert "DTSTART:20260105T080000Z" in ics, "Start im Winter: 09:00 Berlin ist 08:00Z"
    assert "EXDATE:20260706T070000Z" in ics, (
        "Ausnahme im Sommer: 09:00 Berlin ist 07:00Z. "
        f"Gefunden: {[z for z in ics.split(chr(13) + chr(10)) if z.startswith('EXDATE')]}"
    )


# ── Erinnerungen ──────────────────────────────────────────────────────────


def test_erinnerung_traegt_das_vorkommen_im_schluessel(db_session, test_user):
    """Ohne das Vorkommen meldet sich eine Serie genau einmal.

    Der Dedup-Schluessel lautete bis zum 22.09.2026
    `{user}_{event}_{48h|24h}` und kannte das Vorkommen nicht: der Geburtstag
    2027 galt als erledigt, weil 2026 erinnert worden war.
    """
    from datetime import timedelta

    test_user.device_notifications = True
    db_session.commit()

    # +24h und +48h: beide innerhalb der 49 Stunden, die `get_due_reminders`
    # ueberhaupt betrachtet, und in zwei verschiedenen Stufen (24h und 48h).
    morgen = datetime.now(timezone.utc) + timedelta(hours=24)
    CalendarService.create_event(
        db=db_session, user=test_user,
        title="Taeglicher Termin",
        start_time=morgen.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end_time=(morgen + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        recurrence=_serie("FREQ=DAILY"),
    )

    faellig = CalendarService.get_due_reminders(db_session, test_user)
    assert len(faellig) == 2, "zwei Vorkommen im Erinnerungsfenster"

    schluessel = [r["key"] for r in faellig]
    assert len(set(schluessel)) == len(schluessel), "jedes Vorkommen bekommt seinen eigenen Schluessel"
    for r in faellig:
        assert r["vorkommen"], "und nennt, um welches es geht"
        assert r["vorkommen"] in r["key"]


def test_erinnerungsmail_nennt_keinen_chiffretext(db_session, test_user):
    """Bei einem E2EE-Termin stand bisher `sv-cal-v1:…` als Titel in der Mail.

    Der Server hat den Schluessel nicht und wird ihn nie haben — er kann den
    Termin nur ankuendigen, nicht benennen.
    """
    from services.calendar_service import _anzeigeort, _anzeigetitel

    assert _anzeigetitel("sv-cal-v1:AAAA") == "Ein Termin"
    assert _anzeigetitel("") == "Ein Termin"
    assert _anzeigetitel("Zahnarzt") == "Zahnarzt"
    assert _anzeigeort("sv-cal-v1:AAAA") == ""
    assert _anzeigeort("Bahnhofstrasse 1") == "Bahnhofstrasse 1"


# ── CalDAV-Import ─────────────────────────────────────────────────────────


_GOOGLE_GEBURTSTAG = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:abc123@google.com
SUMMARY:Geburtstag Lisa
DTSTART;VALUE=DATE:19950314
DTEND;VALUE=DATE:19950315
RRULE:FREQ=YEARLY
EXDATE;VALUE=DATE:20270314
END:VEVENT
END:VCALENDAR
"""


def test_caldav_import_liest_endlich_die_rrule():
    """Bis zum 22.09.2026 stand RRULE nicht in der Liste der gelesenen Felder.

    Wer seinen Google-Kalender angebunden hatte, sah eine jaehrliche
    Geburtstagsserie **einmal**, am Ursprungsdatum von 1995, und nie wieder.
    """
    from services.calendar_service import _parse_vevents

    events = _parse_vevents(
        _GOOGLE_GEBURTSTAG,
        von=datetime(2026, 1, 1, tzinfo=timezone.utc),
        bis=datetime(2029, 1, 1, tzinfo=timezone.utc),
        tz_name="Europe/Berlin",
    )
    vorkommen = [e["vorkommen"] for e in events]
    assert vorkommen == ["2026-03-14", "2028-03-14"], "2027 ist per EXDATE ausgenommen"
    assert all(e["title"] == "Geburtstag Lisa" for e in events)
    assert all(e["event_id"] == "abc123@google.com" for e in events)
    # Und das ausgegebene Datum selbst, nicht nur der Vorkommensschluessel:
    # der wird lokal gerechnet und war auch dann richtig, als `start` einen
    # Tag danebenlag. Ohne diese Zeile faellt so etwas durch.
    assert [e["start"] for e in events] == ["20260314", "20280314"]
    assert [e["end"] for e in events] == ["20260315", "20280315"]


def test_caldav_ganztag_behaelt_sein_datum_oestlich_von_greenwich():
    """Ein ganzer Tag traegt ein lokales Datum, keinen Zeitpunkt.

    Wer es ueber UTC formatiert, verliert in jeder Zone oestlich von
    Greenwich einen Tag: Berliner Mitternacht ist 23:00 UTC des Vortags. Aus
    `DTSTART;VALUE=DATE:20260314` wurde so `20260313`, und der Geburtstag sass
    einen Tag zu frueh im Kalender.
    """
    from services.calendar_service import _parse_vevents

    for zone, verschiebung in (("Europe/Berlin", "+1/+2"), ("Asia/Tokyo", "+9"), ("Pacific/Kiritimati", "+14")):
        events = _parse_vevents(
            _GOOGLE_GEBURTSTAG,
            von=datetime(2026, 1, 1, tzinfo=timezone.utc),
            bis=datetime(2027, 1, 1, tzinfo=timezone.utc),
            tz_name=zone,
        )
        assert len(events) == 1, zone
        assert events[0]["start"] == "20260314", f"{zone} ({verschiebung}) verschiebt den Tag"
        assert events[0]["end"] == "20260315", f"{zone} ({verschiebung}) verschiebt das Ende"

    # Westlich von Greenwich war es nie kaputt — aber es muss auch so bleiben.
    events = _parse_vevents(
        _GOOGLE_GEBURTSTAG,
        von=datetime(2026, 1, 1, tzinfo=timezone.utc),
        bis=datetime(2027, 1, 1, tzinfo=timezone.utc),
        tz_name="America/Los_Angeles",
    )
    assert events[0]["start"] == "20260314"


def test_caldav_import_faellt_bei_fremden_regeln_auf_das_alte_verhalten_zurueck():
    """Andere Kalender schreiben Regeln ausserhalb unserer Teilmenge.

    Ein Vorkommen am Ursprungsdatum ist unvollstaendig — aber es erfindet
    nichts, und es ist genau das, was vorher auch passierte.
    """
    from services.calendar_service import _parse_vevents

    ical = _GOOGLE_GEBURTSTAG.replace(
        "RRULE:FREQ=YEARLY", "RRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1"
    )
    events = _parse_vevents(
        ical,
        von=datetime(2026, 1, 1, tzinfo=timezone.utc),
        bis=datetime(2029, 1, 1, tzinfo=timezone.utc),
        tz_name="Europe/Berlin",
    )
    assert len(events) == 1
    assert events[0]["start"] == "19950314"
    assert "vorkommen" not in events[0]


def test_caldav_import_ohne_rrule_bleibt_wie_bisher():
    from services.calendar_service import _parse_vevents

    ical = "\n".join(
        z for z in _GOOGLE_GEBURTSTAG.splitlines() if not z.startswith(("RRULE", "EXDATE"))
    )
    events = _parse_vevents(ical, tz_name="Europe/Berlin")
    assert len(events) == 1
    assert events[0]["start"] == "19950314"
    assert events[0]["title"] == "Geburtstag Lisa"






@pytest.fixture
def fremder(db_session):
    """Ein zweites Konto, ohne jedes Recht am ersten."""
    user = User(
        username="fremder",
        email="fremder@example.com",
        password_hash="fakehash",
        is_owner=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_fremder_kommt_an_die_serie_nicht_heran(db_session, test_user, fremder):
    """Ein zweites Konto darf eine fremde Serie weder sehen noch anfassen.

    Die Wiederholung ist ein neues Feld am Termin — sie muss dieselbe Schranke
    haben wie Titel und Ort. Besonders `recurrence`: wer sie aendern koennte,
    liesse einen fremden Geburtstag taeglich feuern oder brauchte nur ein
    `UNTIL` in der Vergangenheit, um ihn verschwinden zu lassen.
    """
    ev = CalendarService.create_event(
        db=db_session, user=test_user,
        title="Geburtstag Lisa", start_time="2026-03-14 00:00", end_time="2026-03-15 00:00",
        all_day=True, recurrence=_serie("FREQ=YEARLY"),
    )
    kennung = ev["event_id"]

    assert [e for e in CalendarService.get_events(db_session, fremder)
            if e["event_id"] == kennung] == [], "der fremde Termin ist sichtbar"

    for name, ruf in (
        ("Regel aendern", lambda: CalendarService.update_event(
            db=db_session, user=fremder, event_id=kennung, recurrence=_serie("FREQ=DAILY"))),
        ("Serie beenden", lambda: CalendarService.update_event(
            db=db_session, user=fremder, event_id=kennung,
            recurrence=_serie("FREQ=YEARLY;UNTIL=20000101"))),
        ("Titel aendern", lambda: CalendarService.update_event(
            db=db_session, user=fremder, event_id=kennung, title="uebernommen")),
        ("loeschen", lambda: CalendarService.delete_event(
            db=db_session, user=fremder, event_id=kennung)),
    ):
        with pytest.raises(Exception):
            ruf()

    # Zweite, unabhaengige Zusage: selbst wenn hier nichts wuerfe, muesste der
    # Termin danach unveraendert dastehen. Ein stiller Fehlschlag waere sonst
    # ebenso gut wie ein lautes Nein — und ein stiller *Erfolg* faellt nur
    # hier auf.
    meine = [e for e in CalendarService.get_events(db_session, test_user)
             if e["event_id"] == kennung]
    assert len(meine) == 1, "der eigene Termin ist weg"
    assert meine[0]["title"] == "Geburtstag Lisa"
    assert "YEARLY" in meine[0]["recurrence"]
    assert "DAILY" not in meine[0]["recurrence"]


def test_node_event_permission_enforced(db_session, test_user, fremder):
    """Prüft, dass nur Nutzer mit nodes.manage oder Owner Node-Termine anlegen oder ändern dürfen."""
    # Fremder (is_owner=False, keine Permissions) darf keinen node-Termin erstellen
    with pytest.raises(ValueError, match="Node-Termin"):
        CalendarService.create_event(
            db=db_session,
            user=fremder,
            title="Node Wartung Fake",
            start_time="2026-04-01 10:00",
            end_time="2026-04-01 12:00",
            event_type="node",
        )

    # Owner darf node-Termin erstellen
    ev = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Echte Node Wartung",
        start_time="2026-04-01 10:00",
        end_time="2026-04-01 12:00",
        event_type="node",
    )
    assert ev["event_type"] == "node"

    # Fremder darf eigenen regulären Termin nicht zu "node" upgraden
    user_ev = CalendarService.create_event(
        db=db_session,
        user=fremder,
        title="Mein normaler Termin",
        start_time="2026-04-02 10:00",
        end_time="2026-04-02 12:00",
        event_type="personal",
    )
    with pytest.raises(ValueError, match="Node-Termin"):
        CalendarService.update_event(
            db=db_session,
            user=fremder,
            event_id=user_ev["event_id"],
            event_type="node",
        )


def test_vorkommen_im_fenster_resilience_against_malformed_event(db_session, test_user, monkeypatch):
    """Prüft, dass ein fehlerhafter Termin nicht alle anderen Termine im Fenster blockiert."""
    ev1 = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Gültiger Termin 1",
        start_time="2026-05-01 10:00",
        end_time="2026-05-01 11:00",
    )
    ev2 = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Gültiger Termin 2",
        start_time="2026-05-02 10:00",
        end_time="2026-05-02 11:00",
    )

    from services import kalender_serie
    original_ausbreiten = kalender_serie.ausbreiten

    def mock_ausbreiten(serie, start, ende, **kwargs):
        # Wir simulieren einen Absturz beim ersten Termin
        if "Termin 1" in getattr(serie, "_test_marker", ""):
            raise RuntimeError("Simulation: Absturz bei fehlerhafter Serie")
        return original_ausbreiten(serie, start, ende, **kwargs)

    cal = CalendarService.get_calendar(db_session, test_user)
    assert cal is not None
    # Direktes Einfügen eines korrupten Eintrags in DB (ungültige Recurrence-Daten)
    corrupt_event = CalendarEvent(
        calendar_id=cal.id,
        user_id=test_user.id,
        title="Korrupt",
        start_time=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 1, 13, 0, tzinfo=timezone.utc),
        event_type="personal",
        recurrence="INVALID_CORRUPTED_JSON",
    )
    db_session.add(corrupt_event)
    db_session.commit()


    von = datetime(2026, 4, 30, tzinfo=timezone.utc)
    bis = datetime(2026, 5, 5, tzinfo=timezone.utc)

    # Darf trotz korruptem Event nicht abstürzen und muss die beiden gültigen Termine liefern
    vorkommen = CalendarService.vorkommen_im_fenster(db_session, test_user, von=von, bis=bis)
    titles = [v["title"] for v in vorkommen]
    assert "Gültiger Termin 1" in titles
    assert "Gültiger Termin 2" in titles

