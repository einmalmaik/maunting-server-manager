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
    cpu_model = metrics.get("cpu_model")
    if isinstance(cpu_model, str):
        cleaned = cpu_model.strip()
        if cleaned:
            node.cpu_model = cleaned[:256]
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


def handle_node_probe_failure(
    db: Session | None,
    node: Node,
    now: datetime | None = None,
) -> None:
    """Handle a failed heartbeat probe with hysteresis/grace-period.

    If the node was previously 'online' and last_heartbeat is within the grace period,
    do not immediately flip node.status to 'offline' (to prevent false-positives
    during heavy I/O / SteamCMD downloads).
    Only mark offline if last_heartbeat is missing or older than the grace period.
    """
    from datetime import datetime, timezone
    from models import Server

    if now is None:
        now = datetime.now(timezone.utc)

    try:
        from config import settings
        raw_grace = getattr(settings, "node_heartbeat_grace_period_seconds", 60.0)
        grace_period = max(5.0, float(raw_grace))
    except Exception:
        grace_period = 60.0

    if node.last_heartbeat is not None:
        last_hb = node.last_heartbeat
        if last_hb.tzinfo is None:
            last_hb = last_hb.replace(tzinfo=timezone.utc)
        elapsed = (now - last_hb).total_seconds()
        if elapsed <= grace_period and node.status == "online":
            logger.info(
                "Node %s (%s) probe failed/timed out, but within grace period (%.1fs <= %.1fs). Retaining status.",
                node.id,
                node.name,
                elapsed,
                grace_period,
            )
            return

    node.status = "offline"
    if db is not None:
        try:
            db.query(Server).filter(Server.node_id == node.id).update(
                {
                    "guardian_observed_state": "unknown",
                    "guardian_container_status": "unknown",
                },
                synchronize_session=False,
            )
        except Exception:
            pass


def probe_node_metrics(
    node: Node,
    *,
    timeout: float = 5.0,
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
            handle_node_probe_failure(None, node)
        return None
    except Exception:
        logger.exception("unexpected node metrics probe failure (node_id=%s)", node.id)
        if mark_status:
            handle_node_probe_failure(None, node)
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
    ram_allocated_mb: int | None = None,
    disk_allocated_gb: int | None = None,
    disk_panel_used_mb: int | None = None,
) -> dict[str, Any]:
    """Serialize Node for API without auth_token_enc."""
    from services.node_capacity import allocatable_ram_mb, allocatable_disk_gb

    count = server_count
    if count is None:
        try:
            count = len(node.servers) if node.servers is not None else 0
        except Exception:
            count = 0

    allocated_ram = 0 if ram_allocated_mb is None else int(ram_allocated_mb)
    ram_allocatable = allocatable_ram_mb(node, allocated_ram)

    allocated_disk = 0 if disk_allocated_gb is None else int(disk_allocated_gb)
    disk_allocatable = allocatable_disk_gb(node, allocated_disk)
    panel_disk_used = 0 if disk_panel_used_mb is None else int(disk_panel_used_mb)

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
            "cpu_count": getattr(node, "cpu_total", None),
            "cpu_percent": cpu_percent,
            "cpu_model": getattr(node, "cpu_model", None),
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
        "cpu_model": getattr(node, "cpu_model", None),
        "ram_total": node.ram_total,
        "disk_total": node.disk_total,
        "disk_used": getattr(node, "disk_used", None),
        "last_heartbeat": node.last_heartbeat,
        "server_count": int(count or 0),
        "ram_allocated_mb": ram_allocated_mb if ram_allocated_mb is not None else allocated_ram,
        "ram_allocatable_mb": ram_allocatable,
        "disk_allocated_gb": allocated_disk,
        "disk_allocatable_gb": disk_allocatable,
        "disk_panel_used_mb": panel_disk_used,
    }
    if metrics is not None:
        # Prefer cached model when live metrics omit the field (older agents).
        if isinstance(metrics, dict) and not metrics.get("cpu_model"):
            cached_model = getattr(node, "cpu_model", None)
            if cached_model:
                metrics = {**metrics, "cpu_model": cached_model}
        out["metrics"] = metrics
    return out
