"""Add chat_groups and chat_group_members tables.

Revision ID: 20260907_02
Revises: 20260907_01
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_02"
down_revision: Union[str, None] = "20260907_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "chat_groups" not in tables:
        op.create_table(
            "chat_groups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("description", sa.String(length=256), nullable=True),
            sa.Column("avatar_url", sa.Text(), nullable=True),
            sa.Column("invite_code", sa.String(length=32), nullable=False, unique=True, index=True),
            sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "chat_group_members" not in tables:
        op.create_table(
            "chat_group_members",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("group_id", sa.Integer(), sa.ForeignKey("chat_groups.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("role", sa.String(length=16), server_default="member", nullable=False),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("group_id", "user_id", name="uq_chat_group_members_group_user"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "chat_group_members" in tables:
        op.drop_table("chat_group_members")
    if "chat_groups" in tables:
        op.drop_table("chat_groups")
