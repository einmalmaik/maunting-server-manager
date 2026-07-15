"""Node resolution helpers (KISS — no manager class)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from models import Node, Server
from services.node_client import NODE_TOKEN_AAD, NodeClient, NodeClientError
from services.dis_client import DisClient
from services.tls_pinning import normalize_fingerprint

logger = logging.getLogger(__name__)

NODE_OFFLINE_MSG = "Node ist offline oder nicht erreichbar"
NODE_UNREACHABLE_STATUS = "node_unreachable"


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


def is_node_offline(node: Node | None) -> bool:
    if node is None:
        return False
    return (node.status or "").lower() == "offline"


def ensure_node_online(node: Node | None) -> None:
    """Fail closed when heartbeat marked the node offline (graceful degradation)."""
    if is_node_offline(node):
        raise NodeClientError(NODE_OFFLINE_MSG, status_code=503)


def validate_remote_node_host(host: str, tls_fingerprint: str | None, *, is_local: bool) -> str:
    """Normalize host and enforce HTTPS+fingerprint for remote nodes."""
    host = (host or "").strip()
    if not host:
        raise ValueError("host ist erforderlich")
    if is_local:
        return host
    parsed = urlparse(host if "://" in host else f"https://{host}")
    scheme = (parsed.scheme or "https").lower()
    if scheme != "https":
        raise ValueError("Remote-Nodes erfordern HTTPS (Self-signed TLS + Fingerprint)")
    fp = normalize_fingerprint(tls_fingerprint)
    if not fp:
        raise ValueError("Remote-Nodes erfordern tls_fingerprint (SHA-256 des Agent-Zertifikats)")
    # Ensure scheme is stored as https
    if "://" not in host:
        host = f"https://{host}"
    elif not host.lower().startswith("https://"):
        host = "https://" + host.split("://", 1)[1]
    return host


def client_for_node(node: Node | None, *, skip_offline_check: bool = False) -> NodeClient | None:
    """Build NodeClient or None if node missing."""
    if node is None:
        return None
    if not skip_offline_check:
        ensure_node_online(node)
    return NodeClient.from_node(node)


def client_for_server(server: Server, db: Session | None = None) -> NodeClient | None:
    """NodeClient for server's node, or None when no node assigned.

    When no node is set (legacy/test fixtures), callers fall back to local
    docker_service / filesystem — keeps the existing test suite working.
    Offline remote nodes fail closed with NodeClientError.
    """
    node = resolve_server_node(server, db)
    if node is None:
        return None
    try:
        ensure_node_online(node)
        return NodeClient.from_node(node)
    except NodeClientError:
        # Remote nodes must fail closed; local may fall back to panel host
        # only when client construction fails (not when merely offline).
        if getattr(node, "is_local", False) and not is_node_offline(node):
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


def effective_server_runtime_status(server: Server, node: Node | None) -> str:
    """Dashboard status: keep server visible when node is down."""
    if is_node_offline(node):
        return NODE_UNREACHABLE_STATUS
    return getattr(server, "status", None) or "unknown"


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
        "tls_fingerprint": getattr(node, "tls_fingerprint", None) or None,
        "cpu_total": node.cpu_total,
        "ram_total": node.ram_total,
        "disk_total": node.disk_total,
        "last_heartbeat": node.last_heartbeat,
        "server_count": int(count or 0),
    }
