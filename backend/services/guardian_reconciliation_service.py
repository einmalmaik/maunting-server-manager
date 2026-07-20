from __future__ import annotations

import logging
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Server
from services.guardian_sync_service import reconcile_guardian_server
from services.node_client import NodeClient


logger = logging.getLogger(__name__)


async def reconcile_guardian_servers() -> None:
    """Reconcile durable intent, observed state and incidents for every server assigned to a node."""
    db = SessionLocal()
    node_clients: dict[int, NodeClient] = {}
    try:
        servers = db.query(Server).filter(Server.node_id.is_not(None)).all()
        for server in servers:
            node = server.node
            if not node or node.status != "online":
                continue
            
            client = node_clients.get(node.id)
            if client is None:
                try:
                    client = NodeClient.from_node(node, timeout=5.0)
                    node_clients[node.id] = client
                except Exception as node_err:
                    logger.warning(
                        "Failed to construct NodeClient for node_id=%s: %s",
                        node.id,
                        node_err,
                    )
                    continue
            
            try:
                reconcile_guardian_server(db, server, node_client=client)
            except Exception as srv_err:
                db.rollback()
                logger.warning(
                    "Guardian reconciliation failed for server_id=%s code=%s",
                    server.id,
                    getattr(srv_err, "code", type(srv_err).__name__),
                )
    except Exception as exc:
        logger.warning(
            "Guardian reconciliation task failed code=%s",
            type(exc).__name__,
        )
        db.rollback()
    finally:
        db.close()
