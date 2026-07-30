"""Add nodes.cpu_model for host inventory display.

Revision ID: 20260731_01
Revises: 20260730_01
Create Date: 2026-07-31
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260731_01"
down_revision: Union[str, None] = "20260730_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("cpu_model", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "cpu_model")
