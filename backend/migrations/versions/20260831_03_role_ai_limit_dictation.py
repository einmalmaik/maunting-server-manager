"""Monthly dictation minutes limit for AI roles and dictation duration tracking.

Revision ID: 20260831_03
Revises: 20260831_02
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260831_03"
down_revision: Union[str, None] = "20260831_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("role_ai_limits") as batch:
        batch.add_column(sa.Column("monthly_dictation_minutes_limit", sa.Integer(), nullable=True))

    with op.batch_alter_table("ai_usage_events") as batch:
        batch.add_column(sa.Column("dictation_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.drop_column("dictation_seconds")

    with op.batch_alter_table("role_ai_limits") as batch:
        batch.drop_column("monthly_dictation_minutes_limit")
