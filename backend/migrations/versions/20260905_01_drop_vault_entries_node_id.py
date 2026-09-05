"""Drop node_id from vault_entries and clean up panel_settings for kiss password manager.

Revision ID: 20260905_01
Revises: 20260903_02
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260905_01"
down_revision: Union[str, None] = "20260903_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vault_entries") as batch_op:
        batch_op.drop_index("ix_vault_entries_node_id")
        batch_op.drop_column("node_id")

    op.execute("DELETE FROM panel_settings WHERE key = 'vault_assigned_node_id'")


def downgrade() -> None:
    with op.batch_alter_table("vault_entries") as batch_op:
        batch_op.add_column(
            sa.Column("node_id", sa.String(length=64), nullable=True),
        )
        batch_op.create_index("ix_vault_entries_node_id", ["node_id"])
