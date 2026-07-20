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
