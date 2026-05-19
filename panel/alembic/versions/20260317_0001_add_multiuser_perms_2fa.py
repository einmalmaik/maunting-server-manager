"""add email, role, permissions, totp fields to users

Revision ID: 20260317_0001
Revises: 20260316_0001
Create Date: 2026-03-17 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260317_0001"
down_revision = "20260316_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("role", sa.String(32), nullable=False, server_default="user"))
        batch_op.add_column(sa.Column("permissions", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("totp_secret", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.create_unique_constraint("uq_users_email", ["email"])

    # Upgrade the first (oldest) user to owner role (cross-DB safe: no self-referencing subquery)
    conn = op.get_bind()
    result = conn.execute(sa.text("SELECT id FROM users ORDER BY id ASC LIMIT 1"))
    row = result.fetchone()
    if row:
        conn.execute(sa.text("UPDATE users SET role = 'owner' WHERE id = :id"), {"id": row[0]})


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_email", type_="unique")
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("totp_secret")
        batch_op.drop_column("permissions")
        batch_op.drop_column("role")
        batch_op.drop_column("email")
