"""Erweitert ai_action_proposals um proposal_type (read, worker, write).

Revision ID: 20260825_01
Revises: 20260823_03
Create Date: 2026-08-25

Unterscheidet Schreib-, Lese- und Worker-Vorschlaege sauber auf Datenbankebene.
Bestehende Vorschlaege erhalten den Standardwert 'write'.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_01"
down_revision: Union[str, None] = "20260823_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_action_proposals") as batch:
        batch.add_column(
            sa.Column(
                "proposal_type",
                sa.String(length=16),
                nullable=False,
                server_default="write",
            )
        )
        batch.create_check_constraint(
            "ck_ai_action_proposals_proposal_type",
            "proposal_type IN ('read', 'worker', 'write')",
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_action_proposals") as batch:
        batch.drop_constraint("ck_ai_action_proposals_proposal_type", type_="check")
        batch.drop_column("proposal_type")
