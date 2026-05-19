"""add auth throttle and backup codes

Revision ID: 20260318_0001
Revises: 20260317_0001
Create Date: 2026-03-18 00:01:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260318_0001"
down_revision = "20260317_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("backup_codes_downloaded_at", sa.DateTime(), nullable=True))

    op.create_table(
        "backup_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_backup_codes_user_id", "backup_codes", ["user_id"])

    op.create_table(
        "auth_throttle",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(length=255), nullable=False, unique=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_until", sa.DateTime(), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("auth_throttle")
    op.drop_index("ix_backup_codes_user_id", table_name="backup_codes")
    op.drop_table("backup_codes")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("backup_codes_downloaded_at")
