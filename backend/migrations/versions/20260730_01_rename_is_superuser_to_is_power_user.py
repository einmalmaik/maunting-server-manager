"""Rename postgres_databases.is_superuser to is_power_user.

Power-User is database-scoped elevated owner credentials, never cluster SUPERUSER.
The old column name implied PostgreSQL SUPERUSER and misled operators.

Revision ID: 20260730_01
Revises: 93a7c6f012e1
Create Date: 2026-07-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260730_01"
down_revision: Union[str, None] = "93a7c6f012e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("postgres_databases")}
    if "is_superuser" in columns and "is_power_user" not in columns:
        op.alter_column(
            "postgres_databases",
            "is_superuser",
            new_column_name="is_power_user",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            existing_server_default=None,
        )
    elif "is_power_user" not in columns:
        op.add_column(
            "postgres_databases",
            sa.Column("is_power_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.alter_column("postgres_databases", "is_power_user", server_default=None)


def downgrade() -> None:
    raise RuntimeError("Downgrading is_power_user rename is not supported.")
