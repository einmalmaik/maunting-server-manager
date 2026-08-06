"""Node-Management API.

- GET list: Owner ODER ``servers.create`` (noetig fuer Create-Server Node-Picker).
- GET detail / mutations: Owner-only.
- Responses never include auth_token / auth_token_enc.
"""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
import ipaddress
from pathlib import Path
import shlex
import tarfile
import tempfile
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from limits import parse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from config import settings
from dependencies import get_current_owner, get_current_user, verify_csrf, require_global
from models import Node, NodeEnrollment, Server, User
from schemas.node import (
    NodeCapacitySummary,
    NodeCreate,
    NodeOut,
    NodePickerOut,
    NodeUpdate,
)
from schemas.node_enrollment import (
    EnrollmentBegin,
    EnrollmentBeginOut,
    EnrollmentPendingOut,
    EnrollmentPollOut,
)
from middleware.rate_limit import limiter
from services import node_enrollment_service
from services.node_capacity import (
    allocatable_ram_mb,
    allocated_ram_by_node_ids,
    sum_allocated_ram_mb,
    allocatable_disk_gb,
    allocated_disk_by_node_ids,
    panel_disk_used_by_node_ids,
    sum_allocated_disk_gb,
    sum_panel_disk_used_mb,
)
from services.node_client import NodeClient, NodeClientError
from services.node_service import (
    encrypt_node_token,
    node_out_dict,
    probe_node_metrics,
    apply_agent_metrics,
    validate_remote_node_host,
)
from services.permission_service import has_global_permission
from services import audit_service

router = APIRouter(prefix="/api/nodes", tags=["nodes"])
_enrollment_begin_limit = parse("5/minute")
_enrollment_poll_limit = parse("60/minute")


def _rate_limit(key: str, limit) -> None:
    if not limiter.limiter.hit(limit, key):
        raise HTTPException(status_code=429, detail="Zu viele Enrollment-Anfragen")


