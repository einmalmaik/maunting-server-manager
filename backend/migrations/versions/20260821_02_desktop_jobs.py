"""Auftraege an den Rechner des Benutzers (Smart System).

Revision ID: 20260821_02
Revises: 20260821_01
Create Date: 2026-08-21

Die Werkzeuge des Smart Systems arbeiten auf dem Rechner vor dem Benutzer, den
das Panel nicht anrufen kann. Ein Werkzeugaufruf wird deshalb zu einer Zeile
hier; die Desktop-App holt sie ab und meldet das Ergebnis zurueck, und dieses
Ergebnis weckt den wartenden Lauf.

`run_id` mit ON DELETE CASCADE: ohne den Lauf koennte niemand das Ergebnis noch
entgegennehmen. Modell und Migration tragen dieselbe Regel — beide zusammen
sind die Zusage (siehe tests/test_schema_constraints.py).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_02"
down_revision: Union[str, None] = "20260821_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "desktop_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("ai_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("result_encrypted", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'taken', 'done', 'failed', 'expired')",
            name="ck_desktop_jobs_status",
        ),
    )
    op.create_index(
        "ix_desktop_jobs_user_status", "desktop_jobs", ["user_id", "status", "created_at"]
    )
    op.create_index("ix_desktop_jobs_run_status", "desktop_jobs", ["run_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_desktop_jobs_run_status", table_name="desktop_jobs")
    op.drop_index("ix_desktop_jobs_user_status", table_name="desktop_jobs")
    op.drop_table("desktop_jobs")
