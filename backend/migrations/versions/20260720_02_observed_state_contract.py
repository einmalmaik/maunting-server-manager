"""Add observed state contract columns to servers table.

Revision ID: 20260720_02
Revises: 20260720_01
Create Date: 2026-07-20
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260720_02"
down_revision: Union[str, None] = "20260720_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("guardian_accepted_generation", sa.Integer(), nullable=True))
    op.add_column("servers", sa.Column("guardian_last_sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("servers", sa.Column("guardian_agent_quarantine_json", sa.Text(), nullable=True))
    op.add_column("servers", sa.Column("guardian_agent_recovery_suspension_json", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Downgrading guardian engine fields is not supported.")
