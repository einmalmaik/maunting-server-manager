from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, ANY

import pytest
from sqlalchemy.orm import Session

from models import Node, Server
from services.guardian_reconciliation_service import reconcile_guardian_servers


def test_reconcile_guardian_servers_caches_clients(db: Session) -> None:
    # 1. Create a node
    node = Node(
        id=101,
        name="test-node-reconcile",
        host="http://127.0.0.1:9000",
        auth_token_enc="tok",
        status="online",
    )
    # 2. Create 2 servers assigned to that node
    srv1 = Server(
        id=201,
        name="srv-1",
        game_type="minecraft",
        install_dir="/tmp/1",
        status="stopped",
        node=node,
    )
    srv2 = Server(
        id=202,
        name="srv-2",
        game_type="minecraft",
        install_dir="/tmp/2",
        status="running",
        node=node,
    )
    db.add_all([node, srv1, srv2])
    db.commit()
    db.refresh(srv1)
    db.refresh(srv2)

    # Mock NodeClient.from_node to count constructions
    mock_client = MagicMock()
    with patch("services.guardian_reconciliation_service.NodeClient.from_node", return_value=mock_client) as mock_from_node, \
         patch("services.guardian_reconciliation_service.reconcile_guardian_server") as mock_reconcile:
         
        # Run reconciliation synchronously
        asyncio.run(reconcile_guardian_servers())
        
        # Verify NodeClient was only constructed ONCE for node 101, since it has been cached!
        mock_from_node.assert_called_once()
        called_node = mock_from_node.call_args.args[0]
        assert called_node.id == node.id
        assert mock_from_node.call_args.kwargs.get("timeout") == 5.0
        
        # Verify reconcile_guardian_server was called for both servers using the same client
        assert mock_reconcile.call_count == 2
        calls = mock_reconcile.call_args_list
        server_ids = {call.args[1].id for call in calls}
        assert server_ids == {201, 202}
        for call in calls:
            assert call.kwargs.get("node_client") is mock_client

@pytest.mark.asyncio
async def test_slow_node_does_not_block_other_servers(db: Session) -> None:
    # Set up two nodes with one server each
    n1 = Node(id=1, name="n1", host="http://1", auth_token_enc="tok", status="online")
    n2 = Node(id=2, name="n2", host="http://2", auth_token_enc="tok", status="online")
    s1 = Server(id=10, name="s1", game_type="mc", install_dir="/1", status="stopped", node=n1)
    s2 = Server(id=20, name="s2", game_type="mc", install_dir="/2", status="stopped", node=n2)
    db.add_all([n1, n2, s1, s2])
    db.commit()
    
    events = []
    
    def mock_reconcile(session, server, node_client):
        import time
        if server.id == 10:
            events.append("s1_start")
            time.sleep(0.1)
            events.append("s1_end")
        else:
            events.append("s2_start")
            events.append("s2_end")

    with patch("services.guardian_reconciliation_service.NodeClient.from_node"), \
         patch("services.guardian_reconciliation_service.reconcile_guardian_server", side_effect=mock_reconcile):
         
        await reconcile_guardian_servers()
        
    # Since they run in parallel threads, s2 should finish before s1 finishes its sleep
    # Events should look like: ["s1_start", "s2_start", "s2_end", "s1_end"] or ["s2_start", "s2_end", "s1_start", "s1_end"]
    assert "s2_end" in events
    assert events.index("s2_end") < events.index("s1_end")

@pytest.mark.asyncio
async def test_reconciliation_concurrency_is_bounded(db: Session) -> None:
    import services.guardian_reconciliation_service as grs
    nodes = []
    servers = []
    for i in range(15):
        n = Node(id=i+1, name=f"n{i}", host=f"http://{i}", auth_token_enc="tok", status="online")
        s = Server(id=i+100, name=f"s{i}", game_type="mc", install_dir=f"/{i}", status="stopped", node=n)
        nodes.append(n)
        servers.append(s)
        
    db.add_all(nodes + servers)
    db.commit()

    active_threads = 0
    max_active = 0
    
    def mock_reconcile(session, server, node_client):
        nonlocal active_threads, max_active
        import time
        active_threads += 1
        max_active = max(max_active, active_threads)
        time.sleep(0.02)
        active_threads -= 1

    with patch("services.guardian_reconciliation_service.NodeClient.from_node"), \
         patch("services.guardian_reconciliation_service.reconcile_guardian_server", side_effect=mock_reconcile), \
         patch("services.guardian_reconciliation_service._MAX_CONCURRENCY", 5):
         
        await reconcile_guardian_servers()
        
    assert max_active == 5

@pytest.mark.asyncio
async def test_each_worker_uses_separate_database_session(db: Session) -> None:
    n1 = Node(id=1, name="n1", host="http://1", auth_token_enc="tok", status="online")
    n2 = Node(id=2, name="n2", host="http://2", auth_token_enc="tok", status="online")
    s1 = Server(id=10, name="s1", game_type="mc", install_dir="/1", status="stopped", node=n1)
    s2 = Server(id=20, name="s2", game_type="mc", install_dir="/2", status="stopped", node=n2)
    db.add_all([n1, n2, s1, s2])
    db.commit()
    
    sessions_used = []
    
    def mock_reconcile(session, server, node_client):
        sessions_used.append(session)

    with patch("services.guardian_reconciliation_service.NodeClient.from_node"), \
         patch("services.guardian_reconciliation_service.reconcile_guardian_server", side_effect=mock_reconcile):
         
        await reconcile_guardian_servers()
        
    # Ensure that each worker thread received a distinct database session instance
    assert len(sessions_used) == 2
    assert sessions_used[0] is not sessions_used[1]

@pytest.mark.asyncio
async def test_one_server_failure_does_not_abort_other_servers(db: Session) -> None:
    n1 = Node(id=1, name="n1", host="http://1", auth_token_enc="tok", status="online")
    s1 = Server(id=10, name="s1", game_type="mc", install_dir="/1", status="stopped", node=n1)
    s2 = Server(id=20, name="s2", game_type="mc", install_dir="/2", status="stopped", node=n1)
    db.add_all([n1, s1, s2])
    db.commit()
    
    success_called = False
    
    def mock_reconcile(session, server, node_client):
        nonlocal success_called
        if server.id == 10:
            raise RuntimeError("Failing server 10")
        if server.id == 20:
            success_called = True

    with patch("services.guardian_reconciliation_service.NodeClient.from_node"), \
         patch("services.guardian_reconciliation_service.reconcile_guardian_server", side_effect=mock_reconcile):
         
        await reconcile_guardian_servers()
        
    # The second server should be reconciled even if the first one threw an exception
    assert success_called is True
