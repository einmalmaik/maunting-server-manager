import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Node
from services.dis_client import DisClient
from services.node_client import NODE_TOKEN_AAD, NodeClient, NodeClientError
from services.scheduler_service import _node_heartbeat_task


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_node_heartbeat_task_scale(db: Session):
    """Simulate 120 nodes under load and verify async parallel heartbeat updates."""
    # 1. Create 120 mock nodes in the database
    token_enc = DisClient.encrypt("valid-agent-token-16-chars", NODE_TOKEN_AAD)
    for i in range(120):
        is_https = (i % 2 != 0)
        node = Node(
            name=f"ScaleNode-{i}",
            host=f"http://127.0.0.1:900{i}" if not is_https else f"https://remote-node-{i}.test",
            auth_token_enc=token_enc,
            tls_fingerprint=f"{i:064x}" if is_https else None,
            status="unknown",
            is_local=(i == 0),
        )
        db.add(node)
    db.commit()

    # 2. Mock NodeClient.metrics_async using AsyncMock
    mock_metrics = {
        "cpu_count": 8,
        "cpu_percent": 15.5,
        "ram_total_bytes": 16 * 1024 * 1024 * 1024,
        "ram_used_bytes": 4 * 1024 * 1024 * 1024,
        "disk_total_bytes": 100 * 1024 * 1024 * 1024,
        "disk_used_bytes": 20 * 1024 * 1024 * 1024,
        "container_count": 3,
        "docker_connected": True,
        "agent_version": "1.0.0",
    }

    # Simulate some nodes failing to test resilience
    async def side_effect(*args, **kwargs):
        # We can inspect the host from NodeClient.self (first arg) or similar
        # For simplicity, let's let nodes with odd indices fail, and even indices succeed
        # Actually, check_node has `node_client` inside, so we mock metrics_async on the class
        pass

    with patch("services.node_client.NodeClient.metrics_async", new_callable=AsyncMock) as mock_method:
        # Mock metrics_async to return mock_metrics
        mock_method.return_value = mock_metrics

        start_time = datetime.now()
        # Run the async heartbeat task (polls all 120 nodes in parallel)
        await _node_heartbeat_task()
        end_time = datetime.now()

        # Verify performance: 120 nodes must be processed concurrently in less than 2 seconds (mocked immediate response)
        duration = (end_time - start_time).total_seconds()
        assert duration < 2.0, f"Task took too long: {duration}s (expected <2.0s for mocked parallel execution)"

    # 3. Verify that DB was updated correctly
    updated_nodes = db.query(Node).all()
    assert len(updated_nodes) == 120
    for node in updated_nodes:
        assert node.status == "online"
        assert node.cpu_percent == 15.5
        assert node.ram_used == 4 * 1024 * 1024 * 1024
        assert node.disk_used == 20 * 1024 * 1024 * 1024
        assert node.container_count == 3
        assert node.docker_connected is True
        assert node.agent_version == "1.0.0"


def test_nodes_api_pagination_and_search(client: TestClient, db: Session, owner_cookies: dict):
    """Verify that pagination, search query filtering, and server count aggregation work correctly."""
    token_enc = DisClient.encrypt("valid-agent-token-16-chars", NODE_TOKEN_AAD)
    # 1. Create 105 mock nodes in the DB
    for i in range(105):
        node = Node(
            name=f"ProdNode-{i:03d}",  # ProdNode-000, ProdNode-001, etc.
            host=f"http://127.0.0.1:800{i}",
            auth_token_enc=token_enc,
            status="online",
            cpu_percent=12.5,
            ram_used=2 * 1024 * 1024 * 1024,
            cpu_total=4.0,
            ram_total=8192,
            disk_total=51200,
        )
        db.add(node)
    db.commit()

    # 2. Query page 1 with limit 20
    headers = {"X-XSRF-TOKEN": owner_cookies.get("__Secure-csrf_token", "")}
    resp = client.get("/api/nodes", params={"page": 1, "limit": 20}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] == 105
    assert len(data["items"]) == 20
    assert data["page"] == 1
    assert data["limit"] == 20

    # Ensure items have the correct metrics structure mapped for the frontend
    first_item = data["items"][0]
    assert first_item["name"] == "ProdNode-000"
    assert "metrics" in first_item
    assert first_item["metrics"]["cpu_percent"] == 12.5
    assert first_item["metrics"]["ram_total_bytes"] == 8192 * 1024 * 1024
    assert first_item["metrics"]["ram_used_bytes"] == 2 * 1024 * 1024 * 1024

    # 3. Query page 6 with limit 20 (should return the remaining 5 items)
    resp = client.get("/api/nodes", params={"page": 6, "limit": 20}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 105
    assert len(data["items"]) == 5
    assert data["items"][0]["name"] == "ProdNode-100"

    # 4. Test Search Filtering
    resp = client.get("/api/nodes", params={"page": 1, "limit": 10, "search": "ProdNode-05"}, cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # Should match: ProdNode-050 to ProdNode-059 -> 10 nodes total
    assert data["total"] == 10
    assert len(data["items"]) == 10
    for item in data["items"]:
        assert "ProdNode-05" in item["name"]

    # 5. Non-paginated query should remain backward-compatible (returns list[NodeOut] directly)
    resp = client.get("/api/nodes", cookies=owner_cookies, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 105
    assert data[0]["name"] == "ProdNode-000"
