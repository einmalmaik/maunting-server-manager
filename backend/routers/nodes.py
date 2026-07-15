"""Admin Node-Management API.

Owner-only for create/update/delete. List/detail for authenticated users
with panel admin context (owner or is_admin via existing admin patterns).
Never returns auth_token_enc or plaintext tokens.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_owner, verify_csrf
from models import Node, Server, User
from schemas.node import NodeCreate, NodeOut, NodeUpdate
from services.node_client import NodeClient, NodeClientError
from services.node_service import encrypt_node_token, node_out_dict

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


@router.get("", response_model=list[NodeOut])
def list_nodes(
    db: Session = Depends(get_db),
    owner: User = Depends(get_current_owner),
) -> list[dict]:
    _ = owner
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
    host = body.host.strip()
    if not host:
        raise HTTPException(status_code=400, detail="host ist erforderlich")
    try:
        token_enc = encrypt_node_token(body.auth_token)
    except Exception:
        raise HTTPException(status_code=503, detail="Token konnte nicht verschluesselt werden (DIS)")

    node = Node(
        name=body.name.strip(),
        host=host,
        auth_token_enc=token_enc,
        is_local=False,
        status="unknown",
    )
    db.add(node)
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

    # Live metrics from agent (best-effort)
    metrics = None
    status = node.status or "unknown"
    try:
        client = NodeClient.from_node(node)
        metrics = client.metrics()
        status = "online"
        # Persist lightweight resource totals when agent reports them
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
    if body.host is not None:
        host = body.host.strip()
        if not host:
            raise HTTPException(status_code=400, detail="host ist erforderlich")
        node.host = host
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
