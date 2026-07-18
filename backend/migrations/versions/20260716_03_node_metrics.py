"""Add node metrics and telemetry cache columns.

Revision ID: 20260716_03
Revises: 20260716_02
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_03"
down_revision: Union[str, None] = "20260716_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add cached live metrics columns to nodes table
    op.add_column("nodes", sa.Column("cpu_percent", sa.Float(), nullable=True))
    op.add_column("nodes", sa.Column("ram_used", sa.BigInteger(), nullable=True))
    op.add_column("nodes", sa.Column("disk_used", sa.BigInteger(), nullable=True))
    op.add_column("nodes", sa.Column("agent_version", sa.String(length=50), nullable=True))
    op.add_column("nodes", sa.Column("docker_connected", sa.Boolean(), nullable=True))
    op.add_column("nodes", sa.Column("container_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Node metrics migration cannot be automatically downgraded.")
