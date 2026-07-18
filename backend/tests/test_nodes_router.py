"""API tests for /api/nodes (owner-only, no token leakage)."""

from __future__ import annotations

from unittest.mock import patch
from io import BytesIO
import tarfile

import pytest
from fastapi.testclient import TestClient

from models import Node, NodeEnrollment, Server
from services.node_client import NodeClientError
from sqlalchemy.orm import Session


@pytest.fixture()
def owner_client(client: TestClient, owner_cookies: dict) -> tuple[TestClient, dict]:
    return client, owner_cookies


def test_list_nodes_requires_auth(client: TestClient):
    r = client.get("/api/nodes")
    assert r.status_code in (401, 403)


def test_list_nodes_as_owner(client: TestClient, owner_cookies: dict):
    r = client.get("/api/nodes", cookies=owner_cookies)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_list_nodes_includes_live_metrics(db, client: TestClient, owner_cookies: dict):
    """Admin cards need metrics on list — not only after GET /nodes/{id}."""
    node = Node(
        name="Local Metrics",
        host="http://127.0.0.1:9000",
        auth_token_enc="enc",
        is_local=True,
        status="online",
        cpu_total=8.0,
        ram_total=16384,
        disk_total=102400,
        cpu_percent=12.5,
        ram_used=4 * 1024 * 1024 * 1024,
        disk_used=40 * 1024 * 1024 * 1024,
        docker_connected=True,
        agent_version="1.0.0",
        container_count=0,
    )
    db.add(node)
    db.commit()
    db.refresh(node)

    r = client.get("/api/nodes", cookies=owner_cookies)
    assert r.status_code == 200, r.text
    rows = r.json()
    match = next((row for row in rows if row["id"] == node.id), None)
    assert match is not None
    assert match["metrics"] is not None
    assert match["metrics"]["cpu_percent"] == 12.5
    assert match["metrics"]["ram_percent"] == 25.0
    assert match["cpu_total"] == 8.0
    assert match["status"] == "online"
    assert "auth_token" not in match
    assert "auth_token_enc" not in match


