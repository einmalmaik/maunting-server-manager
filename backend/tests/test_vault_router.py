"""Integration tests for Vault REST Router & Zero-Knowledge Invariants."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import get_current_user, get_db, verify_csrf
from main import app
from models import User, VaultEntry, VaultUserSetting


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


def test_vault_blind_sync_success(test_db):
    """Verifies that blind sync works completely unauthenticated (no cookies, no CSRF, no user)."""
    session, _, _ = test_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as anonymous_client:
        bucket = "f" * 64
        token = "9" * 64
        res = anonymous_client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": bucket,
                "auth_token": token,
                "since_revision": 0,
                "mutations": [
                    {
                        "id": "entry-blind-1",
                        "ciphertext": "sv-vault-v1:blind_encrypted_data",
                        "revision": 1,
                        "is_deleted": False,
                    }
                ],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["server_revision"] == 1
        assert len(data["entries"]) == 1
        assert data["entries"][0]["id"] == "entry-blind-1"
        assert data["entries"][0]["ciphertext"] == "sv-vault-v1:blind_encrypted_data"

    app.dependency_overrides.clear()


def test_vault_blind_sync_invalid_token(test_db):
    """Verifies that attempting blind sync with an invalid auth_token returns 401 Unauthorized."""
    session, _, _ = test_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as anonymous_client:
        bucket = "8" * 64
        token_valid = "a" * 64
        token_invalid = "b" * 64

        # Register bucket with token_valid
        r1 = anonymous_client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": bucket,
                "auth_token": token_valid,
                "since_revision": 0,
                "mutations": [],
            },
        )
        assert r1.status_code == 200

        # Attempt to access with token_invalid -> 401
        r2 = anonymous_client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": bucket,
                "auth_token": token_invalid,
                "since_revision": 0,
                "mutations": [],
            },
        )
        assert r2.status_code == 401

    app.dependency_overrides.clear()


def test_vault_migration_preserves_entries(test_db):
    """Verifies that existing entries remain 100% accessible after blind verifier registration and user_id is decoupled."""
    session, user1, _ = test_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        bucket = "7" * 64
        auth_token = "c" * 64

        # 1. Existing legacy entry created in database under user1
        legacy_setting = VaultUserSetting(user_id=user1.id, bucket_id=bucket)
        session.add(legacy_setting)
        legacy_entry = VaultEntry(
            id="legacy-item-1",
            bucket_id=bucket,
            ciphertext="sv-vault-v1:original_unbroken_vault_data",
            revision=5,
            is_deleted=False,
        )
        session.add(legacy_entry)
        session.commit()

        # 2. Client unlocks and performs blind-sync with derived bucketAuthToken
        res_blind = client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": bucket,
                "auth_token": auth_token,
                "since_revision": 0,
                "mutations": [],
            },
        )
        assert res_blind.status_code == 200
        data = res_blind.json()
        assert data["server_revision"] == 5
        assert len(data["entries"]) == 1
        assert data["entries"][0]["id"] == "legacy-item-1"
        assert data["entries"][0]["ciphertext"] == "sv-vault-v1:original_unbroken_vault_data"

        # 3. Verify zero-breakage privacy: VaultUserSetting.bucket_id is decoupled (None)
        session.expire_all()
        refreshed_setting = session.get(VaultUserSetting, user1.id)
        assert refreshed_setting.bucket_id is None

        # 4. Subsequent blind mutations succeed under the same bucket
        res_mutate = client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": bucket,
                "auth_token": auth_token,
                "since_revision": 5,
                "mutations": [
                    {
                        "id": "new-blind-item",
                        "ciphertext": "sv-vault-v1:new_blind_data",
                        "revision": 1,
                        "is_deleted": False,
                    }
                ],
            },
        )
        assert res_mutate.status_code == 200
        assert res_mutate.json()["server_revision"] == 6

    app.dependency_overrides.clear()


def test_vault_blind_sync_duplicate_mutations_in_batch(test_db):
    """Verifies that multiple mutations targeting the same ID within a single request batch succeed without IntegrityError."""
    session, _, _ = test_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        bucket = "3" * 64
        auth_token = "4" * 64

        res = client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": bucket,
                "auth_token": auth_token,
                "since_revision": 0,
                "mutations": [
                    {
                        "id": "item-dup-1",
                        "ciphertext": "sv-vault-v1:initial_version",
                        "revision": 1,
                        "is_deleted": False,
                    },
                    {
                        "id": "item-dup-1",
                        "ciphertext": "sv-vault-v1:updated_in_same_batch",
                        "revision": 2,
                        "is_deleted": False,
                    },
                ],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["server_revision"] == 2
        # Only the final updated ciphertext should be in entries
        assert len(data["entries"]) == 1
        assert data["entries"][0]["id"] == "item-dup-1"
        assert data["entries"][0]["ciphertext"] == "sv-vault-v1:updated_in_same_batch"

    app.dependency_overrides.clear()


def test_vault_blind_sync_validation_hex(test_db):
    """Verifies that non-64 hex strings for bucket_id or auth_token are rejected with 422."""
    session, _, _ = test_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        # bucket_id too short
        r1 = client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": "short",
                "auth_token": "a" * 64,
                "since_revision": 0,
                "mutations": [],
            },
        )
        assert r1.status_code == 422

        # auth_token not hex
        r2 = client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": "a" * 64,
                "auth_token": "z" * 64,
                "since_revision": 0,
                "mutations": [],
            },
        )
        assert r2.status_code == 422

    app.dependency_overrides.clear()


def test_vault_cross_bucket_same_id_composite_pk(client, test_db):
    """Verifies that identical entry IDs in different buckets do not collide or block each other (composite PK)."""
    session, user1, user2 = test_db
    bucket_1 = "1" * 64
    bucket_2 = "2" * 64
    shared_entry_id = "same-shared-item-uuid-42"

    # User 1 syncs entry with shared_entry_id into bucket_1
    res1 = client.post(
        "/api/vault/sync",
        json={
            "bucket_id": bucket_1,
            "since_revision": 0,
            "mutations": [
                {
                    "id": shared_entry_id,
                    "ciphertext": "sv-vault-v1:user1_ciphertext_payload",
                    "revision": 1,
                    "is_deleted": False,
                }
            ],
        },
    )
    assert res1.status_code == 200
    assert res1.json()["server_revision"] == 1

    # Override current user to user2 so user2 can sync bucket_2
    def override_user2():
        return user2

    app.dependency_overrides[get_current_user] = override_user2

    # User 2 syncs entry with SAME shared_entry_id into bucket_2
    res2 = client.post(
        "/api/vault/sync",
        json={
            "bucket_id": bucket_2,
            "since_revision": 0,
            "mutations": [
                {
                    "id": shared_entry_id,
                    "ciphertext": "sv-vault-v1:user2_completely_different_payload",
                    "revision": 1,
                    "is_deleted": False,
                }
            ],
        },
    )
    assert res2.status_code == 200
    assert res2.json()["server_revision"] == 1

    # Verify both entries exist independently in the database
    entries = session.query(VaultEntry).filter(VaultEntry.id == shared_entry_id).all()
    assert len(entries) == 2
    bucket_map = {e.bucket_id: e.ciphertext for e in entries}
    assert bucket_map[bucket_1] == "sv-vault-v1:user1_ciphertext_payload"
    assert bucket_map[bucket_2] == "sv-vault-v1:user2_completely_different_payload"

    # Restore user1
    def override_user1():
        return user1

    app.dependency_overrides[get_current_user] = override_user1


def test_vault_blind_sync_cross_bucket_same_id(test_db):
    """Verifies blind-sync also isolates identical IDs across different buckets via composite PK."""
    session, _, _ = test_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        b1 = "a" * 64
        b2 = "b" * 64
        t1 = "1" * 64
        t2 = "2" * 64
        entry_id = "blind-shared-uuid-99"

        r1 = client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": b1,
                "auth_token": t1,
                "since_revision": 0,
                "mutations": [
                    {
                        "id": entry_id,
                        "ciphertext": "sv-vault-v1:bucket1_data",
                        "revision": 1,
                        "is_deleted": False,
                    }
                ],
            },
        )
        assert r1.status_code == 200

        r2 = client.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": b2,
                "auth_token": t2,
                "since_revision": 0,
                "mutations": [
                    {
                        "id": entry_id,
                        "ciphertext": "sv-vault-v1:bucket2_data",
                        "revision": 1,
                        "is_deleted": False,
                    }
                ],
            },
        )
        assert r2.status_code == 200

    app.dependency_overrides.clear()


def test_vault_sync_in_batch_deduplication(client):
    """Verifies that sync_vault handles multiple mutations for the same ID within a single batch."""
    bucket = "3" * 64
    entry_id = "dup-item-sync-batch"

    res = client.post(
        "/api/vault/sync",
        json={
            "bucket_id": bucket,
            "since_revision": 0,
            "mutations": [
                {
                    "id": entry_id,
                    "ciphertext": "sv-vault-v1:first_version",
                    "revision": 1,
                    "is_deleted": False,
                },
                {
                    "id": entry_id,
                    "ciphertext": "sv-vault-v1:second_version_in_same_batch",
                    "revision": 2,
                    "is_deleted": False,
                },
            ],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["server_revision"] == 2
    assert len(data["entries"]) == 1
    assert data["entries"][0]["id"] == entry_id
    assert data["entries"][0]["ciphertext"] == "sv-vault-v1:second_version_in_same_batch"


def test_vault_sync_exception_sanitization(client, monkeypatch):
    """Verifies that unexpected server-side sync exceptions are logged and return generic 500 without leaking SQL/internals."""
    from services import vault_service

    def mock_sync_error(*args, **kwargs):
        raise RuntimeError("SELECT secret_table_internal FROM secrets WHERE token='super-secret-token'")

    monkeypatch.setattr(vault_service, "sync_vault", mock_sync_error)

    bucket = "4" * 64
    res = client.post(
        "/api/vault/sync",
        json={"bucket_id": bucket, "since_revision": 0, "mutations": []},
    )
    assert res.status_code == 500
    data = res.json()
    assert data["detail"] == "Interner Fehler bei der Tresor-Synchronisation."
    # Crucially ensure internal SQL/tokens are NEVER in the response body
    assert "super-secret-token" not in str(data)
    assert "SELECT secret_table_internal" not in str(data)


def test_vault_schema_boundary_limits(client):
    """Verifies that requests exceeding mutation batch size (100) or MAX_SAFE_INTEGER revision are rejected with 422."""
    bucket = "5" * 64

    # 1. More than 100 mutations rejected with 422
    too_many_mutations = [
        {
            "id": f"item-{i}",
            "ciphertext": f"sv-vault-v1:cipher_{i}",
            "revision": 1,
            "is_deleted": False,
        }
        for i in range(101)
    ]
    res_mut = client.post(
        "/api/vault/sync",
        json={"bucket_id": bucket, "since_revision": 0, "mutations": too_many_mutations},
    )
    assert res_mut.status_code == 422

    # Exactly 100 mutations accepted
    res_100 = client.post(
        "/api/vault/sync",
        json={"bucket_id": bucket, "since_revision": 0, "mutations": too_many_mutations[:100]},
    )
    assert res_100.status_code == 200

    # 2. since_revision > MAX_SAFE_INTEGER (9007199254740991) rejected with 422
    res_rev = client.post(
        "/api/vault/sync",
        json={
            "bucket_id": bucket,
            "since_revision": 9007199254740992,
            "mutations": [],
        },
    )
    assert res_rev.status_code == 422

    # since_revision == MAX_SAFE_INTEGER is accepted
    res_safe_max = client.post(
        "/api/vault/sync",
        json={
            "bucket_id": bucket,
            "since_revision": 9007199254740991,
            "mutations": [],
        },
    )
    assert res_safe_max.status_code == 200

    # Test same schema boundaries on blind-sync
    token = "6" * 64
    r_blind_mut = client.post(
        "/api/vault/blind-sync",
        json={
            "bucket_id": bucket,
            "auth_token": token,
            "since_revision": 0,
            "mutations": too_many_mutations,
        },
    )
    assert r_blind_mut.status_code == 422

    r_blind_rev = client.post(
        "/api/vault/blind-sync",
        json={
            "bucket_id": bucket,
            "auth_token": token,
            "since_revision": 9007199254740992,
            "mutations": [],
        },
    )
    assert r_blind_rev.status_code == 422



