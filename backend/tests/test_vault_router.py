"""Integration tests for Vault REST Router & Zero-Knowledge Invariants."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import get_current_user, get_db, require_global, verify_csrf
from main import app
from models import Node, User, VaultEntry, VaultUserSetting


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    user = User(
        id=1,
        username="vault_owner",
        email="owner@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=True,
    )
    session.add(user)

    user2 = User(
        id=2,
        username="other_user",
        email="other@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=False,
    )
    session.add(user2)
    session.commit()
    session.refresh(user)
    session.refresh(user2)

    yield session, user, user2
    session.close()


@pytest.fixture
def client(test_db):
    session, user, _ = test_db

    def override_get_db():
        yield session

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


def test_vault_sync_empty(client):
    bucket = "a" * 64
    response = client.post(
        "/api/vault/sync",
        json={"bucket_id": bucket, "since_revision": 0, "mutations": []},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["server_revision"] == 0
    assert data["entries"] == []


def test_vault_sync_invalid_bucket_id(client):
    response = client.post(
        "/api/vault/sync",
        json={"bucket_id": "short_invalid_bucket", "since_revision": 0, "mutations": []},
    )
    assert response.status_code == 422


def test_vault_sync_insert_and_update(client, test_db):
    session, _, _ = test_db
    bucket = "b" * 64
    entry_id = "test-uuid-1"
    ciphertext_v1 = "sv-vault-v1:encrypted_payload_1"

    # 1. Insert mutation
    resp1 = client.post(
        "/api/vault/sync",
        json={
            "bucket_id": bucket,
            "since_revision": 0,
            "mutations": [
                {
                    "id": entry_id,
                    "ciphertext": ciphertext_v1,
                    "revision": 1,
                    "is_deleted": False,
                }
            ],
        },
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["server_revision"] == 1
    assert len(data1["entries"]) == 1
    assert data1["entries"][0]["id"] == entry_id
    assert data1["entries"][0]["ciphertext"] == ciphertext_v1
    assert data1["entries"][0]["is_deleted"] is False

    # 2. Sync with since_revision = 1 (should return empty since nothing is newer)
    resp2 = client.post(
        "/api/vault/sync",
        json={"bucket_id": bucket, "since_revision": 1, "mutations": []},
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["entries"]) == 0

    # 3. Update with revision 2
    ciphertext_v2 = "sv-vault-v1:encrypted_payload_2_updated"
    resp3 = client.post(
        "/api/vault/sync",
        json={
            "bucket_id": bucket,
            "since_revision": 1,
            "mutations": [
                {
                    "id": entry_id,
                    "ciphertext": ciphertext_v2,
                    "revision": 2,
                    "is_deleted": False,
                }
            ],
        },
    )
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["server_revision"] == 2
    assert len(data3["entries"]) == 1
    assert data3["entries"][0]["ciphertext"] == ciphertext_v2


def test_vault_monotonic_multi_device_sync(test_db):
    """SEC-03: Tests that new entries from Device B are never skipped on Device A."""
    session, user, _ = test_db

    def override_get_db():
        yield session

    def override_get_current_user():
        return user

    def override_verify_csrf():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[verify_csrf] = override_verify_csrf

    with TestClient(app) as test_client:
        bucket = "c" * 64

        # Device A creates Item 1 and Item 2
        r1 = test_client.post(
            "/api/vault/sync",
            json={
                "bucket_id": bucket,
                "since_revision": 0,
                "mutations": [
                    {"id": "item-1", "ciphertext": "sv-vault-v1:item1", "revision": 1, "is_deleted": False},
                    {"id": "item-2", "ciphertext": "sv-vault-v1:item2", "revision": 1, "is_deleted": False},
                ],
            },
        )
        assert r1.status_code == 200
        assert r1.json()["server_revision"] == 2

        # Device A's watermark is now 2
        device_a_watermark = 2

        # Device B (offline earlier or fresh device) creates Item 3 with local revision 1
        r2 = test_client.post(
            "/api/vault/sync",
            json={
                "bucket_id": bucket,
                "since_revision": 0,
                "mutations": [
                    {"id": "item-3", "ciphertext": "sv-vault-v1:item3", "revision": 1, "is_deleted": False},
                ],
            },
        )
        assert r2.status_code == 200
        assert r2.json()["server_revision"] == 3

        # Device A now syncs with since_revision = 2
        # It MUST receive Item 3 even though Item 3's client-mutation revision was 1!
        r3 = test_client.post(
            "/api/vault/sync",
            json={
                "bucket_id": bucket,
                "since_revision": device_a_watermark,
                "mutations": [],
            },
        )
        assert r3.status_code == 200
        data3 = r3.json()
        assert data3["server_revision"] == 3
        assert len(data3["entries"]) == 1
        assert data3["entries"][0]["id"] == "item-3"
        assert data3["entries"][0]["ciphertext"] == "sv-vault-v1:item3"

    app.dependency_overrides.clear()


def test_vault_bucket_authorization_idor_protection(test_db):
    """SEC-02: Tests IDOR prevention: users cannot read/sync other users' buckets."""
    session, user1, user2 = test_db

    def override_get_db():
        yield session

    def override_verify_csrf():
        return None

    bucket_user1 = "1" * 64
    bucket_user2 = "2" * 64

    # User 1 initializes and syncs bucket 1
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user1
    app.dependency_overrides[verify_csrf] = override_verify_csrf

    with TestClient(app) as client1:
        res1 = client1.post(
            "/api/vault/sync",
            json={
                "bucket_id": bucket_user1,
                "since_revision": 0,
                "mutations": [
                    {"id": "secret-u1", "ciphertext": "sv-vault-v1:secret1", "revision": 1, "is_deleted": False}
                ],
            },
        )
        assert res1.status_code == 200

    # User 2 tries to access User 1's bucket -> 403 Forbidden!
    app.dependency_overrides[get_current_user] = lambda: user2

    with TestClient(app) as client2:
        res_forbidden = client2.post(
            "/api/vault/sync",
            json={
                "bucket_id": bucket_user1,
                "since_revision": 0,
                "mutations": [],
            },
        )
        assert res_forbidden.status_code == 403

        # User 2 can sync their own bucket 2
        res2 = client2.post(
            "/api/vault/sync",
            json={
                "bucket_id": bucket_user2,
                "since_revision": 0,
                "mutations": [
                    {"id": "secret-u2", "ciphertext": "sv-vault-v1:secret2", "revision": 1, "is_deleted": False}
                ],
            },
        )
        assert res2.status_code == 200

    app.dependency_overrides.clear()


