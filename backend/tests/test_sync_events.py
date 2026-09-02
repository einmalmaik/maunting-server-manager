"""Tests für den SSE-Live-Synchronisations-Endpunkt und den SyncEventService."""

import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import get_db, get_current_user
from main import app
from models import User
from services.calendar_service import CalendarService
from services.notes_service import NotesService
from services.sync_event_service import SyncEventService


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def test_user(db_session):
    user = User(
        id=1,
        username="testuser",
        email="test@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture(autouse=True)
def clean_subscribers():
    SyncEventService.clear_all_for_testing()
    yield
    SyncEventService.clear_all_for_testing()


def test_sync_event_service_pubsub():
    """Testet das An- und Abmelden sowie Verteilen von Events."""
    conn_id, queue = SyncEventService.subscribe(user_id=1, team_ids=[10, 20], is_admin=False)
    assert SyncEventService.get_subscriber_count() == 1

    # 1. Eigenes Event wird zugestellt
    delivered = SyncEventService.publish({"entity": "notes", "action": "created", "id": "n1"}, user_id=1)
    assert delivered == 1
    event = queue.get_nowait()
    assert event["entity"] == "notes"
    assert event["id"] == "n1"

    # 2. Team Event wird zugestellt
    delivered = SyncEventService.publish({"entity": "calendar", "action": "updated", "id": "c1"}, team_id=10)
    assert delivered == 1
    event = queue.get_nowait()
    assert event["entity"] == "calendar"
    assert event["id"] == "c1"

    # 3. Fremdes Event eines anderen Nutzers / Teams wird NICHT zugestellt
    delivered = SyncEventService.publish({"entity": "notes", "action": "deleted", "id": "n2"}, user_id=2, team_id=99)
    assert delivered == 0
    assert queue.empty()

    # 4. Unsubscribe räumt Verbindung auf
    SyncEventService.unsubscribe(conn_id)
    assert SyncEventService.get_subscriber_count() == 0


def test_notes_mutation_triggers_sync_event(db_session, test_user):
    """Prüft, ob Notizen-Erstellung und -Änderung automatisch Sync-Signale publizieren."""
    conn_id, queue = SyncEventService.subscribe(user_id=test_user.id, team_ids=[], is_admin=False)

    # 1. Note create
    note = NotesService.create_note(
        db=db_session,
        user=test_user,
        title="SSE Test Einkaufsliste",
        content="- [ ] Milch",
        category="shopping",
    )
    assert not queue.empty()
    evt1 = queue.get_nowait()
    assert evt1["entity"] == "notes"
    assert evt1["action"] == "created"
    assert evt1["id"] == note["note_uid"]

    # 2. Note update (Checkliste toggeln)
    NotesService.update_note(
        db=db_session,
        user=test_user,
        note_id_or_uid=note["note_uid"],
        content="- [x] Milch",
    )
    evt2 = queue.get_nowait()
    assert evt2["entity"] == "notes"
    assert evt2["action"] == "updated"
    assert evt2["id"] == note["note_uid"]

    # 3. Note delete
    NotesService.delete_note(db=db_session, user=test_user, note_id_or_uid=note["note_uid"])
    evt3 = queue.get_nowait()
    assert evt3["entity"] == "notes"
    assert evt3["action"] == "deleted"
    assert evt3["id"] == note["note_uid"]


def test_calendar_mutation_triggers_sync_event(db_session, test_user):
    """Prüft, ob Kalendertermin-Mutationen automatisch Sync-Signale publizieren."""
    conn_id, queue = SyncEventService.subscribe(user_id=test_user.id, team_ids=[], is_admin=False)

    # 1. Event create
    cal_ev = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="SSE Wartung",
        start_time="2026-09-02T10:00:00Z",
        end_time="2026-09-02T11:00:00Z",
    )
    evt1 = queue.get_nowait()
    assert evt1["entity"] == "calendar"
    assert evt1["action"] == "created"
    assert evt1["id"] == cal_ev["event_id"]

    # 2. Event update
    CalendarService.update_event(
        db=db_session,
        user=test_user,
        event_id=cal_ev["event_id"],
        title="SSE Wartung (Verschoben)",
    )
    evt2 = queue.get_nowait()
    assert evt2["entity"] == "calendar"
    assert evt2["action"] == "updated"
    assert evt2["id"] == cal_ev["event_id"]

    # 3. Event delete
    CalendarService.delete_event(
        db=db_session,
        user=test_user,
        event_id=cal_ev["event_id"],
    )
    evt3 = queue.get_nowait()
    assert evt3["entity"] == "calendar"
    assert evt3["action"] == "deleted"
    assert evt3["id"] == cal_ev["event_id"]


def test_sse_endpoint_authentication():
    """Prüft, dass der SSE-Endpunkt unauthentifizierte Anfragen mit 401 ablehnt."""
    client = TestClient(app)
    resp = client.get("/api/events/live")
    assert resp.status_code == 401

    resp_alias = client.get("/api/sync/events")
    assert resp_alias.status_code == 401


