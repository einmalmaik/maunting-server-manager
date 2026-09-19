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


def test_notes_stored_encrypted_in_database(db_session, test_user):
    """Beweist, dass in der Datenbank absolut KEIN Klartext fuer title und content existiert."""
    secret_title = "Absolutes_Geheimnis_12345"
    secret_content = "Dies_ist_vertraulicher_Inhalt_XYZ"

    note = NotesService.create_note(
        db_session,
        user=test_user,
        title=secret_title,
        content=secret_content,
    )

    # Direkte RAW SQL Abfrage der DB-Tabelle (wie ein neugieriger Admin)
    from sqlalchemy import text
    row = db_session.execute(
        text("SELECT title, content FROM notes WHERE note_uid = :uid"),
        {"uid": note["note_uid"]},
    ).fetchone()

    raw_title, raw_content = row[0], row[1]

    # Der Klartext darf NIEMALS in der Datenbank stehen!
    assert secret_title not in raw_title
    assert secret_content not in raw_content
    # Es muss ein Base64-DIS-Ciphertext sein
    assert len(raw_title) > 20
    assert len(raw_content) > 20

    # Aber fuer den berechtigten Nutzer wird es sauber entschluesselt
    fetched = NotesService.get_note(db_session, user=test_user, note_id_or_uid=note["note_uid"])
    assert fetched["title"] == secret_title
    assert fetched["content"] == secret_content


def test_notes_automatic_migration_of_legacy_plaintext(db_session, test_user):
    """Beweist, dass Altdaten im Klartext beim ersten Aufruf automatisch verschluesselt werden."""
    from sqlalchemy import text
    import uuid

    legacy_uid = str(uuid.uuid4())
    legacy_title = "Alte_Unverschluesselte_Notiz"
    legacy_content = "Alter_Klartext_der_damals_gespeichert_wurde"

    # Altdaten direkt unverschluesselt in die DB einschleusen
    db_session.execute(
        text(
            "INSERT INTO notes (user_id, note_uid, title, content, category, color, is_pinned, is_archived, note_type, created_at, updated_at) "
            "VALUES (:uid, :nuid, :title, :content, 'personal', 'primary', 0, 0, 'personal', datetime('now'), datetime('now'))"
        ),
        {
            "uid": test_user.id,
            "nuid": legacy_uid,
            "title": legacy_title,
            "content": legacy_content,
        },
    )
    db_session.commit()

    # Vor dem Aufruf: In der DB steht Klartext
    before_row = db_session.execute(
        text("SELECT title, content FROM notes WHERE note_uid = :uid"),
        {"uid": legacy_uid},
    ).fetchone()
    assert before_row[0] == legacy_title
    assert before_row[1] == legacy_content

    # Nutzer ruft get_notes() auf
    notes = NotesService.get_notes(db_session, user=test_user)
    migrated_note = next(n for n in notes if n["note_uid"] == legacy_uid)
    assert migrated_note["title"] == legacy_title
    assert migrated_note["content"] == legacy_content

    # Nach dem Aufruf: Die Datenbank MUSS jetzt transparent und dauerhaft verschluesselt sein!
    after_row = db_session.execute(
        text("SELECT title, content FROM notes WHERE note_uid = :uid"),
        {"uid": legacy_uid},
    ).fetchone()
    assert after_row[0] != legacy_title
    assert legacy_title not in after_row[0]
    assert after_row[1] != legacy_content
    assert legacy_content not in after_row[1]


def test_notes_client_e2ee_opaque_storage(db_session, test_user):
    """Beweist, dass client-seitig verschluesselte Daten (sv-note-v1:) vom Server nicht angefasst werden."""
    from sqlalchemy import text

    client_cipher_title = "sv-note-v1:abcdef1234567890base64title"
    client_cipher_content = "sv-note-v1:fedcba0987654321base64content"

    note = NotesService.create_note(
        db_session,
        user=test_user,
        title=client_cipher_title,
        content=client_cipher_content,
        category="personal",
    )

    # In der DB muss exakt der Client-Ciphertext stehen
    row = db_session.execute(
        text("SELECT title, content FROM notes WHERE note_uid = :uid"),
        {"uid": note["note_uid"]},
    ).fetchone()
    assert row[0] == client_cipher_title
    assert row[1] == client_cipher_content

    # Beim Abruf erhaelt der Client den Ciphertext unveraendert zur client-seitigen Entschluesselung
    fetched = NotesService.get_note(db_session, user=test_user, note_id_or_uid=note["note_uid"])
    assert fetched["title"] == client_cipher_title
    assert fetched["content"] == client_cipher_content

    # Update mit neuem Client-Ciphertext
    new_cipher_title = "sv-note-v1:new9876543210title"
    updated = NotesService.update_note(
        db_session,
        user=test_user,
        note_id_or_uid=note["note_uid"],
        title=new_cipher_title,
    )
    assert updated["title"] == new_cipher_title

    upd_row = db_session.execute(
        text("SELECT title FROM notes WHERE note_uid = :uid"),
        {"uid": note["note_uid"]},
    ).fetchone()
    assert upd_row[0] == new_cipher_title

    client_uid = "client-uuid-98765-note"
    note_with_uid = NotesService.create_note(
        db_session,
        user=test_user,
        note_uid=client_uid,
        title=client_cipher_title,
        content=client_cipher_content,
        category="personal",
    )
    assert note_with_uid["note_uid"] == client_uid

    # Idempotenter Replay mit gleicher UID liefert denselben Eintrag
    replay_note = NotesService.create_note(
        db_session,
        user=test_user,
        note_uid=client_uid,
        title=client_cipher_title,
        content=client_cipher_content,
        category="personal",
    )
    assert replay_note["note_uid"] == client_uid
    assert replay_note["id"] == note_with_uid["id"]


