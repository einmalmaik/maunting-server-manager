"""API tests for /api/nodes (owner-only, no token leakage)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from models import Node, Server


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
