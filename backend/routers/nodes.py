"""Node-Management API.

- GET list: Owner ODER ``servers.create`` (noetig fuer Create-Server Node-Picker).
- GET detail / mutations: Owner-only.
- Responses never include auth_token / auth_token_enc.
"""

from __future__ import annotations

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
from sqlalchemy.orm import Session

from database import get_db
from config import settings
from dependencies import get_current_owner, get_current_user, verify_csrf
from models import Node, NodeEnrollment, Server, User
from schemas.node import NodeCreate, NodeOut, NodeUpdate
from schemas.node_enrollment import (
    EnrollmentBegin,
    EnrollmentBeginOut,
    EnrollmentPendingOut,
    EnrollmentPollOut,
)
from middleware.rate_limit import limiter
from services import node_enrollment_service
from services.node_client import NodeClient, NodeClientError
from services.node_service import encrypt_node_token, node_out_dict, validate_remote_node_host
from services.permission_service import has_global_permission

router = APIRouter(prefix="/api/nodes", tags=["nodes"])
_enrollment_begin_limit = parse("5/minute")
_enrollment_poll_limit = parse("60/minute")


def _rate_limit(request: Request, limit) -> None:
    key = request.client.host if request.client else "unknown"
    if not limiter.limiter.hit(limit, key):
        raise HTTPException(status_code=429, detail="Zu viele Enrollment-Anfragen")


def _source_ip(request: Request) -> str:
    direct = request.client.host if request.client else ""
    candidate = direct
    if direct in {"127.0.0.1", "::1"} or (settings.debug and direct == "testclient"):
        candidate = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
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
    return has_global_permission(db, user, "servers.create")


@router.get("", response_model=list[NodeOut])
def list_nodes(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """List nodes for the create-server picker and admin UI.

    Auth: logged-in user with Owner OR global ``servers.create``.
    Never returns agent tokens.
    """
    if not _can_list_nodes(db, user):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    nodes = db.query(Node).order_by(Node.id.asc()).all()
    out = []
    for n in nodes:
        count = db.query(Server).filter(Server.node_id == n.id).count()
        out.append(node_out_dict(n, server_count=count))
    return out


@router.post("", response_model=NodeOut, status_code=201)
def create_node(
    body: NodeCreate,
    db: Session = Depends(get_db),
    owner: User = Depends(get_current_owner),
    _: None = Depends(verify_csrf),
) -> dict:
    _ = owner
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
    return node_out_dict(node, server_count=0)


@router.get("/install-command")
def install_command(
    owner: User = Depends(get_current_owner),
) -> dict:
    _ = owner
    origin = settings.panel_url.rstrip("/")
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
    path = Path(__file__).resolve().parent.parent.parent / "scripts" / "install-node.sh"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Node-Installer ist nicht verfügbar")
    return FileResponse(path, media_type="text/x-shellscript", filename="install-node.sh")


@router.get("/agent-package", include_in_schema=False)
def node_agent_package(request: Request) -> FileResponse:
    _rate_limit(request, _enrollment_begin_limit)
    root = Path(__file__).resolve().parent.parent.parent
    agent_dir = root / "msm-agent"
    installer = root / "scripts" / "install-agent.sh"
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
            archive.add(installer, arcname="scripts/install-agent.sh", filter=package_filter)
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
    _rate_limit(request, _enrollment_begin_limit)
    source_ip = _source_ip(request)
    try:
        enrollment, claim = node_enrollment_service.begin_enrollment(
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
        claim_secret=claim,
        display_code=enrollment.display_code,
        expires_at=enrollment.expires_at,
    )


@router.post("/enrollments/poll", response_model=EnrollmentPollOut)
def poll_enrollment(
    request: Request,
    db: Session = Depends(get_db),
) -> EnrollmentPollOut:
    _rate_limit(request, _enrollment_poll_limit)
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
    owner: User = Depends(get_current_owner),
) -> list[NodeEnrollment]:
    _ = owner
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
    owner: User = Depends(get_current_owner),
    _: None = Depends(verify_csrf),
) -> dict:
    _ = owner
    enrollment = db.query(NodeEnrollment).filter(NodeEnrollment.id == enrollment_id).first()
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
    if metrics.get("cpu_count") is not None:
        node.cpu_total = float(metrics["cpu_count"])
    if metrics.get("ram_total_bytes") is not None:
        node.ram_total = int(metrics["ram_total_bytes"]) // (1024 * 1024)
    if metrics.get("disk_total_bytes") is not None:
        node.disk_total = int(metrics["disk_total_bytes"]) // (1024 * 1024)
    enrollment.status = "approved"
    db.commit()
    db.refresh(node)
    return node_out_dict(node, server_count=0)


@router.get("/{node_id}", response_model=NodeOut)
def get_node(
    node_id: int,
    db: Session = Depends(get_db),
    owner: User = Depends(get_current_owner),
) -> dict:
    _ = owner
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node nicht gefunden")
    count = db.query(Server).filter(Server.node_id == node.id).count()
    data = node_out_dict(node, server_count=count)

    # Live metrics from agent (best-effort; skip offline guard for manual probe)
    metrics = None
    status = node.status or "unknown"
    try:
        client = NodeClient.from_node(node)
        metrics = client.metrics()
        status = "online"
        if metrics:
            if metrics.get("cpu_count") is not None:
                node.cpu_total = float(metrics["cpu_count"])
            if metrics.get("ram_total_bytes") is not None:
                node.ram_total = int(metrics["ram_total_bytes"]) // (1024 * 1024)
            if metrics.get("disk_total_bytes") is not None:
                node.disk_total = int(metrics["disk_total_bytes"]) // (1024 * 1024)
            node.last_heartbeat = datetime.now(timezone.utc)
            node.status = "online"
            db.commit()
            data["cpu_total"] = node.cpu_total
            data["ram_total"] = node.ram_total
            data["disk_total"] = node.disk_total
            data["last_heartbeat"] = node.last_heartbeat
    except NodeClientError:
        status = "offline"
        node.status = "offline"
        db.commit()
    data["status"] = status
    data["metrics"] = metrics
    return data


@router.put("/{node_id}", response_model=NodeOut)
def update_node(
    node_id: int,
    body: NodeUpdate,
    db: Session = Depends(get_db),
    owner: User = Depends(get_current_owner),
    _: None = Depends(verify_csrf),
) -> dict:
    _ = owner
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node nicht gefunden")

    if body.name is not None:
        node.name = body.name.strip()

    new_fp = body.tls_fingerprint if body.tls_fingerprint is not None else node.tls_fingerprint
    new_host = body.host.strip() if body.host is not None else node.host
    if body.host is not None or body.tls_fingerprint is not None:
        try:
            node.host = validate_remote_node_host(
                new_host, new_fp, is_local=bool(node.is_local)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.tls_fingerprint is not None:
            node.tls_fingerprint = body.tls_fingerprint

    if body.auth_token is not None:
        try:
            node.auth_token_enc = encrypt_node_token(body.auth_token)
        except Exception:
            raise HTTPException(status_code=503, detail="Token konnte nicht verschluesselt werden (DIS)")

    db.commit()
    db.refresh(node)
    count = db.query(Server).filter(Server.node_id == node.id).count()
    return node_out_dict(node, server_count=count)


@router.delete("/{node_id}")
def delete_node(
    node_id: int,
    db: Session = Depends(get_db),
    owner: User = Depends(get_current_owner),
    _: None = Depends(verify_csrf),
) -> dict:
    _ = owner
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
