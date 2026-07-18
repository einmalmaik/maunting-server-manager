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


def apply_agent_metrics(node: Node, metrics: dict[str, Any] | None) -> None:
    """Persist capacity totals from an agent /metrics payload (no secrets)."""
    if not metrics:
        return
    if metrics.get("cpu_count") is not None:
        try:
            node.cpu_total = float(metrics["cpu_count"])
        except (TypeError, ValueError):
            pass
    if metrics.get("ram_total_bytes") is not None:
        try:
            node.ram_total = int(metrics["ram_total_bytes"]) // (1024 * 1024)
        except (TypeError, ValueError):
            pass
    if metrics.get("disk_total_bytes") is not None:
        try:
            node.disk_total = int(metrics["disk_total_bytes"]) // (1024 * 1024)
        except (TypeError, ValueError):
            pass
    if metrics.get("cpu_percent") is not None:
        try:
            node.cpu_percent = float(metrics["cpu_percent"])
        except (TypeError, ValueError):
            pass
    if metrics.get("ram_used_bytes") is not None:
        try:
            node.ram_used = int(metrics["ram_used_bytes"]) // (1024 * 1024)
        except (TypeError, ValueError):
            pass
    if metrics.get("disk_used_bytes") is not None:
        try:
            node.disk_used = int(metrics["disk_used_bytes"]) // (1024 * 1024)
        except (TypeError, ValueError):
            pass
    if metrics.get("container_count") is not None:
        try:
            container_count = int(metrics["container_count"])
            if container_count >= 0:
                node.container_count = container_count
        except (TypeError, ValueError):
            pass
    if isinstance(metrics.get("docker_connected"), bool):
        node.docker_connected = metrics["docker_connected"]
    agent_version = metrics.get("agent_version")
    if isinstance(agent_version, str) and agent_version.strip():
        node.agent_version = agent_version.strip()[:50]


def probe_node_metrics(
    node: Node,
    *,
    timeout: float = 2.5,
    mark_status: bool = True,
) -> dict[str, Any] | None:
    """Best-effort live metrics from the agent. Never raises for admin list UI.

    Updates node.status / capacity / last_heartbeat on success when mark_status.
    """
    from datetime import datetime, timezone

    from services.node_client import NodeClient, NodeClientError

    try:
        client = NodeClient.from_node(node, timeout=timeout)
        metrics = client.metrics()
    except NodeClientError:
        if mark_status:
            node.status = "offline"
        return None
    except Exception:
        logger.exception("unexpected node metrics probe failure (node_id=%s)", node.id)
        if mark_status:
            node.status = "offline"
        return None

    if not isinstance(metrics, dict):
        metrics = {}
    if mark_status:
        node.status = "online"
        node.last_heartbeat = datetime.now(timezone.utc)
        apply_agent_metrics(node, metrics)
    return metrics


def node_out_dict(
    node: Node,
    server_count: int | None = None,
    *,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize Node for API without auth_token_enc."""
    count = server_count
    if count is None:
        try:
            count = len(node.servers) if node.servers is not None else 0
        except Exception:
            count = 0

    cpu_percent = getattr(node, "cpu_percent", None)
    if metrics is None and cpu_percent is not None:
        ram_total = getattr(node, "ram_total", None)
        disk_total = getattr(node, "disk_total", None)
        ram_used = getattr(node, "ram_used", None)
        disk_used = getattr(node, "disk_used", None)
        
        ram_total_bytes = (ram_total or 0) * 1024 * 1024
        disk_total_bytes = (disk_total or 0) * 1024 * 1024
        ram_used_bytes = (ram_used or 0) * 1024 * 1024
        disk_used_bytes = (disk_used or 0) * 1024 * 1024
        metrics = {
            "cpu_percent": cpu_percent,
            "ram_percent": (ram_used_bytes / ram_total_bytes * 100) if ram_total_bytes else 0.0,
            "ram_total_bytes": ram_total_bytes,
            "ram_used_bytes": ram_used_bytes,
            "disk_total_bytes": disk_total_bytes,
            "disk_used_bytes": disk_used_bytes,
            "disk_percent": (disk_used_bytes / disk_total_bytes * 100) if disk_total_bytes else 0.0,
            "agent_version": getattr(node, "agent_version", None),
            "docker_connected": getattr(node, "docker_connected", None),
            "container_count": getattr(node, "container_count", None),
        }

    out: dict[str, Any] = {
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
    if metrics is not None:
        out["metrics"] = metrics
    return out
