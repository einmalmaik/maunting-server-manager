"""Harden node trust identity and resource column types.

Revision ID: 20260718_01
Revises: 20260716_03
Create Date: 2026-07-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260718_01"
down_revision: Union[str, None] = "20260716_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE nodes SET tls_fingerprint = "
        "lower(replace(tls_fingerprint, ':', '')) "
        "WHERE tls_fingerprint IS NOT NULL"
    )
    op.alter_column(
        "nodes",
        "tls_fingerprint",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
    )
    op.create_check_constraint(
        "ck_nodes_tls_fingerprint_normalized",
        "nodes",
        "tls_fingerprint IS NULL OR (length(tls_fingerprint) = 64 "
        "AND tls_fingerprint = lower(tls_fingerprint) "
        "AND tls_fingerprint NOT LIKE '%:%')",
    )
    op.create_unique_constraint(
        "uq_nodes_tls_fingerprint", "nodes", ["tls_fingerprint"]
    )
    op.create_index("ix_servers_node_id", "servers", ["node_id"], unique=False)
    op.alter_column(
        "nodes", "ram_total", existing_type=sa.Integer(), type_=sa.BigInteger()
    )
    op.alter_column(
        "nodes", "disk_total", existing_type=sa.Integer(), type_=sa.BigInteger()
    )
    op.alter_column(
        "nodes", "ram_used", existing_type=sa.Integer(), type_=sa.BigInteger()
    )
    op.alter_column(
        "nodes", "disk_used", existing_type=sa.Integer(), type_=sa.BigInteger()
    )
def downgrade() -> None:
    raise RuntimeError("Node-Trust-Hardening darf nicht automatisch zurueckgestuft werden.")
