"""Add vault_blind_buckets table for blind zero-metadata vault synchronization.

Revision ID: 20260906_01
Revises: 20260905_01
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260906_01"
down_revision: Union[str, None] = "20260905_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vault_blind_buckets",
        sa.Column("bucket_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("auth_verifier", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("vault_blind_buckets")
