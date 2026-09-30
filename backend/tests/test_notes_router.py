"""Integration tests for Notes REST Router."""
import pytest
from fastapi.testclient import TestClient
from main import app
from dependencies import get_db, get_current_user, verify_csrf
from models import User, Note, PanelSetting


@pytest.fixture
def test_db(db):
    session = db

    # Create admin user
    user = User(
        id=1,
        username="admin",
        email="admin@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    yield session, user
    session.close()


@pytest.fixture
def client(test_db):
    session, user = test_db

    def override_get_db():
        try:
            yield session
        finally:
            pass

    def override_get_current_user():
        return user

    def override_verify_csrf():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[verify_csrf] = override_verify_csrf

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_notes_crud_endpoints(client):
    # Create note
    res = client.post(
        "/api/notes",
        json={
            "title": "Einkaufsliste Supermarkt",
            "content": "- [ ] 1x Butter (~1.89 €)\n- [ ] 6x Eier (~1.99 €)",
            "category": "shopping",
            "color": "emerald",
            "is_pinned": True,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["title"] == "Einkaufsliste Supermarkt"
    note_uid = data["note_uid"]

    # List notes
    list_res = client.get("/api/notes")
    assert list_res.status_code == 200
    notes = list_res.json()
    assert len(notes) == 1
    assert notes[0]["note_uid"] == note_uid

    # Update note via PUT
    update_res = client.put(
        f"/api/notes/{note_uid}",
        json={"title": "Einkaufsliste Rewe"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["title"] == "Einkaufsliste Rewe"

    # Toggle pin
    pin_res = client.post(f"/api/notes/{note_uid}/pin")
    assert pin_res.status_code == 200
    assert pin_res.json()["is_pinned"] is False

    # Delete note
    del_res = client.delete(f"/api/notes/{note_uid}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"


def test_archivierte_notizen_kommen_mit_include_archived(client):
    """Die App laedt mit ``include_archived=true`` und filtert selbst nach Aktiv/Archiviert.

    Bis 5.0.3 kannte der Server den Parameter nicht und lieferte nur nicht
    archivierte Notizen: eine archivierte Notiz stand nie unter „Archiviert“
    und liess sich auch nicht wiederherstellen.
    """
    aktiv = client.post("/api/notes", json={"title": "Aktiv"}).json()["note_uid"]
    archiv = client.post("/api/notes", json={"title": "Archiv"}).json()["note_uid"]
    assert client.post(f"/api/notes/{archiv}/archive").json()["is_archived"] is True

    alle = {n["note_uid"]: n["is_archived"] for n in client.get("/api/notes?include_archived=true").json()}
    assert alle == {aktiv: False, archiv: True}

    # Ohne den Parameter bleibt es, wie es war: nur die aktiven — so lesen es andere Aufrufer.
    assert [n["note_uid"] for n in client.get("/api/notes").json()] == [aktiv]
    assert [n["note_uid"] for n in client.get("/api/notes?is_archived=true").json()] == [archiv]

    # Wiederhergestellt: wieder unter den aktiven.
    assert client.post(f"/api/notes/{archiv}/archive").json()["is_archived"] is False
    assert {n["note_uid"] for n in client.get("/api/notes").json()} == {aktiv, archiv}