def _is_trusted_proxy(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback:
            return True
        if addr.version == 4:
            octets = addr.packed
            if octets[0] == 10:
                return True
            if octets[0] == 172 and 16 <= octets[1] <= 31:
                return True
            if octets[0] == 192 and octets[1] == 168:
                return True
        else:
            if (addr.packed[0] & 0xfe) == 0xfc:
                return True
            if addr.packed[0] == 0xfe and (addr.packed[1] & 0xc0) == 0x80:
                return True
        return False
    except ValueError:
        return False


def _source_ip(request: Request) -> str:
    direct = request.client.host if request.client else ""
    candidate = direct
    is_trusted = _is_trusted_proxy(direct) if direct else False

    if is_trusted:
        x_forwarded_for = request.headers.get("x-forwarded-for", "")
        if x_forwarded_for:
            candidate = x_forwarded_for.split(",", 1)[0].strip()
    elif settings.debug and direct == "testclient":
        candidate = request.headers.get("x-forwarded-for", "127.0.0.1").split(",", 1)[0].strip()
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Node-IP konnte nicht sicher erkannt werden") from exc
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


def _bearer_claim(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(status_code=401, detail="Enrollment-Claim fehlt")
    return value.strip()


def _can_list_nodes(db: Session, user: User) -> bool:
    if user.is_owner:
        return True
    return has_global_permission(db, user, "nodes.read") or has_global_permission(db, user, "servers.create")


def _can_read_node_details(db: Session, user: User) -> bool:
    return user.is_owner or has_global_permission(db, user, "nodes.read")


def _picker_out(
    node: Node,
    *,
    ram_allocated_mb: int | None = None,
    disk_allocated_gb: int | None = None,
) -> dict[str, Any]:
    ram_allocated = 0 if ram_allocated_mb is None else int(ram_allocated_mb)
    disk_allocated = 0 if disk_allocated_gb is None else int(disk_allocated_gb)
    return NodePickerOut(
        id=node.id,
        name=node.name,
        status=node.status or "unknown",
        cpu_total=node.cpu_total,
        ram_total=node.ram_total,
        ram_allocated_mb=ram_allocated,
        ram_allocatable_mb=allocatable_ram_mb(node, ram_allocated),
        disk_total=node.disk_total,
        disk_allocated_gb=disk_allocated,
        disk_allocatable_gb=allocatable_disk_gb(node, disk_allocated),
    ).model_dump()


@router.get("/capacity-summary", response_model=NodeCapacitySummary)
def capacity_summary(
    limit: int = 5,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Dashboard Top-N node capacity (cached metrics only — no agent fan-out).

    Auth: owner or ``nodes.read``. Never returns tokens or host URLs.
    """
    if not _can_read_node_details(db, user):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    limit = max(1, min(int(limit or 5), 25))
    total = db.query(func.count(Node.id)).scalar() or 0
    online = (
        db.query(func.count(Node.id)).filter(Node.status == "online").scalar() or 0
    )

    nodes = db.query(Node).order_by(Node.id.asc()).all()
    node_ids = [n.id for n in nodes]
    allocated_ram_map = allocated_ram_by_node_ids(db, node_ids)
    allocated_disk_map = allocated_disk_by_node_ids(db, node_ids)
    panel_disk_used_map = panel_disk_used_by_node_ids(db, node_ids)
    server_counts_raw = (
        db.query(Server.node_id, func.count(Server.id))
        .filter(Server.node_id.in_(node_ids))
        .group_by(Server.node_id)
        .all()
        if node_ids
        else []
    )
    server_counts = {node_id: count for node_id, count in server_counts_raw}

    def _sort_key(node: Node) -> tuple:
        status = (node.status or "unknown").lower()
        online_rank = 0 if status == "online" else 1
        ram_total = int(node.ram_total or 0)
        ram_used = int(node.ram_used or 0)
        allocated = int(allocated_ram_map.get(node.id, 0))
        used_ratio = (ram_used / ram_total) if ram_total > 0 else 0.0
        alloc_ratio = (allocated / ram_total) if ram_total > 0 else 0.0
        # Online first, then busiest (max of OS used vs booked), then name.
        heat = max(used_ratio, alloc_ratio)
        return (online_rank, -heat, (node.name or "").lower(), node.id)

    ranked = sorted(nodes, key=_sort_key)[:limit]
    items = []
    for n in ranked:
        ram_booked = int(allocated_ram_map.get(n.id, 0))
        disk_booked = int(allocated_disk_map.get(n.id, 0))
        panel_used = int(panel_disk_used_map.get(n.id, 0))
        items.append(
            {
                "id": n.id,
                "name": n.name,
                "status": n.status or "unknown",
                "cpu_model": getattr(n, "cpu_model", None),
                "cpu_total": n.cpu_total,
                "ram_total_mb": n.ram_total,
                "ram_used_mb": n.ram_used,
                "ram_allocated_mb": ram_booked,
                "ram_allocatable_mb": allocatable_ram_mb(n, ram_booked),
                "disk_total_mb": n.disk_total,
                "disk_used_mb": n.disk_used,
                "disk_allocated_gb": disk_booked,
                "disk_allocatable_gb": allocatable_disk_gb(n, disk_booked),
                "disk_panel_used_mb": panel_used,
                "server_count": int(server_counts.get(n.id, 0)),
            }
        )
    return {
        "online": int(online),
        "total": int(total),
        "items": items,
    }


@router.get("")
def list_nodes(
    page: int | None = None,
    limit: int | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    """List nodes for the create-server picker and admin UI.

    Auth: logged-in user with Owner OR global ``servers.create``.
    Never returns agent tokens.

    Uses cached live metrics from the database (updated by the background heartbeat job)
    for high scale, supporting pagination and search.
    """
    if not _can_list_nodes(db, user):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    can_read_details = _can_read_node_details(db, user)
    query = db.query(Node)
    if search:
        search_term = f"%{search}%"
        if can_read_details:
            query = query.filter(Node.name.ilike(search_term) | Node.host.ilike(search_term))
        else:
            query = query.filter(Node.name.ilike(search_term))

    query = query.order_by(Node.id.asc())

    if page is not None and limit is not None:
        page = max(1, page)
        limit = max(1, limit)
        total = query.count()
        nodes = query.offset((page - 1) * limit).limit(limit).all()
        result_page = {
            "items": [],
            "total": total,
            "page": page,
            "limit": limit,
        }
        node_ids = [node.id for node in nodes]
        allocated_ram_map = allocated_ram_by_node_ids(db, node_ids)
        allocated_disk_map = allocated_disk_by_node_ids(db, node_ids)
        panel_disk_used_map = panel_disk_used_by_node_ids(db, node_ids)
        if not can_read_details:
            result_page["items"] = [
                _picker_out(
                    node,
                    ram_allocated_mb=allocated_ram_map.get(node.id, 0),
                    disk_allocated_gb=allocated_disk_map.get(node.id, 0),
                )
                for node in nodes
            ]
            return result_page

        server_counts_raw = (
            db.query(Server.node_id, func.count(Server.id))
            .filter(Server.node_id.in_(node_ids))
            .group_by(Server.node_id)
            .all()
            if node_ids
            else []
        )
        server_counts = {node_id: count for node_id, count in server_counts_raw}
        result_page["items"] = [
            node_out_dict(
                node,
                server_count=server_counts.get(node.id, 0),
                ram_allocated_mb=allocated_ram_map.get(node.id, 0),
                disk_allocated_gb=allocated_disk_map.get(node.id, 0),
                disk_panel_used_mb=panel_disk_used_map.get(node.id, 0),
            )
            for node in nodes
        ]
        return result_page
    else:
        nodes = query.all()
        node_ids = [node.id for node in nodes]
        allocated_ram_map = allocated_ram_by_node_ids(db, node_ids)
        allocated_disk_map = allocated_disk_by_node_ids(db, node_ids)
        panel_disk_used_map = panel_disk_used_by_node_ids(db, node_ids)
        if not can_read_details:
            return [
                _picker_out(
                    node,
                    ram_allocated_mb=allocated_ram_map.get(node.id, 0),
                    disk_allocated_gb=allocated_disk_map.get(node.id, 0),
                )
                for node in nodes
            ]
        server_counts_raw = (
            db.query(Server.node_id, func.count(Server.id))
            .filter(Server.node_id.in_(node_ids))
            .group_by(Server.node_id)
            .all()
            if node_ids
            else []
        )
        server_counts = {node_id: count for node_id, count in server_counts_raw}
        return [
            node_out_dict(
                n,
                server_count=server_counts.get(n.id, 0),
                ram_allocated_mb=allocated_ram_map.get(n.id, 0),
                disk_allocated_gb=allocated_disk_map.get(n.id, 0),
                disk_panel_used_mb=panel_disk_used_map.get(n.id, 0),
            )
            for n in nodes
        ]


@router.post("", response_model=NodeOut, status_code=201)
def create_node(
    body: NodeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("nodes.manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    _ = user
    try:
        host = validate_remote_node_host(
            body.host, body.tls_fingerprint, is_local=False
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        token_enc = encrypt_node_token(body.auth_token)
    except Exception:
        raise HTTPException(status_code=503, detail="Token konnte nicht verschluesselt werden (DIS)")

    node = Node(
        name=body.name.strip(),
        host=host,
        auth_token_enc=token_enc,
        tls_fingerprint=body.tls_fingerprint,
        is_local=False,
        status="unknown",
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node_out_dict(node, server_count=0, ram_allocated_mb=0)


@router.get("/install-command")
def install_command(
    user: User = Depends(require_global("nodes.manage")),
) -> dict:
    _ = user
    origin = (settings.api_url or settings.panel_url).rstrip("/")
    parsed = urlparse(origin)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local_http:
        raise HTTPException(status_code=400, detail="Node-Installation erfordert HTTPS")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(status_code=500, detail="Panel-URL ist ungültig konfiguriert")
    quoted_origin = shlex.quote(origin)
    command = (
        f"curl -fsSL {quoted_origin}/api/nodes/install.sh | "
        f"sudo bash -s -- --panel {quoted_origin}"
    )
    return {"command": command}


@router.get("/install.sh", include_in_schema=False)
def node_installer_script() -> FileResponse:
    path = Path(__file__).resolve().parent.parent.parent / "helper-scripts" / "install-msm-node.sh"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Node-Installer ist nicht verfügbar")
    return FileResponse(path, media_type="text/x-shellscript", filename="install-node.sh")


@router.get("/agent-package", include_in_schema=False)
def node_agent_package(request: Request) -> FileResponse:
    _rate_limit(_source_ip(request), _enrollment_begin_limit)
    root = Path(__file__).resolve().parent.parent.parent
    agent_dir = root / "msm-agent"
    installer = root / "helper-scripts" / "install-msm-agent.sh"
    if not agent_dir.is_dir() or not installer.is_file():
        raise HTTPException(status_code=503, detail="Agent-Paket ist nicht verfügbar")

    fd, archive_name = tempfile.mkstemp(prefix="msm-agent-", suffix=".tar.gz")
    import os

    os.close(fd)
    excluded = {
        "venv",
        ".env",
        ".dev",
        "__pycache__",
        ".pytest_cache",
        "servers",
        "postgres",
        "certs",
        "tests",
    }

    def package_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if any(part in excluded for part in Path(info.name).parts):
            return None
        if info.name.endswith((".pyc", ".db", ".sqlite", ".sqlite3")):
            return None
        return info

    try:
        with tarfile.open(archive_name, "w:gz") as archive:
            archive.add(agent_dir, arcname="msm-agent", filter=package_filter)
            archive.add(installer, arcname="helper-scripts/install-msm-agent.sh", filter=package_filter)
    except Exception:
        Path(archive_name).unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="Agent-Paket konnte nicht erstellt werden")

    return FileResponse(
        archive_name,
        media_type="application/gzip",
        filename="msm-agent.tar.gz",
        background=BackgroundTask(Path(archive_name).unlink, missing_ok=True),
    )


@router.post("/enrollments/begin", response_model=EnrollmentBeginOut, status_code=201)
def begin_enrollment(
    body: EnrollmentBegin,
    request: Request,
    db: Session = Depends(get_db),
) -> EnrollmentBeginOut:
    source_ip = _source_ip(request)
    _rate_limit(source_ip, _enrollment_begin_limit)
    try:
        enrollment, claim_secret = node_enrollment_service.begin_enrollment(
            db,
            name=body.name,
            source_ip=source_ip,
            port=body.port,
            tls_fingerprint=body.tls_fingerprint,
            agent_token=body.agent_token,
        )
    except Exception:
        raise HTTPException(status_code=503, detail="Node-Enrollment konnte nicht angelegt werden")

    return EnrollmentBeginOut(
        claim_secret=claim_secret,
        display_code=enrollment.display_code,
        expires_at=enrollment.expires_at,
        already_enrolled=False,
    )


@router.post("/enrollments/poll", response_model=EnrollmentPollOut)
def poll_enrollment(
    request: Request,
    db: Session = Depends(get_db),
) -> EnrollmentPollOut:
    _rate_limit(_source_ip(request), _enrollment_poll_limit)
    enrollment = node_enrollment_service.find_by_claim(db, _bearer_claim(request))
    if enrollment is None or node_enrollment_service.is_expired(enrollment):
        raise HTTPException(status_code=404, detail="Enrollment nicht gefunden oder abgelaufen")
    status = enrollment.status
    node_id = enrollment.node_id
    if status == "approved":
        node_enrollment_service.mark_claimed(db, enrollment)
        status = "approved"
    return EnrollmentPollOut(status=status, node_id=node_id)


@router.get("/enrollments/pending", response_model=list[EnrollmentPendingOut])
def pending_enrollments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_owner),
) -> list[NodeEnrollment]:
    _ = user
    node_enrollment_service.cleanup_expired(db)
    return (
        db.query(NodeEnrollment)
        .filter(NodeEnrollment.status == "pending")
        .order_by(NodeEnrollment.created_at.asc())
        .all()
    )


@router.post("/enrollments/{enrollment_id}/approve", response_model=NodeOut)
def approve_enrollment(
    enrollment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_owner),
    _: None = Depends(verify_csrf),
) -> dict:
    _ = user
    enrollment = node_enrollment_service.lock_pending_for_approval(db, enrollment_id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment nicht gefunden")
    try:
        node = node_enrollment_service.approve(db, enrollment)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        metrics = NodeClient.from_node(node, timeout=5.0).metrics()
    except NodeClientError as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail=(
                "Agent ist vom Panel noch nicht erreichbar. "
                "Node-Firewall und öffentliche Erreichbarkeit prüfen."
            ),
        ) from exc

    node.status = "online"
    node.last_heartbeat = datetime.now(timezone.utc)
    apply_agent_metrics(node, metrics)
    enrollment.status = "approved"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="TLS-Fingerprint ist bereits einer anderen Node zugeordnet",
        ) from exc
    db.refresh(node)
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="nodes.enrollment.approve",
        target_type="node",
        target_id=node.id,
        details={"enrollment_id": enrollment_id, "node_name": node.name},
        commit=True,
    )
    return node_out_dict(node, server_count=0, ram_allocated_mb=0)


@router.get("/{node_id}", response_model=NodeOut)
def get_node(
    node_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("nodes.read")),
) -> dict:
    _ = user
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node nicht gefunden")
    count = db.query(Server).filter(Server.node_id == node.id).count()

    # Build client from node
    from services.node_client import NodeClient
    client = NodeClient.from_node(node, timeout=5.0)

    # Commit and close/release the current DB connection back to the pool during the slow HTTP call
    db.commit()
    db.close()

    metrics = None
    status = "offline"
    try:
        metrics = client.metrics()
        status = "online"
    except Exception:
        pass

    # Open a new short-lived DB transaction to apply the heartbeat/metric results
    from database import SessionLocal
    from datetime import datetime, timezone
    from services.node_service import apply_agent_metrics

    db_new = SessionLocal()
    try:
        node = db_new.query(Node).filter(Node.id == node_id).first()
        if node:
            node.status = status
            if status == "online":
                node.last_heartbeat = datetime.now(timezone.utc)
                if isinstance(metrics, dict):
                    apply_agent_metrics(node, metrics)
            db_new.commit()
            db_new.refresh(node)
            # Re-read count in the new session to return accurate serializable data
            count = db_new.query(Server).filter(Server.node_id == node.id).count()
            allocated_ram = sum_allocated_ram_mb(db_new, node.id)
            allocated_disk = sum_allocated_disk_gb(db_new, node.id)
            panel_disk_used = sum_panel_disk_used_mb(db_new, node.id)
            # Return serialized output
            return node_out_dict(
                node,
                server_count=count,
                metrics=metrics,
                ram_allocated_mb=allocated_ram,
                disk_allocated_gb=allocated_disk,
                disk_panel_used_mb=panel_disk_used,
            )
        else:
            raise HTTPException(status_code=404, detail="Node nicht gefunden")
    except Exception as exc:
        db_new.rollback()
        raise exc
    finally:
        db_new.close()


@router.put("/{node_id}", response_model=NodeOut)
def update_node(
    node_id: int,
    body: NodeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("nodes.manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    _ = user
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node nicht gefunden")

    if body.name is not None:
        node.name = body.name.strip()

    has_fp_field = "tls_fingerprint" in body.model_fields_set
    new_fp = body.tls_fingerprint if has_fp_field else node.tls_fingerprint
    new_host = body.host.strip() if body.host is not None else node.host
    if body.host is not None or has_fp_field:
        try:
            node.host = validate_remote_node_host(
                new_host, new_fp, is_local=bool(node.is_local)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if has_fp_field:
            node.tls_fingerprint = body.tls_fingerprint

    token_rotated = False
    if body.auth_token is not None:
        try:
            node.auth_token_enc = encrypt_node_token(body.auth_token)
            token_rotated = True
        except Exception:
            raise HTTPException(status_code=503, detail="Token konnte nicht verschluesselt werden (DIS)")

    db.commit()
    db.refresh(node)
    if token_rotated:
        # Token-Inhalt nie in details — nur dass rotiert wurde.
        audit_service.record_privileged_action(
            db,
            user_id=user.id,
            action="nodes.token.update",
            target_type="node",
            target_id=node.id,
            details={"node_name": node.name},
            commit=True,
        )
    count = db.query(Server).filter(Server.node_id == node.id).count()
    allocated = sum_allocated_ram_mb(db, node.id)
    return node_out_dict(node, server_count=count, ram_allocated_mb=allocated)


@router.delete("/{node_id}")
def delete_node(
    node_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("nodes.manage")),
    _: None = Depends(verify_csrf),
) -> dict:
    _ = user
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node nicht gefunden")
    if node.is_local:
        raise HTTPException(status_code=400, detail="Lokaler Default-Node kann nicht geloescht werden")
    count = db.query(Server).filter(Server.node_id == node.id).count()
    if count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Node hat noch {count} Server — zuerst Server verschieben oder loeschen",
        )
    db.delete(node)
    db.commit()
    return {"ok": True}


@router.get("/{node_id}/interfaces")
def node_interfaces(
    node_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not _can_list_nodes(db, user):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node nicht gefunden")

    if node.is_local:
        from services import network_interfaces_service
        interfaces = [h.to_dict() for h in network_interfaces_service.list_host_interfaces()]
        return {
            "interfaces": interfaces,
            "default_bind_ip": network_interfaces_service.default_bind_ip(),
        }

    try:
        return NodeClient.from_node(node, timeout=10.0).interfaces()
    except NodeClientError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Verbindung zum Agenten auf Node {node.name} fehlgeschlagen: {exc.message}",
        ) from exc
