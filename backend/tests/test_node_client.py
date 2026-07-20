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


def test_node_client_rejects_healthy_http_when_docker_is_down():
    client = NodeClient(host="http://127.0.0.1:9000", token="synthetic")
    with patch("services.node_client.httpx.Client") as mock_cls:
        mock = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock
        mock.get.return_value.status_code = 200
        mock.get.return_value.json.return_value = {
            "status": "degraded",
            "version": "test",
            "docker_connected": False,
        }
        with pytest.raises(NodeClientError, match="Docker runtime"):
            client.health()


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


def test_remote_container_payload_preserves_runtime_contract():
    from services import docker_service

    node = SimpleNamespace(id=2)
    client = MagicMock()
    client.create_container.return_value = {"ok": True, "id": "abc123"}
    with patch.object(NodeClient, "from_node", return_value=client):
        result = docker_service.run_container(
            name="msm-srv-42",
            image="example.invalid/runtime:test",
            read_only_rootfs=True,
            tmpfs_paths=["/tmp"],
            extra_networks=["msm-managed-postgres"],
            tty=True,
            restart_policy_name="on-failure",
            startup_check_seconds=2,
            node=node,
        )

    assert result["ok"] is True
    body = client.create_container.call_args.args[0]
    assert body["read_only_rootfs"] is True
    assert body["tmpfs_paths"] == ["/tmp"]
    assert body["extra_networks"] == ["msm-managed-postgres"]
    assert body["tty"] is True
    assert body["restart_policy_name"] == "on-failure"
    assert body["startup_check_seconds"] == 2


def test_remote_ephemeral_container_runs_on_selected_node():
    from services import docker_service
    from services.docker_service import VolumeBind

    node = SimpleNamespace(id=2)
    client = MagicMock()
    client.run_ephemeral_container.return_value = {
        "ok": True,
        "stdout": "installed\n",
        "stderr": "",
    }
    logs: list[str] = []
    with patch.object(NodeClient, "from_node", return_value=client):
        result = docker_service.run_ephemeral(
            image="example.invalid/tool:test",
            command=["install"],
            volumes=[VolumeBind("/opt/msm/servers/42", "/data", read_only=False)],
            cap_adds=["CHOWN"],
            log_callback=logs.append,
            node=node,
        )

    assert result["ok"] is True
    body = client.run_ephemeral_container.call_args.args[0]
    assert body["volumes"]["/opt/msm/servers/42"] == {"bind": "/data", "mode": "rw"}
    assert body["cap_add"] == ["CHOWN"]
    assert logs == ["installed\n"]


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
    # Same port on both nodes is fine ÔÇö each set only has one entry
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

def test_sync_invalid_json_raises_node_client_error():
    client = NodeClient(host="http://127.0.0.1:9000", token="synthetic")
    with patch("services.node_client.get_shared_sync_client") as mock_get:
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock.__aenter__.return_value = mock
        mock_get.return_value = mock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"not json"
        mock_resp.json.side_effect = Exception("decode error")
        mock.request.return_value = mock_resp
        
        with pytest.raises(NodeClientError) as exc_info:
            client.list_containers()
        
        assert exc_info.value.code == "node_invalid_json_response"
        assert exc_info.value.data["status_code"] == 200
        assert exc_info.value.data["response_snippet"] == "not json"

@pytest.mark.asyncio
async def test_async_invalid_json_raises_node_client_error():
    client = NodeClient(host="http://127.0.0.1:9000", token="synthetic")
    with patch("services.node_client.get_shared_async_client") as mock_get:
        from unittest.mock import AsyncMock
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock.__aenter__.return_value = mock
        mock_get.return_value = mock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"broken async json"
        mock_resp.json.side_effect = Exception("decode error")
        mock.request = AsyncMock(return_value=mock_resp)
        
        with pytest.raises(NodeClientError) as exc_info:
            await client.metrics_async()
        
        assert exc_info.value.code == "node_invalid_json_response"
        assert exc_info.value.data["response_snippet"] == "broken async json"

def test_204_response_is_allowed():
    client = NodeClient(host="http://127.0.0.1:9000", token="synthetic")
    with patch("services.node_client.get_shared_sync_client") as mock_get:
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock_get.return_value = mock
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.content = b""
        mock.request.return_value = mock_resp
        
        # 204 should return {} without raising JSON error
        assert client.metrics() == {}

def test_empty_json_response_is_rejected_when_body_required():
    client = NodeClient(host="http://127.0.0.1:9000", token="synthetic")
    with patch("services.node_client.get_shared_sync_client") as mock_get:
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock.__aenter__.return_value = mock
        mock_get.return_value = mock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        mock.request.return_value = mock_resp
        
        with pytest.raises(NodeClientError) as exc_info:
            client.list_containers()
            
        assert exc_info.value.code == "node_invalid_json_response"
        assert exc_info.value.message == "Agent returned empty response when JSON was expected"

def test_invalid_json_error_does_not_expose_auth_token():
    client = NodeClient(host="http://127.0.0.1:9000", token="super-secret-token-123")
    with patch("services.node_client.get_shared_sync_client") as mock_get:
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock.__aenter__.return_value = mock
        mock_get.return_value = mock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"invalid payload data"
        mock_resp.json.side_effect = Exception("decode error")
        mock.request.return_value = mock_resp
        
        with pytest.raises(NodeClientError) as exc_info:
            client.list_containers()
            
        err_str = str(exc_info.value)
        data_str = str(exc_info.value.data)
        assert "super-secret-token" not in err_str
        assert "super-secret-token" not in data_str
