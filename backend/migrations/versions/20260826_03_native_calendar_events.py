"""Natives Kalendersystem: calendar_events.

Revision ID: 20260826_03
Revises: 20260826_02
Create Date: 2026-08-26

Erstellt die Tabelle:
- `calendar_events`: Speichert native Termine und Termindaten für den integrierten MSM-Kalender.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_03"
down_revision: Union[str, None] = "20260826_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "calendar_id",
            sa.Integer(),
            sa.ForeignKey("user_calendars.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_uid", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column(
            "all_day",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("calendar_events")
