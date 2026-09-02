"""Add vault_entries table for anonymous zero-knowledge password manager.

Revision ID: 20260902_02
Revises: 20260902_01
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_02"
down_revision: Union[str, None] = "20260902_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vault_entries",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("bucket_id", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_vault_entries_bucket_id", "vault_entries", ["bucket_id"])
    op.create_index("ix_vault_entries_revision", "vault_entries", ["revision"])
    op.create_index("ix_vault_entries_bucket_revision", "vault_entries", ["bucket_id", "revision"])


def downgrade() -> None:
    op.drop_index("ix_vault_entries_bucket_revision", table_name="vault_entries")
    op.drop_index("ix_vault_entries_revision", table_name="vault_entries")
    op.drop_index("ix_vault_entries_bucket_id", table_name="vault_entries")
    op.drop_table("vault_entries")
