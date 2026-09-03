"""Add vault_user_settings table for user bucket authorization and persistent KDF salt.

Revision ID: 20260903_02
Revises: 20260903_01
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260903_02"
down_revision: Union[str, None] = "20260903_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vault_user_settings",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("bucket_id", sa.String(length=64), nullable=True),
        sa.Column("kdf_salt", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_vault_user_settings_bucket_id", "vault_user_settings", ["bucket_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_vault_user_settings_bucket_id", table_name="vault_user_settings")
    op.drop_table("vault_user_settings")