def test_vault_kdf_salt_endpoints(test_db):
    """SEC-04: Tests server-side KDF salt storage and retrieval."""
    session, user1, user2 = test_db

    def override_get_db():
        yield session

    def override_verify_csrf():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user1
    app.dependency_overrides[verify_csrf] = override_verify_csrf

    with TestClient(app) as client:
        # Initially no salt
        res_get1 = client.get("/api/vault/salt")
        assert res_get1.status_code == 200
        assert res_get1.json()["kdf_salt"] is None
        assert res_get1.json()["has_vault"] is False

        # Save salt and bucket
        salt_val = "dGVzdC1zYWx0LTE2Ynl0ZXM="
        bucket_val = "d" * 64
        res_set = client.post(
            "/api/vault/salt",
            json={"kdf_salt": salt_val, "bucket_id": bucket_val},
        )
        assert res_set.status_code == 200
        assert res_set.json()["kdf_salt"] == salt_val
        assert res_set.json()["bucket_id"] == bucket_val
        assert res_set.json()["has_vault"] is True

        # Fetch salt again
        res_get2 = client.get("/api/vault/salt")
        assert res_get2.status_code == 200
        assert res_get2.json()["kdf_salt"] == salt_val
        assert res_get2.json()["bucket_id"] == bucket_val
        assert res_get2.json()["has_vault"] is True

    # User 2 cannot hijack User 1's bucket in /api/vault/salt
    app.dependency_overrides[get_current_user] = lambda: user2
    with TestClient(app) as client2:
        res_hijack = client2.post(
            "/api/vault/salt",
            json={"kdf_salt": "YW5vdGhlci1zYWx0LXZhbHVl", "bucket_id": bucket_val},
        )
        assert res_hijack.status_code == 403

    app.dependency_overrides.clear()


