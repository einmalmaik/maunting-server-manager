"""add servers table

Revision ID: 20260315_0001
Revises: 20260314_0001
Create Date: 2026-03-15 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260315_0001"
down_revision = "20260314_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("server_dir", sa.String(512), nullable=False),
        sa.Column("manager_path", sa.String(512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("servers")
