"""Geräte-Benachrichtigungen für Nutzer: device_notifications.

Revision ID: 20260826_04
Revises: 20260826_03
Create Date: 2026-08-26

Fügt die Spalte `device_notifications` zur Tabelle `users` hinzu.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_04"
down_revision: Union[str, None] = "20260826_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "device_notifications",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("device_notifications")
