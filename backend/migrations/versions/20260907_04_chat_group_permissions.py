"""Add default_permissions to chat_groups and permissions to chat_group_members.

Revision ID: 20260907_04
Revises: 20260907_03
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_04"
down_revision: Union[str, None] = "20260907_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    group_cols = {col["name"] for col in inspector.get_columns("chat_groups")}
    if "default_permissions" not in group_cols:
        op.add_column(
            "chat_groups",
            sa.Column(
                "default_permissions",
                sa.String(length=256),
                server_default="send_messages,invite_members",
                nullable=True,
            ),
        )

    member_cols = {col["name"] for col in inspector.get_columns("chat_group_members")}
    if "permissions" not in member_cols:
        op.add_column(
            "chat_group_members",
            sa.Column(
                "permissions",
                sa.String(length=256),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    member_cols = {col["name"] for col in inspector.get_columns("chat_group_members")}
    if "permissions" in member_cols:
        op.drop_column("chat_group_members", "permissions")

    group_cols = {col["name"] for col in inspector.get_columns("chat_groups")}
    if "default_permissions" in group_cols:
        op.drop_column("chat_groups", "default_permissions")
