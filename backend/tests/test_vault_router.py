"""Integration tests for Vault REST Router & Zero-Knowledge Invariants."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import get_current_owner, get_current_user, get_db, verify_csrf
from main import app
from models import Node, User, VaultEntry


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
    session.commit()
    session.refresh(user)

    yield session, user
    session.close()


@pytest.fixture
def client(test_db):
    session, user = test_db

    def override_get_db():
        yield session

    def override_get_current_user():
        return user

    def override_get_current_owner():
        return user

    def override_verify_csrf():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_current_owner] = override_get_current_owner
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
    session, _ = test_db
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

    # 4. Out-of-order/older revision must NOT overwrite newer revision
    resp4 = client.post(
        "/api/vault/sync",
        json={
            "bucket_id": bucket,
            "since_revision": 0,
            "mutations": [
                {
                    "id": entry_id,
                    "ciphertext": "older_stale_ciphertext",
                    "revision": 1,
                    "is_deleted": False,
                }
            ],
        },
    )
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["entries"][0]["ciphertext"] == ciphertext_v2


def test_vault_node_assignment(client, test_db):
    session, _ = test_db

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
