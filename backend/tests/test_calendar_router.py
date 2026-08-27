from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from main import app
from database import get_db
from models import User
from routers.auth import get_current_user
from services.panel_settings_service import PanelSettingsService


@pytest.fixture
def override_deps(db):
    user = User(
        username="calendar_tester",
        email="cal@example.com",
        password_hash="fake",
        is_owner=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.clear()


def test_calendar_status_and_crud_api(override_deps):
    client = TestClient(app)

    # 1. Check status
    res = client.get("/api/calendar/status")
    assert res.status_code == 200
    assert res.json()["enabled"] is True

    # 2. List events (initially empty)
    res = client.get("/api/calendar/events")
    assert res.status_code == 200
    assert res.json() == []

    # 3. Create event
    res = client.post(
        "/api/calendar/events",
        json={
            "title": "Projekt Kickoff",
            "start_time": "2026-08-26 09:00",
            "end_time": "2026-08-26 10:00",
            "description": "Erstes Treffen",
            "location": "Raum Alpha",
            "color": "primary",
        },
    )
    assert res.status_code == 201
    ev_data = res.json()
    assert ev_data["title"] == "Projekt Kickoff"
    event_id = ev_data["event_id"]

    # 4. Update event
    res = client.put(
        f"/api/calendar/events/{event_id}",
        json={
            "title": "Projekt Kickoff (Verschoben)",
            "start_time": "2026-08-26 11:00",
            "end_time": "2026-08-26 12:00",
        },
    )
    assert res.status_code == 200
    assert res.json()["title"] == "Projekt Kickoff (Verschoben)"

    # 5. List events with range
    res = client.get("/api/calendar/events?start=2026-08-26T00:00:00Z&end=2026-08-26T23:59:59Z")
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["title"] == "Projekt Kickoff (Verschoben)"

    # 6. Feed .ics export
    res = client.get("/api/calendar/feed.ics")
    assert res.status_code == 200
    assert "text/calendar" in res.headers["content-type"]
    assert "BEGIN:VCALENDAR" in res.text
    assert "SUMMARY:Projekt Kickoff (Verschoben)" in res.text

    # 7. Delete event
    res = client.delete(f"/api/calendar/events/{event_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"

    res = client.get("/api/calendar/events")
    assert res.json() == []


def test_calendar_disabled_returns_403(override_deps):
    client = TestClient(app)
    PanelSettingsService.set("calendar_enabled", "false")
    try:
        res = client.get("/api/calendar/events")
        assert res.status_code == 403
    finally:
        PanelSettingsService.set("calendar_enabled", "true")


def test_calendar_test_reminder_endpoint(override_deps):
    client = TestClient(app)
    res = client.post("/api/calendar/test-reminder")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "Test-Termin" in data["title"]
    assert data["time_hint"] == "in 1 Tag"


def test_calendar_feed_token_and_unauthenticated_export(override_deps):
    user = override_deps
    client = TestClient(app)

    # 1. Fetch tokenized feed URL
    res = client.get("/api/calendar/feed-url")
    assert res.status_code == 200
    data = res.json()
    assert "feed_url" in data
    assert "token" in data
    token = data["token"]
    assert token.startswith(f"{user.id}_")

    # 2. Unauthenticated client fetching with valid token
    unauth_client = TestClient(app)
    # Clear auth override for unauthenticated check
    app.dependency_overrides.pop(get_current_user, None)
    try:
        res = unauth_client.get(f"/api/calendar/feed.ics?token={token}")
        assert res.status_code == 200
        assert "BEGIN:VCALENDAR" in res.text

        # 3. Invalid token returns 401
        res_invalid = unauth_client.get("/api/calendar/feed.ics?token=invalid_token")
        assert res_invalid.status_code == 401

        # 4. No token and no cookie returns 401
        res_no_token = unauth_client.get("/api/calendar/feed.ics")
        assert res_no_token.status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = lambda: user