def test_admin_note_mutation_notifies_owner(db_session, test_user):
    """Prüft, dass der Notizen-Eigentümer auch dann SSE-Events erhält, wenn ein Admin die Notiz ändert/löscht."""
    admin_user = User(
        id=99,
        username="adminuser",
        email="admin@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=True,
    )
    db_session.add(admin_user)
    db_session.commit()

    conn_id, queue = SyncEventService.subscribe(user_id=test_user.id, team_ids=[], is_admin=False)

    note = NotesService.create_note(
        db=db_session,
        user=test_user,
        title="Persönliche Notiz",
        content="Inhalt",
    )
    _ = queue.get_nowait()  # consume create event

    # Admin updates user's note
    NotesService.update_note(
        db=db_session,
        user=admin_user,
        note_id_or_uid=note["note_uid"],
        title="Persönliche Notiz (Admin Edit)",
    )
    assert not queue.empty()
    update_evt = queue.get_nowait()
    assert update_evt["entity"] == "notes"
    assert update_evt["action"] == "updated"
    assert update_evt["id"] == note["note_uid"]

    # Admin deletes user's note
    NotesService.delete_note(db=db_session, user=admin_user, note_id_or_uid=note["note_uid"])
    assert not queue.empty()
    delete_evt = queue.get_nowait()
    assert delete_evt["entity"] == "notes"
    assert delete_evt["action"] == "deleted"
    assert delete_evt["id"] == note["note_uid"]


def test_admin_calendar_mutation_notifies_owner(db_session, test_user):
    """Prüft, dass der Kalender-Eigentümer auch dann SSE-Events erhält, wenn ein Admin den Termin ändert/löscht."""
    admin_user = User(
        id=99,
        username="adminuser2",
        email="admin2@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=True,
    )
    db_session.add(admin_user)
    db_session.commit()

    conn_id, queue = SyncEventService.subscribe(user_id=test_user.id, team_ids=[], is_admin=False)

    ev = CalendarService.create_event(
        db=db_session,
        user=test_user,
        title="Persönlicher Termin",
        start_time="2026-09-02T10:00:00Z",
        end_time="2026-09-02T11:00:00Z",
    )
    _ = queue.get_nowait()  # consume create event

    # Admin updates user's event
    CalendarService.update_event(
        db=db_session,
        user=admin_user,
        event_id=ev["event_id"],
        title="Persönlicher Termin (Admin Edit)",
    )
    assert not queue.empty()
    update_evt = queue.get_nowait()
    assert update_evt["entity"] == "calendar"
    assert update_evt["action"] == "updated"
    assert update_evt["id"] == ev["event_id"]

    # Admin deletes user's event
    CalendarService.delete_event(
        db=db_session,
        user=admin_user,
        event_id=ev["event_id"],
    )
    assert not queue.empty()
    delete_evt = queue.get_nowait()
    assert delete_evt["entity"] == "calendar"
    assert delete_evt["action"] == "deleted"
    assert delete_evt["id"] == ev["event_id"]


def test_threadsafe_publish():
    """Prüft die Thread-Sicherheit der publish-Methode."""
    import threading

    conn_id, queue = SyncEventService.subscribe(user_id=1, team_ids=[], is_admin=False)

    def worker():
        SyncEventService.publish({"entity": "notes", "action": "created", "id": "t1"}, user_id=1)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert not queue.empty()
    evt = queue.get_nowait()
    assert evt["id"] == "t1"


def test_personal_notes_privacy_isolation(db_session, test_user):
    """Prüft, dass persönliche Notizen ausschließlich an den jeweiligen Benutzer gestreamt werden."""
    other_user = User(
        id=2,
        username="otheruser",
        email="other@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=False,
    )
    admin_user = User(
        id=99,
        username="adminuser3",
        email="admin3@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=True,
    )
    db_session.add(other_user)
    db_session.add(admin_user)
    db_session.commit()

    conn1, q1 = SyncEventService.subscribe(user_id=test_user.id, team_ids=[], is_admin=False)
    conn2, q2 = SyncEventService.subscribe(user_id=other_user.id, team_ids=[], is_admin=False)
    conn_admin, q_admin = SyncEventService.subscribe(user_id=admin_user.id, team_ids=[], is_admin=True)

    # test_user creates personal note
    note = NotesService.create_note(
        db=db_session,
        user=test_user,
        title="Vertrauliche persönliche Notiz",
        content="Geheime Daten",
    )

    # Only test_user should receive the event
    assert not q1.empty()
    evt = q1.get_nowait()
    assert evt["id"] == note["note_uid"]

    # Other user and admin MUST NOT receive test_user's personal note
    assert q2.empty()
    assert q_admin.empty()


