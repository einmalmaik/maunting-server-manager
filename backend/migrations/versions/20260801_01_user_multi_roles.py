"""Add additive multi-role assignments for users.

Revision ID: 20260801_01
Revises: 20260731_01
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260801_01"
down_revision: Union[str, None] = "20260731_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Erstellt die Zuordnungstabelle und uebernimmt bestehende Primärrollen."""
    op.create_table(
        "user_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"], unique=False)
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"], unique=False)

    # Die Migration ist additiv: ``users.role_id`` bleibt als kompatible
    # Primärrolle erhalten. INSERT .. SELECT kopiert nur reale Zuweisungen.
    op.execute(
        "INSERT INTO user_roles (user_id, role_id) "
        "SELECT id, role_id FROM users WHERE role_id IS NOT NULL"
    )


def downgrade() -> None:
    """Entfernt nur die additive Tabelle; die Legacy-Primärrolle bleibt intakt."""
    op.drop_index("ix_user_roles_role_id", table_name="user_roles")
    op.drop_index("ix_user_roles_user_id", table_name="user_roles")
    op.drop_table("user_roles")
