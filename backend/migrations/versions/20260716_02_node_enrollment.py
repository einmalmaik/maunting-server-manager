"""Add short-lived node enrollment records.

Revision ID: 20260716_02
Revises: 20260716_01
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_02"
down_revision: Union[str, None] = "20260716_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "node_enrollments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claim_hash", sa.String(length=64), nullable=False),
        sa.Column("display_code", sa.String(length=9), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("tls_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("auth_token_enc", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_node_enrollments_claim_hash"),
        "node_enrollments",
        ["claim_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_node_enrollments_display_code"),
        "node_enrollments",
        ["display_code"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError("Node-Enrollment darf nicht automatisch zurueckgestuft werden.")
