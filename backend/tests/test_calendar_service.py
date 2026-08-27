from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, UserCalendar, CalendarEvent
from services.calendar_service import CalendarService


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


