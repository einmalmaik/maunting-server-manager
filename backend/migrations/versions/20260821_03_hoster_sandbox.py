"""Hoster-Anbindung: Sandbox-Modus fuer Integrationen.

Revision ID: 20260821_03
Revises: 20260821_02
Create Date: 2026-08-21

Erlaubt das risikolose, isolierte Testen von Shop-Anbindungen (Stripe Testmodus,
schnelle Test-Provisionierung, Kündigungen und Webhooks).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_03"
down_revision: Union[str, None] = "20260821_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("hoster_integrations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_sandbox",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("hoster_integrations") as batch_op:
        batch_op.drop_column("is_sandbox")
