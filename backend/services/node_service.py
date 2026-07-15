"""Node resolution helpers (KISS — no manager class)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from models import Node, Server
from services.node_client import NODE_TOKEN_AAD, NodeClient, NodeClientError
from services.dis_client import DisClient

logger = logging.getLogger(__name__)


def encrypt_node_token(raw_token: str) -> str:
    """Encrypt agent bearer token for DB storage. Never log raw_token."""
    return DisClient.encrypt(raw_token, aad=NODE_TOKEN_AAD)


def get_node(db: Session, node_id: int) -> Node | None:
    return db.query(Node).filter(Node.id == node_id).first()


def get_local_node(db: Session) -> Node | None:
    return db.query(Node).filter(Node.is_local.is_(True)).first()


def resolve_server_node(server: Server, db: Session | None = None) -> Node | None:
    """Return the Node for a server (relationship or node_id lookup)."""
    node = getattr(server, "node", None)
    if node is not None:
        return node
    node_id = getattr(server, "node_id", None)
    if node_id is None or db is None:
        return None
    return get_node(db, int(node_id))


def client_for_node(node: Node | None) -> NodeClient | None:
    """Build NodeClient or None if node missing."""
    if node is None:
        return None
    return NodeClient.from_node(node)


def client_for_server(server: Server, db: Session | None = None) -> NodeClient | None:
    """NodeClient for server's node, or None when no node assigned.

    When no node is set (legacy/test fixtures), callers fall back to local
    docker_service / filesystem — keeps the existing test suite working.
    """
    node = resolve_server_node(server, db)
    if node is None:
        return None
    try:
        return NodeClient.from_node(node)
    except NodeClientError:
        # Remote nodes must fail closed; local may fall back to panel host.
        if getattr(node, "is_local", False):
            logger.warning("local node client unavailable, falling back to panel host")
            return None
        raise


def uses_agent(server: Server, db: Session | None = None) -> bool:
    """True when operations for this server must go through the agent.

    Remote nodes always use the agent. Local node uses agent when the
    client can be constructed (token decrypt + host set); otherwise local
    panel paths remain for single-host / tests without a running agent.
    """
    node = resolve_server_node(server, db)
    if node is None:
        return False
    if not getattr(node, "is_local", False):
        return True
    try:
        NodeClient.from_node(node)
        return True
    except NodeClientError:
        return False


def node_out_dict(node: Node, server_count: int | None = None) -> dict[str, Any]:
    """Serialize Node for API without auth_token_enc."""
    count = server_count
    if count is None:
        try:
            count = len(node.servers) if node.servers is not None else 0
        except Exception:
            count = 0
    return {
        "id": node.id,
        "name": node.name,
        "host": node.host,
        "is_local": bool(node.is_local),
        "status": node.status or "unknown",
        "cpu_total": node.cpu_total,
        "ram_total": node.ram_total,
        "disk_total": node.disk_total,
        "last_heartbeat": node.last_heartbeat,
        "server_count": int(count or 0),
    }
