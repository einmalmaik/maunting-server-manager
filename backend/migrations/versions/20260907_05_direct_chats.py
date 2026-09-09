"""Add direct_chats table for persistent 1:1 messaging channels.

Revision ID: 20260907_05
Revises: 20260907_04
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_05"
down_revision: Union[str, None] = "20260907_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names()
    if "direct_chats" not in tables:
        op.create_table(
            "direct_chats",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_a_id", sa.Integer(), nullable=False),
            sa.Column("user_b_id", sa.Integer(), nullable=False),
            sa.Column("blind_mailbox_id", sa.String(length=64), nullable=False),
            sa.Column("initiated_by_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_a_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_b_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_a_id", "user_b_id", name="uq_direct_chats_users"),
        )
        op.create_index("ix_direct_chats_id", "direct_chats", ["id"], unique=False)
        op.create_index("ix_direct_chats_user_a_id", "direct_chats", ["user_a_id"], unique=False)
        op.create_index("ix_direct_chats_user_b_id", "direct_chats", ["user_b_id"], unique=False)
        op.create_index("ix_direct_chats_blind_mailbox_id", "direct_chats", ["blind_mailbox_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names()
    if "direct_chats" in tables:
        op.drop_index("ix_direct_chats_blind_mailbox_id", table_name="direct_chats")
        op.drop_index("ix_direct_chats_user_b_id", table_name="direct_chats")
        op.drop_index("ix_direct_chats_user_a_id", table_name="direct_chats")
        op.drop_index("ix_direct_chats_id", table_name="direct_chats")
        op.drop_table("direct_chats")
