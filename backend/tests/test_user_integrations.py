import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models.user import User
from models.user_mailbox import UserMailbox
from models.user_calendar import UserCalendar
from services.dis_client import DisClient


def test_user_mailbox_crud(client: TestClient, db: Session, regular_user: User, user_cookies: dict):
    # 1. List initially empty
    res = client.get("/api/user/integrations/mailboxes", cookies=user_cookies)
    assert res.status_code == 200
    assert res.json() == []

    # 2. Create custom SMTP/IMAP mailbox
    payload = {
        "name": "Mein Arbeitskonto",
        "email": "user@example.com",
        "provider_type": "custom",
        "is_default": True,
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_use_ssl": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "imap_username": "user@example.com",
        "smtp_username": "user@example.com",
        "password_or_token": "super-secret-password-123",
        "sync_enabled": True,
    }
    res = client.post("/api/user/integrations/mailboxes", json=payload, cookies=user_cookies)
    assert res.status_code == 201
    data = res.json()
    mailbox_id = data["id"]
    assert data["email"] == "user@example.com"
    assert data["name"] == "Mein Arbeitskonto"
    assert data["has_credentials"] is True
    assert "password_or_token" not in data
    assert "super-secret-password-123" not in str(data)

    # 3. Verify DB row encryption with DIS AES-256-GCM
    row = db.get(UserMailbox, mailbox_id)
    assert row is not None
    assert row.credentials_encrypted is not None
    assert "super-secret-password-123" not in row.credentials_encrypted
    decrypted = DisClient.decrypt(row.credentials_encrypted, aad=f"msm:user_mailbox:{regular_user.id}")
    assert decrypted == "super-secret-password-123"

    # 4. Update mailbox
    update_payload = {
        "name": "Haupt-E-Mail-Postfach",
        "sync_enabled": False,
    }
    res = client.patch(f"/api/user/integrations/mailboxes/{mailbox_id}", json=update_payload, cookies=user_cookies)
    assert res.status_code == 200
    assert res.json()["name"] == "Haupt-E-Mail-Postfach"
    assert res.json()["sync_enabled"] is False

    # 5. Delete mailbox
    res = client.delete(f"/api/user/integrations/mailboxes/{mailbox_id}", cookies=user_cookies)
    assert res.status_code == 204
    db.expire_all()
    assert db.get(UserMailbox, mailbox_id) is None


def test_user_calendar_crud(client: TestClient, db: Session, regular_user: User, user_cookies: dict):
    # 1. List initially empty
    res = client.get("/api/user/integrations/calendars", cookies=user_cookies)
    assert res.status_code == 200
    assert res.json() == []

    # 2. Create CalDAV calendar
    payload = {
        "name": "Mein Kalender",
        "provider_type": "caldav",
        "is_default": True,
        "caldav_url": "https://caldav.example.com/dav/users/test/calendar/",
        "caldav_username": "testuser",
        "password_or_token": "caldav-secret-password",
    }
    res = client.post("/api/user/integrations/calendars", json=payload, cookies=user_cookies)
    assert res.status_code == 201
    data = res.json()
    calendar_id = data["id"]
    assert data["name"] == "Mein Kalender"
    assert data["has_credentials"] is True
    assert "password_or_token" not in data
    assert "caldav-secret-password" not in str(data)

    # 3. Verify DB row encryption
    row = db.get(UserCalendar, calendar_id)
    assert row is not None
    assert row.credentials_encrypted is not None
    decrypted = DisClient.decrypt(row.credentials_encrypted, aad=f"msm:user_calendar:{regular_user.id}")
    assert decrypted == "caldav-secret-password"

    # 4. Delete calendar
    res = client.delete(f"/api/user/integrations/calendars/{calendar_id}", cookies=user_cookies)
    assert res.status_code == 204
    db.expire_all()
    assert db.get(UserCalendar, calendar_id) is None
