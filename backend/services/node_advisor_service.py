from __future__ import annotations

from sqlalchemy.orm import Session

from models import Node
from services.node_capacity import (
    allocatable_ram_mb,
    allocatable_disk_gb,
    sum_allocated_ram_mb,
    sum_allocated_disk_gb,
    sum_running_ram_mb,
    normalize_ram_mb,
)


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
        host_total_mb = normalize_ram_mb(n.ram_total)
        host_used_mb = normalize_ram_mb(n.ram_used)
        
        # Realer physischer Freiraum auf dem Host:
        if host_total_mb is not None:
            if host_used_mb is not None:
                real_free_ram = max(0, host_total_mb - host_used_mb)
            else:
                real_free_ram = max(0, host_total_mb - running_ram)
        else:
            real_free_ram = None
        
        # MSM erlaubt Überbuchung: Passt, wenn Platte reicht und physisch noch Platz ist (oder Overcommit aktiv)
        fits = (avail_disk is None or avail_disk >= disk_need_gb) and (
            avail_ram is None
            or avail_ram >= ram_need_mb
            or real_free_ram is None
            or real_free_ram >= ram_need_mb
            or n.status in (None, "online", "healthy")
        )
        
        reason_parts = []
        if host_used_mb is not None and host_total_mb:
            pct = round(host_used_mb / host_total_mb * 100)
            reason_parts.append(f"{real_free_ram}MB physisch frei ({pct}% Host-Auslastung)")
        elif real_free_ram is not None:
            reason_parts.append(f"{real_free_ram}MB physisch frei")
        if avail_ram is not None and avail_ram > 0:
            reason_parts.append(f"{avail_ram}MB ungebucht")
        if avail_disk is not None:
            reason_parts.append(f"{avail_disk}GB Disk frei")
            
        # Netzwerk-Interfaces & IPs der Node für Vergabe und DNS
        host_ifaces = []
        default_bind = None
        if n.is_local:
            try:
                from services import network_interfaces_service
                host_ifaces = [h.to_dict() for h in network_interfaces_service.list_host_interfaces()]
                default_bind = network_interfaces_service.default_bind_ip()
            except Exception:
                pass
        else:
            try:
                from services.node_client import NodeClient
                data = NodeClient.from_node(n, timeout=2.0).interfaces()
                host_ifaces = data.get("interfaces", []) if isinstance(data, dict) else []
                default_bind = data.get("default_bind_ip") if isinstance(data, dict) else None
            except Exception:
                pass
        
        public_ip = next(
            (iface["ip"] for iface in host_ifaces if isinstance(iface, dict) and not iface.get("is_private") and not iface.get("is_loopback") and not iface.get("is_link_local")),
            default_bind or n.host
        )

        scored.append(
            {
                "node_id": n.id,
                "name": n.name,
                "host": n.host,
                "status": n.status,
                "is_local": bool(n.is_local),
                "public_ip": public_ip,
                "default_bind_ip": default_bind or public_ip,
                "interfaces": host_ifaces,
                "ram_total_mb": host_total_mb,
                "ram_used_mb": host_used_mb,
                "ram_allocated_mb": alloc_ram,
                "ram_running_mb": running_ram,
                "ram_allocatable_mb": avail_ram,
                "ram_real_free_mb": real_free_ram,
                "disk_allocatable_gb": avail_disk,
                "overcommit_supported": True,
                "fits": fits,
                "reason": " / ".join(reason_parts) if reason_parts else "Kapazitaet bereit, Überbuchung erlaubt",
            }
        )
    scored.sort(key=lambda x: (not x["fits"], -(x["ram_real_free_mb"] or x["ram_allocatable_mb"] or 0)))
    return scored

