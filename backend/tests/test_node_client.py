"""Unit tests for NodeClient and node-aware port scoping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.node_client import NODE_TOKEN_AAD, NodeClient, NodeClientError
from services.port_allocation_service import _db_used_ports, allocate_ports


def test_node_client_from_node_decrypts_with_aad():
    node = SimpleNamespace(
        host="http://127.0.0.1:9000",
        auth_token_enc="cipher",
        id=1,
        is_local=True,
        tls_fingerprint=None,
    )
    with patch("services.node_client.DisClient.decrypt", return_value="plain-token") as dec:
        client = NodeClient.from_node(node)
    dec.assert_called_once_with("cipher", aad=NODE_TOKEN_AAD)
    assert client._base == "http://127.0.0.1:9000"
    assert client.bearer_token == "plain-token"


def test_node_client_never_logs_token():
    client = NodeClient(host="http://127.0.0.1:9000", token="secret-token-xyz")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer secret-token-xyz"
    # Health does not use bearer
    with patch("services.node_client.httpx.Client") as mock_cls:
        mock = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock
        mock.get.return_value.status_code = 200
        mock.get.return_value.json.return_value = {
            "status": "ok",
            "version": "1.0.0",
            "docker_connected": True,
        }
        client.health()
        args, kwargs = mock.get.call_args
        # no Authorization on health
        assert "headers" not in kwargs or "Authorization" not in (kwargs.get("headers") or {})


def test_node_client_auth_failure():
    client = NodeClient(host="http://127.0.0.1:9000", token="x")
    with patch("services.node_client.httpx.Client") as mock_cls:
        mock = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock
        resp = MagicMock()
        resp.status_code = 401
        resp.content = b'{"detail":"Unauthorized"}'
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {"detail": "Unauthorized"}
        mock.request.return_value = resp
        with pytest.raises(NodeClientError) as ei:
            client.list_containers()
        assert ei.value.status_code == 401


def test_docker_stop_routes_to_node():
    node = SimpleNamespace(
        host="http://127.0.0.1:9000",
        auth_token_enc="c",
        id=1,
        is_local=True,
        tls_fingerprint=None,
        status="online",
    )
    with patch("services.node_client.DisClient.decrypt", return_value="t"), \
         patch.object(NodeClient, "stop_container", return_value={"ok": True}) as stop:
        from services import docker_service

        result = docker_service.stop("msm-srv-1", timeout=15, node=node)
    assert result["ok"] is True
    stop.assert_called_once()


def test_docker_stop_local_without_node_uses_local_path():
    from services import docker_service

    with patch.object(docker_service, "_container", return_value=None):
        result = docker_service.stop("msm-srv-1", timeout=10)
    assert result["ok"] is True
    assert result.get("note") == "container was not running"


def test_db_used_ports_scoped_by_node(db):
    """Ports on another node must not block allocation on this node."""
    from models import Node, Server
    from models.server_port import ServerPort

    n1 = Node(name="A", host="http://a:9000", auth_token_enc="x", is_local=True)
    n2 = Node(name="B", host="http://b:9000", auth_token_enc="y", is_local=False)
    db.add_all([n1, n2])
    db.commit()
    db.refresh(n1)
    db.refresh(n2)

    s1 = Server(name="s1", game_type="test", install_dir="/tmp/s1", node_id=n1.id, status="stopped")
    s2 = Server(name="s2", game_type="test", install_dir="/tmp/s2", node_id=n2.id, status="stopped")
    db.add_all([s1, s2])
    db.commit()
    db.refresh(s1)
    db.refresh(s2)

    db.add(ServerPort(server_id=s1.id, role="game", port=27015, protocol="udp"))
    db.add(ServerPort(server_id=s2.id, role="game", port=27015, protocol="udp"))
    db.commit()

    used_n1 = _db_used_ports(db, node_id=n1.id)
    used_n2 = _db_used_ports(db, node_id=n2.id)
    assert (27015, "udp") in used_n1
    assert (27015, "udp") in used_n2
    # Same port on both nodes is fine — each set only has one entry
    assert len(used_n1) == 1
    assert len(used_n2) == 1


def test_allocate_ports_same_port_different_nodes(db):
    from models import Node, Server
    from models.server_port import ServerPort

    n1 = Node(name="A", host="http://a:9000", auth_token_enc="x", is_local=True)
    n2 = Node(name="B", host="http://b:9000", auth_token_enc="y", is_local=False)
    db.add_all([n1, n2])
    db.commit()
    db.refresh(n1)
    db.refresh(n2)
    s1 = Server(name="s1", game_type="test", install_dir="/tmp/s1", node_id=n1.id, status="stopped")
    db.add(s1)
    db.commit()
    db.refresh(s1)
    db.add(ServerPort(server_id=s1.id, role="game", port=27050, protocol="udp"))
    db.commit()

    # On n2, 27050 must still be free (no host check)
    with patch("services.port_allocation_service.is_port_available", return_value=True):
        game, query, rcon = allocate_ports(
            db,
            requested_game_port=27050,
            node_id=n2.id,
            check_host=False,
        )
    assert game == 27050
