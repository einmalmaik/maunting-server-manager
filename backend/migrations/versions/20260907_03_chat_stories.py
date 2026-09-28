"""Add chat_stories table for 24h temporary status updates.

Revision ID: 20260907_03
Revises: 20260907_02
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_03"
down_revision: Union[str, None] = "20260907_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "chat_stories" not in tables:
        op.create_table(
            "chat_stories",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("media_url", sa.Text(), nullable=True),
            sa.Column("background", sa.String(length=64), server_default="gradient-1", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "chat_stories" in tables:
        op.drop_table("chat_stories")
