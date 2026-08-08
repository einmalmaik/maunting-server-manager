"""Node RAM capacity accounting (booked limits vs host total).

KISS: pure functions + SQL SUM. No manager classes, no agent round-trips.
CPU overcommit is intentional and not guarded here.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from models import Node, Server

# Reserved for OS / agent / docker when checking allocatable RAM.
# Overridable via MSM_NODE_RAM_HEADROOM_MB.
DEFAULT_RAM_HEADROOM_MB = 1024


def ram_headroom_mb() -> int:
    raw = getattr(settings, "node_ram_headroom_mb", DEFAULT_RAM_HEADROOM_MB)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RAM_HEADROOM_MB
    return max(0, value)


def sum_allocated_ram_mb(
    db: Session,
    node_id: int,
    *,
    exclude_server_id: int | None = None,
) -> int:
    """Sum of non-null server.ram_limit_mb on the node (booked RAM)."""
    query = db.query(func.coalesce(func.sum(Server.ram_limit_mb), 0)).filter(
        Server.node_id == node_id,
        Server.ram_limit_mb.isnot(None),
    )
    if exclude_server_id is not None:
        query = query.filter(Server.id != exclude_server_id)
    total = query.scalar()
    try:
        return int(total or 0)
    except (TypeError, ValueError):
        return 0


def allocated_ram_by_node_ids(db: Session, node_ids: list[int]) -> dict[int, int]:
    """Batch SUM(ram_limit_mb) grouped by node_id."""
    if not node_ids:
        return {}
    rows = (
        db.query(Server.node_id, func.coalesce(func.sum(Server.ram_limit_mb), 0))
        .filter(
            Server.node_id.in_(node_ids),
            Server.ram_limit_mb.isnot(None),
        )
        .group_by(Server.node_id)
        .all()
    )
    out: dict[int, int] = {nid: 0 for nid in node_ids}
    for node_id, total in rows:
        if node_id is None:
            continue
        try:
            out[int(node_id)] = int(total or 0)
        except (TypeError, ValueError):
            out[int(node_id)] = 0
    return out


def allocatable_ram_mb(node: Node, allocated_mb: int) -> int | None:
    """Remaining bookable RAM after headroom, or None if host total unknown."""
    if node.ram_total is None:
        return None
    try:
        total = int(node.ram_total)
    except (TypeError, ValueError):
        return None
    budget = max(0, total - ram_headroom_mb())
    return max(0, budget - max(0, int(allocated_mb)))


def ensure_ram_limit_fits(
    db: Session,
    node: Node | None,
    *,
    new_ram_limit_mb: int | None,
    exclude_server_id: int | None = None,
) -> None:
    """RAM overcommit is allowed by default. Soft accounting check.

    Skip blocking so users can assign RAM limits even if total booked limits
    exceed host capacity (overcommit is handled via UI warning modal).
    """
    return


# ── Disk capacity accounting ───────────────────────────────────────────────


def sum_allocated_disk_gb(
    db: Session,
    node_id: int,
    *,
    exclude_server_id: int | None = None,
) -> int:
    """Sum of non-null server.disk_limit_gb on the node (booked disk limit in GB)."""
    query = db.query(func.coalesce(func.sum(Server.disk_limit_gb), 0)).filter(
        Server.node_id == node_id,
        Server.disk_limit_gb.isnot(None),
    )
    if exclude_server_id is not None:
        query = query.filter(Server.id != exclude_server_id)
    total = query.scalar()
    try:
        return int(total or 0)
    except (TypeError, ValueError):
        return 0


def allocated_disk_by_node_ids(db: Session, node_ids: list[int]) -> dict[int, int]:
    """Batch SUM(disk_limit_gb) grouped by node_id."""
    if not node_ids:
        return {}
    rows = (
        db.query(Server.node_id, func.coalesce(func.sum(Server.disk_limit_gb), 0))
        .filter(
            Server.node_id.in_(node_ids),
            Server.disk_limit_gb.isnot(None),
        )
        .group_by(Server.node_id)
        .all()
    )
    out: dict[int, int] = {nid: 0 for nid in node_ids}
    for node_id, total in rows:
        if node_id is None:
            continue
        try:
            out[int(node_id)] = int(total or 0)
        except (TypeError, ValueError):
            out[int(node_id)] = 0
    return out


def sum_panel_disk_used_mb(db: Session, node_id: int) -> int:
    """Sum of server.disk_usage_mb on the node (actual storage used by panel servers + DBs)."""
    total = (
        db.query(func.coalesce(func.sum(Server.disk_usage_mb), 0))
        .filter(
            Server.node_id == node_id,
            Server.disk_usage_mb.isnot(None),
        )
        .scalar()
    )
    try:
        return int(total or 0)
    except (TypeError, ValueError):
        return 0


def panel_disk_used_by_node_ids(db: Session, node_ids: list[int]) -> dict[int, int]:
    """Batch SUM(disk_usage_mb) grouped by node_id."""
    if not node_ids:
        return {}
    rows = (
        db.query(Server.node_id, func.coalesce(func.sum(Server.disk_usage_mb), 0))
        .filter(
            Server.node_id.in_(node_ids),
            Server.disk_usage_mb.isnot(None),
        )
        .group_by(Server.node_id)
        .all()
    )
    out: dict[int, int] = {nid: 0 for nid in node_ids}
    for node_id, total in rows:
        if node_id is None:
            continue
        try:
            out[int(node_id)] = int(total or 0)
        except (TypeError, ValueError):
            out[int(node_id)] = 0
    return out


def allocatable_disk_gb(node: Node, allocated_gb: int) -> int | None:
    """Remaining bookable disk limit (in GB), or None if host total unknown."""
    if node.disk_total is None:
        return None
    try:
        total_gb = int(node.disk_total) // 1024
    except (TypeError, ValueError):
        return None
    return max(0, total_gb - max(0, int(allocated_gb)))