def test_create_and_list_node(client: TestClient, owner_cookies: dict):
    csrf = owner_cookies.get("__Secure-csrf_token") or owner_cookies.get("csrf") or ""
    with patch("services.node_service.encrypt_node_token", return_value="enc-token"), \
         patch("routers.nodes.encrypt_node_token", return_value="enc-token"):
        r = client.post(
            "/api/nodes",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
            json={
                "name": "Worker-1",
                "host": "https://10.0.0.5:9000",
                "auth_token": "super-secret-agent-token-32chars!!",
                "tls_fingerprint": "a" * 64,
            },
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Worker-1"
    assert body.get("tls_fingerprint") == "a" * 64
    assert "auth_token" not in body
    assert "auth_token_enc" not in body
    assert body["server_count"] == 0


def test_delete_node_blocked_when_servers_exist(db, client: TestClient, owner_cookies: dict):
    node = Node(name="Remote", host="http://r:9000", auth_token_enc="enc", is_local=False)
    db.add(node)
    db.commit()
    db.refresh(node)
    db.add(Server(name="s", game_type="t", install_dir="/tmp/x", node_id=node.id, status="stopped"))
    db.commit()

    csrf = owner_cookies.get("__Secure-csrf_token") or ""
    r = client.delete(
        f"/api/nodes/{node.id}",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "Server" in (r.json().get("detail") or "")


def test_node_out_never_includes_token_fields():
    from types import SimpleNamespace

    from services.node_service import node_out_dict

    node = SimpleNamespace(
        id=1,
        name="L",
        host="http://127.0.0.1:9000",
        is_local=True,
        status="online",
        cpu_total=4.0,
        ram_total=8192,
        disk_total=100000,
        last_heartbeat=None,
        servers=[],
        auth_token_enc="MUST-NOT-APPEAR",
    )
    out = node_out_dict(node, server_count=3)
    assert "auth_token" not in out
    assert "auth_token_enc" not in out
    assert out["server_count"] == 3


def test_install_command_uses_configured_api_url_and_contains_no_secret(
    client: TestClient,
    owner_cookies: dict,
):
    response = client.get("/api/nodes/install-command", cookies=owner_cookies)

    assert response.status_code == 200, response.text
    command = response.json()["command"]
    assert "http://localhost:3000/api/nodes/install.sh" in command
    assert "--panel http://localhost:3000" in command
    assert "token" not in command.lower()
    assert "claim" not in command.lower()


def test_install_command_uses_backend_origin_for_external_frontend(
    client: TestClient,
    owner_cookies: dict,
    monkeypatch,
):
    from config import settings

    monkeypatch.setattr(settings, "panel_url", "https://panel.vercel.app")
    monkeypatch.setattr(settings, "api_url", "https://api.example.com")
    response = client.get("/api/nodes/install-command", cookies=owner_cookies)

    assert response.status_code == 200, response.text
    command = response.json()["command"]
    assert "https://api.example.com/api/nodes/install.sh" in command
    assert "panel.vercel.app" not in command


def test_node_enrollment_requires_owner_approval_and_never_returns_agent_token(
    db,
    client: TestClient,
    owner_cookies: dict,
):
    agent_token = "agent-secret-that-must-never-be-returned-123456"
    with patch(
        "services.node_enrollment_service.encrypt_node_token",
        return_value="encrypted-agent-token",
    ):
        begin = client.post(
            "/api/nodes/enrollments/begin",
            headers={"X-Forwarded-For": "198.51.100.24"},
            json={
                "name": "Worker Enrollment",
                "agent_token": agent_token,
                "tls_fingerprint": "b" * 64,
                "port": 9000,
            },
        )

    assert begin.status_code == 201, begin.text
    begin_body = begin.json()
    assert agent_token not in begin.text
    assert begin_body["display_code"]
    assert len(begin_body["claim_secret"]) >= 32

    pending_without_auth = client.get("/api/nodes/enrollments/pending")
    assert pending_without_auth.status_code in (401, 403)

    pending = client.get("/api/nodes/enrollments/pending", cookies=owner_cookies)
    assert pending.status_code == 200, pending.text
    assert pending.json()[0]["host"] == "https://198.51.100.24:9000"
    assert "auth_token" not in pending.text
    from services import node_enrollment_service

    assert node_enrollment_service.find_by_claim(db, begin_body["claim_secret"]) is not None

    before_approval = client.post(
        "/api/nodes/enrollments/poll",
        headers={"Authorization": f"Bearer {begin_body['claim_secret']}"},
    )
    assert before_approval.status_code == 200, before_approval.text
    assert before_approval.json() == {"status": "pending", "node_id": None}

    csrf = owner_cookies.get("__Secure-csrf_token") or ""
    enrollment_id = pending.json()[0]["id"]
    with patch("routers.nodes.NodeClient.from_node") as node_client:
        node_client.return_value.metrics.return_value = {
            "cpu_count": 4,
            "ram_total_bytes": 8 * 1024 * 1024,
            "disk_total_bytes": 32 * 1024 * 1024,
        }
        approved = client.post(
            f"/api/nodes/enrollments/{enrollment_id}/approve",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )
    assert approved.status_code == 200, approved.text
    assert "auth_token" not in approved.text
    node_id = approved.json()["id"]

    after_approval = client.post(
        "/api/nodes/enrollments/poll",
        headers={"Authorization": f"Bearer {begin_body['claim_secret']}"},
    )
    assert after_approval.status_code == 200, after_approval.text
    assert after_approval.json() == {"status": "approved", "node_id": node_id}

    enrollment = db.query(NodeEnrollment).filter(NodeEnrollment.id == enrollment_id).one()
    assert enrollment.status == "claimed"
    assert enrollment.auth_token_enc == "claimed"
    node = db.query(Node).filter(Node.id == node_id).one()
    assert node.auth_token_enc == "encrypted-agent-token"
    assert node.status == "online"


def test_node_enrollment_approval_rolls_back_when_panel_cannot_reach_agent(
    db,
    client: TestClient,
    owner_cookies: dict,
):
    with patch(
        "services.node_enrollment_service.encrypt_node_token",
        return_value="encrypted-agent-token",
    ):
        begin = client.post(
            "/api/nodes/enrollments/begin",
            headers={"X-Forwarded-For": "198.51.100.25"},
            json={
                "name": "Offline Worker",
                "agent_token": "agent-secret-that-is-long-enough-123456",
                "tls_fingerprint": "d" * 64,
                "port": 9000,
            },
        )
    assert begin.status_code == 201
    enrollment = db.query(NodeEnrollment).filter_by(name="Offline Worker").one()
    csrf = owner_cookies.get("__Secure-csrf_token") or ""

    with patch(
        "routers.nodes.NodeClient.from_node",
        side_effect=NodeClientError("Agent not reachable"),
    ):
        response = client.post(
            f"/api/nodes/enrollments/{enrollment.id}/approve",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf},
        )

    assert response.status_code == 502
    db.expire_all()
    enrollment = db.query(NodeEnrollment).filter_by(name="Offline Worker").one()
    assert enrollment.status == "pending"
    assert enrollment.node_id is None
    assert db.query(Node).filter_by(name="Offline Worker").count() == 0


def test_node_enrollment_rejects_untrusted_source_ip(
    client: TestClient,
):
    response = client.post(
        "/api/nodes/enrollments/begin",
        headers={"X-Forwarded-For": "not-an-ip"},
        json={
            "name": "Broken",
            "agent_token": "x" * 40,
            "tls_fingerprint": "c" * 64,
            "port": 9000,
        },
    )
    assert response.status_code == 400


def test_agent_package_excludes_local_secrets_data_and_test_artifacts(client: TestClient):
    response = client.get("/api/nodes/agent-package")

    assert response.status_code == 200, response.text
    with tarfile.open(fileobj=BytesIO(response.content), mode="r:gz") as archive:
        names = archive.getnames()

    assert "msm-agent/main.py" in names
    assert "helper-scripts/install-msm-agent.sh" in names
    forbidden_parts = {
        ".env",
        ".dev",
        "venv",
        "servers",
        "postgres",
        "certs",
        "tests",
        "__pycache__",
        ".pytest_cache",
    }
    assert not any(forbidden_parts.intersection(name.split("/")) for name in names)
    assert not any(name.endswith((".pyc", ".db", ".sqlite", ".sqlite3")) for name in names)


def test_node_enrollment_already_enrolled_returns_node_id(
    client: TestClient,
    db: Session,
):
    from sqlalchemy.orm import Session
    # 1. Create an existing node
    node = Node(
        name="Already Registered",
        host="https://198.51.100.25:9000",
        auth_token_enc="encrypted-token",
        tls_fingerprint="d" * 64,
        is_local=False,
        status="online",
    )
    db.add(node)
    db.commit()

    # 2. Try to begin enrollment with same fingerprint
    with patch(
        "services.node_enrollment_service.encrypt_node_token",
        return_value="encrypted-agent-token",
    ):
        response = client.post(
            "/api/nodes/enrollments/begin",
            headers={"X-Forwarded-For": "198.51.100.25"},
            json={
                "name": "Different Name",
                "agent_token": "agent-secret-that-is-long-enough-123456",
                "tls_fingerprint": "d" * 64,
                "port": 9000,
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["already_enrolled"] is True
    assert data["node_id"] == node.id

    # Clean up the node
    db.delete(node)
    db.commit()
