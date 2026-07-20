"""Add occurrences and observed state fields to incidents and servers.

Revision ID: 20260720_01
Revises: 20260719_02
Create Date: 2026-07-20
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260720_01"
down_revision: Union[str, None] = "20260719_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add occurrences to incidents table
    op.add_column(
        "incidents",
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
    )

    # 2. Add observed state fields to servers table
    op.add_column("servers", sa.Column("guardian_last_payload_hash", sa.String(length=71), nullable=True))
    op.add_column("servers", sa.Column("guardian_container_status", sa.String(length=32), nullable=True))
    op.add_column("servers", sa.Column("guardian_active_incident_uuid", sa.String(length=36), nullable=True))
    op.add_column("servers", sa.Column("guardian_probe_timestamp", sa.DateTime(timezone=True), nullable=True))
    op.add_column("servers", sa.Column("guardian_transition_timestamp", sa.DateTime(timezone=True), nullable=True))
    op.add_column("servers", sa.Column("guardian_quarantine_status", sa.String(length=32), nullable=True))
    op.add_column("servers", sa.Column("guardian_sync_error_statistics", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Downgrading guardian engine fields is not supported.")