def test_vault_csrf_enforcement(test_db):
    """SEC-09: Tests that mutating endpoints enforce CSRF validation."""
    session, user, _ = test_db

    def override_get_db():
        yield session

    def override_get_current_user():
        return user

    # Note: Do NOT override verify_csrf so actual CSRF dependency is called
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as client:
        # Request without CSRF token or cookie should fail with 403
        res = client.post(
            "/api/vault/sync",
            json={"bucket_id": "e" * 64, "since_revision": 0, "mutations": []},
        )
        assert res.status_code == 403

        res_hint = client.post(
            "/api/vault/hint",
            json={"hint": "test hint"},
        )
        assert res_hint.status_code == 403

        res_salt = client.post(
            "/api/vault/salt",
            json={"kdf_salt": "dGVzdC1zYWx0LTE2Ynl0ZXM=", "bucket_id": "e" * 64},
        )
        assert res_salt.status_code == 403

    app.dependency_overrides.clear()


def test_vault_node_assignment(client, test_db):
    session, _, _ = test_db

    # Default: None
    res = client.get("/api/vault/node-assignment")
    assert res.status_code == 200
    assert res.json()["node_id"] is None

    # Invalid node id fails with 400
    res_err = client.put("/api/vault/node-assignment", json={"node_id": "non-existent-node"})
    assert res_err.status_code == 400

    # Create a node
    node = Node(id=42, name="Dedicated Vault Node", host="10.0.0.5:8000", auth_token_enc="enc_token")
    session.add(node)
    session.commit()

    # Assign node
    res_ok = client.put("/api/vault/node-assignment", json={"node_id": "42"})
    assert res_ok.status_code == 200
    assert res_ok.json()["node_id"] == "42"
    assert res_ok.json()["assigned_node_name"] == "Dedicated Vault Node"

    # Reset assignment to None
    res_reset = client.put("/api/vault/node-assignment", json={"node_id": None})
    assert res_reset.status_code == 200
    assert res_reset.json()["node_id"] is None


def test_vault_hint_flow_and_rate_limit(client, test_db, monkeypatch):
    session, _, _ = test_db

    # 1. Initially no hint
    res_status = client.get("/api/vault/hint-status")
    assert res_status.status_code == 200
    assert res_status.json()["has_hint"] is False

    # 2. Request hint without having one -> 400
    res_req_err = client.post("/api/vault/request-hint")
    assert res_req_err.status_code == 400

    # 3. Save hint
    res_save = client.post("/api/vault/hint", json={"hint": "Mein erstes Haustier"})
    assert res_save.status_code == 200

    # 4. Status should now show has_hint = True and can_request = True
    res_status2 = client.get("/api/vault/hint-status")
    assert res_status2.status_code == 200
    assert res_status2.json()["has_hint"] is True
    assert res_status2.json()["can_request"] is True

    # 5. Mock EmailService.send_email to return True
    from services.email_service import EmailService
    sent_emails = []

    async def fake_send_email(to, subject, body, html=None):
        sent_emails.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(EmailService, "send_email", fake_send_email)

    # 6. Request hint -> succeeds and sends email
    res_req = client.post("/api/vault/request-hint")
    assert res_req.status_code == 200
    assert len(sent_emails) == 1
    assert "Mein erstes Haustier" in sent_emails[0]["body"]

    # 7. Request again immediately -> Rate limit (429)
    res_req_limit = client.post("/api/vault/request-hint")
    assert res_req_limit.status_code == 429
    assert "10 Minuten" in res_req_limit.json()["detail"]


def test_vault_disabled_via_settings(client, test_db):
    from services.panel_settings_service import PanelSettingsService

    PanelSettingsService.set("vault_enabled", "false")
    try:
        res_sync = client.post(
            "/api/vault/sync",
            json={
                "bucket_id": "a" * 64,
                "since_revision": 0,
                "mutations": [],
            },
        )
        assert res_sync.status_code == 403
        assert "deaktiviert" in res_sync.json()["detail"]

        res_hint = client.get("/api/vault/hint-status")
        assert res_hint.status_code == 403
    finally:
        PanelSettingsService.set("vault_enabled", "true")


def test_vault_node_migration_count(client, test_db):
    session, _, _ = test_db
    node = Node(id=99, name="Migration Node", host="10.0.0.9:8000", auth_token_enc="enc_token")
    session.add(node)

    # Create an entry
    e = VaultEntry(
        id="item-mig-1",
        bucket_id="f" * 64,
        ciphertext="sv-vault-v1:test",
        revision=1,
        node_id=None,
    )
    session.add(e)
    session.commit()

    # Assign node 99 -> 1 entry should be migrated
    res = client.put("/api/vault/node-assignment", json={"node_id": "99"})
    assert res.status_code == 200
    assert res.json()["migrated_entries"] >= 1
