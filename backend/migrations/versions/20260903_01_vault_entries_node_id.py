"""Add node_id to vault_entries for dedicated multi-node assignment.

Revision ID: 20260903_01
Revises: 20260902_03
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260903_01"
down_revision: Union[str, None] = "20260902_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vault_entries",
        sa.Column("node_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_vault_entries_node_id", "vault_entries", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_vault_entries_node_id", table_name="vault_entries")
    op.drop_column("vault_entries", "node_id")
