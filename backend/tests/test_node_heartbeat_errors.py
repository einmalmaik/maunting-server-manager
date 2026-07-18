import asyncio
import logging
from unittest.mock import patch

from models import Node
from services.node_client import NodeClientError
from services.scheduler_service import _node_heartbeat_task


def _remote_node(db, fingerprint: str) -> Node:
    node = Node(
        name="Synthetic heartbeat node",
        host="https://198.51.100.60:9000",
        auth_token_enc="synthetic-encrypted-token",
        tls_fingerprint=fingerprint,
        is_local=False,
        status="online",
        docker_connected=True,
    )
    db.add(node)
    db.commit()
    return node


def test_expected_heartbeat_error_marks_offline_without_fabricating_docker_state(
    db, caplog
):
    node = _remote_node(db, "3" * 64)
    with patch(
        "services.node_client.NodeClient.from_node",
        side_effect=NodeClientError("synthetic unavailable"),
    ):
        asyncio.run(_node_heartbeat_task())

    db.expire_all()
    refreshed = db.get(Node, node.id)
    assert refreshed.status == "offline"
    assert refreshed.docker_connected is True
    assert "unexpected node heartbeat failure" not in caplog.text


def test_unexpected_heartbeat_error_is_logged(db, caplog):
    node = _remote_node(db, "4" * 64)
    caplog.set_level(logging.ERROR, logger="services.scheduler_service")
    with patch(
        "services.node_client.NodeClient.from_node",
        side_effect=RuntimeError("synthetic programmer error"),
    ):
        asyncio.run(_node_heartbeat_task())

    db.expire_all()
    refreshed = db.get(Node, node.id)
    assert refreshed.status == "offline"
    assert refreshed.docker_connected is True
    assert "unexpected node heartbeat failure" in caplog.text
