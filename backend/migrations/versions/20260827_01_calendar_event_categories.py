"""Kalender-Kategorien und Team/Server-Zuordnung für calendar_events.

Revision ID: 20260827_01
Revises: 20260826_04
Create Date: 2026-08-27

Fügt die Spalten `event_type`, `team_id` und `server_id` zur Tabelle `calendar_events` hinzu.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260827_01"
down_revision: Union[str, None] = "20260826_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("calendar_events") as batch:
        batch.add_column(
            sa.Column(
                "event_type",
                sa.String(length=32),
                nullable=False,
                server_default="personal",
            )
        )
        batch.add_column(
            sa.Column(
                "team_id",
                sa.Integer(),
                sa.ForeignKey("teams.id", ondelete="CASCADE"),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "server_id",
                sa.Integer(),
                sa.ForeignKey("servers.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.create_index("ix_calendar_events_event_type", ["event_type"])
        batch.create_index("ix_calendar_events_team_id", ["team_id"])
        batch.create_index("ix_calendar_events_server_id", ["server_id"])


def downgrade() -> None:
    with op.batch_alter_table("calendar_events") as batch:
        batch.drop_index("ix_calendar_events_server_id")
        batch.drop_index("ix_calendar_events_team_id")
        batch.drop_index("ix_calendar_events_event_type")
        batch.drop_column("server_id")
        batch.drop_column("team_id")
        batch.drop_column("event_type")
