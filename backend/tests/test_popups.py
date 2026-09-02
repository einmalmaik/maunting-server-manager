from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, PanelPopup, UserPopupState
from services.ai_proposal_service import _popup_create_payload, _ausfuehren_popup_create, _AusfuehrungsRahmen


def test_popup_lifecycle(client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, db: Session):
    headers = {"X-CSRF-Token": csrf_token}

    # 1. Keine Popups initial
    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json() is None

    # 2. Admin legt Popup an
    create_payload = {
        "title": "Wartungsarbeiten am Wochenende",
        "content_markdown": "### Wichtige Information\nAm Samstag finden Wartungsarbeiten statt.",
        "is_active": True,
        "button_text": "Statusseite",
        "button_url": "https://status.example.com",
    }
    resp = client.post("/api/popups/admin", json=create_payload, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 201
    popup_data = resp.json()
    popup_id = popup_data["id"]
    assert popup_data["title"] == "Wartungsarbeiten am Wochenende"

    # 3. Aktives Popup abrufen
    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    active = resp.json()
    assert active is not None
    assert active["id"] == popup_id
    assert active["title"] == "Wartungsarbeiten am Wochenende"

    # 4. Snooze (24h)
    resp = client.post(f"/api/popups/{popup_id}/dismiss", json={"mode": "snooze"}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["mode"] == "snooze"

    # Jetzt sollte kein aktives Popup mehr geliefert werden (innerhalb 24h)
    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json() is None

    # 5. Zeitreise: Dismissal vor 25 Stunden setzen
    state = db.query(UserPopupState).filter_by(user_id=owner_user.id, popup_id=popup_id).first()
    assert state is not None
    state.last_dismissed_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()

    # Jetzt muss es wieder auftauchen!
    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json()["id"] == popup_id

    # 6. Permanent dismiss
    resp = client.post(f"/api/popups/{popup_id}/dismiss", json={"mode": "permanent"}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["mode"] == "permanent"

    # Auch nach 48 Stunden darf es nie wieder auftauchen
    state = db.query(UserPopupState).filter_by(user_id=owner_user.id, popup_id=popup_id).first()
    state.last_dismissed_at = datetime.now(timezone.utc) - timedelta(hours=48)
    db.commit()

    resp = client.get("/api/popups/active", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json() is None

    # 7. Admin Update & Delete
    resp = client.put(f"/api/popups/admin/{popup_id}", json={"title": "Aktualisierter Titel"}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Aktualisierter Titel"

    resp = client.delete(f"/api/popups/admin/{popup_id}", cookies=owner_cookies, headers=headers)
    assert resp.status_code == 204

    resp = client.get("/api/popups/admin/list", cookies=owner_cookies)
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_ai_popup_proposal_execution(db: Session, owner_user: User):
    # Testet Payload-Validierung und Ausführung des KI-Tools
    payload, preview = _popup_create_payload(
        db,
        owner_user,
        {
            "title": "Neues Feature: Discord RPC",
            "content_markdown": "Ab sofort unterstützen wir Discord Rich Presence.",
            "is_active": True,
        },
    )
    assert payload["title"] == "Neues Feature: Discord RPC"
    assert preview["operation"] == "popup_create"

    rahmen = _AusfuehrungsRahmen(
        payload=payload,
        server_id=None,
        active_user=owner_user,
        correlation_id="corr_test",
        expected_revision=None,
        row_id="prop_test",
        guardian=None,
        tool_name="propose_popup_create",
    )
    res = _ausfuehren_popup_create(db, rahmen)
    assert res.result["created"] is True
    popup_id = res.result["popup_id"]

    db_popup = db.query(PanelPopup).filter_by(id=popup_id).first()
    assert db_popup is not None
    assert db_popup.title == "Neues Feature: Discord RPC"
