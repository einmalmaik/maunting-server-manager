"""Phase 5: TLS fingerprint pinning, remote policy, heartbeat, offline guards."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.node_client import NodeClient, NodeClientError
from services.node_service import (
    NODE_OFFLINE_MSG,
    effective_server_runtime_status,
    ensure_node_online,
    is_node_offline,
    validate_remote_node_host,
)
from services.tls_pinning import normalize_fingerprint


def test_normalize_fingerprint_strips_colons():
    raw = "AB:CD:EF:12:34:56:78:90:ab:cd:ef:12:34:56:78:90:ab:cd:ef:12:34:56:78:90:ab:cd:ef:12:34:56:78:90"
    # pad to 64 hex for a realistic length after strip
    fp = "a" * 64
    assert normalize_fingerprint(fp) == fp
    assert normalize_fingerprint("sha256/" + fp) == fp
    assert normalize_fingerprint("AA:BB") == "aabb"


def test_remote_host_requires_https_and_fingerprint():
    with pytest.raises(ValueError, match="HTTPS"):
        validate_remote_node_host("http://10.0.0.1:9000", "a" * 64, is_local=False)
    with pytest.raises(ValueError, match="fingerprint"):
        validate_remote_node_host("https://10.0.0.1:9000", None, is_local=False)
    host = validate_remote_node_host("10.0.0.1:9000", "b" * 64, is_local=False)
    assert host.startswith("https://")


def test_local_host_allows_http_without_fingerprint():
    assert validate_remote_node_host("http://127.0.0.1:9000", None, is_local=True) == "http://127.0.0.1:9000"


def test_node_client_remote_requires_https_and_pin():
    with pytest.raises(NodeClientError, match="fingerprint"):
        NodeClient(
            host="https://agent.example:9000",
            token="x" * 20,
            require_tls_pin=True,
            tls_fingerprint=None,
        )
    with pytest.raises(NodeClientError, match="HTTPS"):
        NodeClient(
            host="http://agent.example:9000",
            token="x" * 20,
            require_tls_pin=True,
            tls_fingerprint="a" * 64,
        )


def test_node_client_pin_mismatch_raises(monkeypatch):
    def _boom(*_a, **_k):
        raise ValueError("TLS certificate fingerprint mismatch")

    monkeypatch.setattr(
        "services.node_client.build_pinned_ssl_context",
        _boom,
    )
    client = NodeClient(
        host="https://127.0.0.1:9443",
        token="x" * 20,
        tls_fingerprint="c" * 64,
        require_tls_pin=True,
    )
    with pytest.raises(NodeClientError, match="fingerprint"):
        client.health()


def test_offline_guard():
    node = SimpleNamespace(status="offline")
    assert is_node_offline(node) is True
    with pytest.raises(NodeClientError) as ei:
        ensure_node_online(node)
    assert ei.value.status_code == 503
    assert NODE_OFFLINE_MSG in ei.value.message

    server = SimpleNamespace(status="running")
    assert effective_server_runtime_status(server, node) == "node_unreachable"
    assert effective_server_runtime_status(server, SimpleNamespace(status="online")) == "running"


def test_heartbeat_marks_offline(db):
    from models import Node
    from services.scheduler_service import _node_heartbeat_task

    node = Node(
        name="Remote",
        host="https://127.0.0.1:19999",
        auth_token_enc="enc",
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
    )
    db.add(node)
    db.commit()
    db.refresh(node)

    with patch("services.node_client.DisClient.decrypt", return_value="tok"), \
         patch.object(NodeClient, "health", side_effect=NodeClientError("down")):
        _node_heartbeat_task()

    db.refresh(node)
    assert node.status == "offline"


def test_start_rejected_when_node_offline(
    client: TestClient, db, owner_cookies: dict, owner_user
):
    """Graceful degradation: start returns 503 when node is offline."""
    from models import Node, Server
    from services.dis_client import DisClient
    from services.node_client import NODE_TOKEN_AAD

    # Minimal offline remote node + server
    with patch.object(DisClient, "encrypt", return_value="enc-token"):
        node = Node(
            name="Off",
            host="https://10.0.0.9:9000",
            auth_token_enc="enc-token",
            tls_fingerprint="d" * 64,
            is_local=False,
            status="offline",
        )
    db.add(node)
    db.commit()
    db.refresh(node)
    server = Server(
        name="s-off",
        game_type="minecraft_paper",
        install_dir="/tmp/msm-test-soff",
        node_id=node.id,
        status="stopped",
        public_bind_ip="127.0.0.1",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    csrf = owner_cookies.get("__Secure-csrf_token")
    resp = client.post(
        f"/api/servers/{server.id}/start",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf or ""},
    )
    assert resp.status_code == 503
    assert "offline" in resp.json()["detail"].lower() or "nicht erreichbar" in resp.json()["detail"].lower()


def test_create_node_requires_fingerprint(client: TestClient, owner_cookies: dict):
    csrf = owner_cookies.get("__Secure-csrf_token")
    resp = client.post(
        "/api/nodes",
        json={
            "name": "NoPin",
            "host": "https://10.0.0.2:9000",
            "auth_token": "x" * 20,
        },
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf or ""},
    )
    assert resp.status_code == 400
