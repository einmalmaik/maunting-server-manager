"""Unit tests for NotesService."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models import User, Note, Team, TeamMember
from services.notes_service import NotesService


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
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


@pytest.fixture
def other_user(db_session):
    user = User(
        id=2,
        username="otheruser",
        email="other@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_create_and_get_personal_note(db_session, test_user):
    note = NotesService.create_note(
        db_session,
        user=test_user,
        title="Einkaufsliste",
        content="- [ ] Butter\n- [ ] Milch",
        category="shopping",
        color="emerald",
        is_pinned=True,
    )
    assert note["title"] == "Einkaufsliste"
    assert note["category"] == "shopping"
    assert note["color"] == "emerald"
    assert note["is_pinned"] is True
    assert note["note_type"] == "personal"

    notes = NotesService.get_notes(db_session, user=test_user)
    assert len(notes) == 1
    assert notes[0]["note_uid"] == note["note_uid"]


def test_update_and_delete_note(db_session, test_user):
    note = NotesService.create_note(
        db_session,
        user=test_user,
        title="Projekt-Idee",
        content="Erste Notiz",
        category="idea",
    )
    updated = NotesService.update_note(
        db_session,
        user=test_user,
        note_id_or_uid=note["note_uid"],
        title="Neue Projekt-Idee",
        content="Aktualisierter Text",
    )
    assert updated["title"] == "Neue Projekt-Idee"
    assert updated["content"] == "Aktualisierter Text"

    res = NotesService.delete_note(db_session, user=test_user, note_id_or_uid=note["note_uid"])
    assert res["status"] == "deleted"

    notes = NotesService.get_notes(db_session, user=test_user)
    assert len(notes) == 0


def test_toggle_pin_and_archive(db_session, test_user):
    note = NotesService.create_note(
        db_session,
        user=test_user,
        title="Wichtige Notiz",
        is_pinned=False,
    )
    pinned = NotesService.toggle_pin(db_session, user=test_user, note_id_or_uid=note["note_uid"])
    assert pinned["is_pinned"] is True

    archived = NotesService.toggle_archive(db_session, user=test_user, note_id_or_uid=note["note_uid"])
    assert archived["is_archived"] is True

    # Active list excludes archived by default
    active_notes = NotesService.get_notes(db_session, user=test_user, is_archived=False)
    assert len(active_notes) == 0

    all_notes = NotesService.get_notes(db_session, user=test_user, is_archived=None)
    assert len(all_notes) == 1


def test_privacy_other_user_cannot_access(db_session, test_user, other_user):
    note = NotesService.create_note(
        db_session,
        user=test_user,
        title="Geheime Notiz",
        content="Streng vertraulich",
    )
    other_notes = NotesService.get_notes(db_session, user=other_user)
    assert len(other_notes) == 0

    with pytest.raises(ValueError):
        NotesService.get_note(db_session, user=other_user, note_id_or_uid=note["note_uid"])
