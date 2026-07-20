from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from models import Node
from services.node_client import NodeClient


def _client() -> NodeClient:
    node = Node(
        id=1,
        name="test-node",
        host="http://127.0.0.1:9000",
        auth_token_enc="token",
        is_local=False,
        status="online",
    )
    # Mocking dis decryption
    with patch("services.node_client.DisClient.decrypt", return_value="decrypted-token"):
        return NodeClient.from_node(node)


def test_set_desired_state() -> None:
    client = _client()
    payload = {"some": "data"}
    
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"ok": True}
        res = client.set_desired_state("srv-1", payload)
        
        assert res == {"ok": True}
        mock_req.assert_called_once_with("POST", "/containers/srv-1/desired-state", json=payload)


def test_get_guardian_capabilities() -> None:
    client = _client()
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"guardian_schema_versions": [1]}
        res = client.get_guardian_capabilities()
        
        assert res == {"guardian_schema_versions": [1]}
        mock_req.assert_called_once_with("GET", "/guardian/capabilities")


def test_get_guardian_state() -> None:
    client = _client()
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"guardian_observed_state": "healthy"}
        res = client.get_guardian_state("srv-1")
        
        assert res == {"guardian_observed_state": "healthy"}
        mock_req.assert_called_once_with("GET", "/containers/srv-1/guardian-state")


def test_get_incidents() -> None:
    client = _client()
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = [{"uuid": "abc"}]
        res = client.get_incidents("srv-1")
        
        assert res == [{"uuid": "abc"}]
        mock_req.assert_called_once_with("GET", "/containers/srv-1/incidents")


def test_acknowledge_incidents_empty_noop() -> None:
    client = _client()
    with patch.object(client, "_request") as mock_req:
        res = client.acknowledge_incidents("srv-1", [])
        assert res == {}
        mock_req.assert_not_called()


def test_acknowledge_incidents_batching() -> None:
    client = _client()
    uuids = [f"uuid-{i}" for i in range(2500)]
    
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"status": "ok"}
        res = client.acknowledge_incidents("srv-1", uuids)
        
        assert res == {"status": "ok"}
        assert mock_req.call_count == 3
        
        # Check first batch: 0 to 1000
        mock_req.assert_any_call(
            "POST",
            "/containers/srv-1/incidents/acknowledge",
            json={"uuids": [f"uuid-{i}" for i in range(1000)]},
        )
        # Check second batch: 1000 to 2000
        mock_req.assert_any_call(
            "POST",
            "/containers/srv-1/incidents/acknowledge",
            json={"uuids": [f"uuid-{i}" for i in range(1000, 2000)]},
        )
        # Check third batch: 2000 to 2500
        mock_req.assert_any_call(
            "POST",
            "/containers/srv-1/incidents/acknowledge",
            json={"uuids": [f"uuid-{i}" for i in range(2000, 2500)]},
        )
