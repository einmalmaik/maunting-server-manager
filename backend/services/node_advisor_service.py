from __future__ import annotations

from sqlalchemy.orm import Session

from models import Node
from services.node_capacity import allocatable_ram_mb, allocatable_disk_gb, sum_allocated_ram_mb, sum_allocated_disk_gb, sum_running_ram_mb


def estimate_ram_for_modpack(mod_count: int | None, base_mb: int = 2048) -> int:
    if not mod_count or mod_count <= 0:
        return base_mb
    return min(16384, base_mb + int(mod_count * 280))


def advise_node(db: Session, ram_need_mb: int, disk_need_gb: int = 5) -> list[dict]:
    nodes: list[Node] = db.query(Node).all()
    scored: list[dict] = []
    for n in nodes:
        if n.status not in (None, "online", "healthy"):
            continue
        alloc_ram = sum_allocated_ram_mb(db, n.id)
        alloc_disk = sum_allocated_disk_gb(db, n.id)
        avail_ram = allocatable_ram_mb(n, alloc_ram)
        avail_disk = allocatable_disk_gb(n, alloc_disk)
        running_ram = sum_running_ram_mb(db, n.id)
        score = (avail_ram if avail_ram is not None else 999999) + (avail_disk * 1024 if avail_disk is not None else 0)
        scored.append(
            {
                "node_id": n.id,
                "name": n.name,
                "status": n.status,
                "ram_total": n.ram_total,
                "ram_allocated_mb": alloc_ram,
                "ram_running_mb": running_ram,
                "ram_allocatable_mb": avail_ram,
                "disk_allocatable_gb": avail_disk,
                "fits": (avail_ram is None or avail_ram >= ram_need_mb) and (avail_disk is None or avail_disk >= disk_need_gb),
                "reason": f"frei {avail_ram}MB RAM / {avail_disk}GB Disk" if avail_ram is not None else "Kapazitaet unbekannt, Ueberbuchung moeglich",
            }
        )
    scored.sort(key=lambda x: (not x["fits"], -(x["ram_allocatable_mb"] or 0)))
    return scored
