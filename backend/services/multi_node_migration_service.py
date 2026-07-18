"""Idempotent startup migration and local-agent registration for multi-node."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from config import settings
from services.dis_client import DisClient

NODE_TOKEN_AAD = "msm:node:auth_token"


def _local_agent_token() -> str | None:
    explicit = os.getenv("MSM_LOCAL_AGENT_TOKEN", "").strip()
    if explicit:
        return explicit
    env_path = Path(settings.local_agent_env_file)
    if not env_path.is_absolute():
        env_path = (Path(__file__).resolve().parent.parent / env_path).resolve()
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "MSM_AGENT_TOKEN":
                token = value.strip().strip('"').strip("'")
                return token or None
    except OSError:
        return None
    return None


def ensure_multi_node_schema(engine: Any) -> None:
    """Add only the legacy multi-node schema pieces, without touching secrets."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "servers" in tables:
        columns = {column["name"] for column in inspector.get_columns("servers")}
        if "node_id" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE servers ADD COLUMN node_id INTEGER REFERENCES nodes(id)")
                )
    if "nodes" in tables:
        columns = {column["name"] for column in inspector.get_columns("nodes")}
        if "tls_fingerprint" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE nodes ADD COLUMN tls_fingerprint VARCHAR(128)"))


def migrate_multi_node_schema(
    engine: Any,
    session_factory: Any,
    *,
    allow_missing_local_token: bool = False,
    local_agent_enabled: bool = True,
) -> None:
    """Add legacy columns, sync the local agent token, assign orphan servers."""
    ensure_multi_node_schema(engine)
    sync_multi_node_registration(
        engine,
        session_factory,
        allow_missing_local_token=allow_missing_local_token,
        local_agent_enabled=local_agent_enabled,
    )


def sync_multi_node_registration(
    engine: Any,
    session_factory: Any,
    *,
    allow_missing_local_token: bool = False,
    local_agent_enabled: bool = True,
) -> None:
    """Sync local-node data after Alembic has prepared the schema."""
    tables = set(inspect(engine).get_table_names())

    from models import Node

    db = session_factory()
    try:
        local_node = db.query(Node).filter(Node.is_local.is_(True)).first()
        if not local_agent_enabled:
            if local_node is not None:
                raise RuntimeError(
                    "Backend-only mode cannot start while a local node is still "
                    "registered; convert it to a verified remote node first"
                )
            return

        token = _local_agent_token()
        if token:
            encrypted = DisClient.encrypt(token, aad=NODE_TOKEN_AAD)
            if local_node is None:
                local_node = Node(
                    name="Local",
                    host="http://127.0.0.1:9000",
                    auth_token_enc=encrypted,
                    is_local=True,
                    status="unknown",
                )
                db.add(local_node)
                db.flush()
            else:
                local_node.auth_token_enc = encrypted
        elif local_node is None and not allow_missing_local_token:
            raise RuntimeError(
                "Local MSM Agent token is missing; expected MSM_LOCAL_AGENT_TOKEN "
                "or the configured msm-agent/.env"
            )

        if local_node is not None and "servers" in tables:
            db.execute(
                text("UPDATE servers SET node_id = :node_id WHERE node_id IS NULL"),
                {"node_id": local_node.id},
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
