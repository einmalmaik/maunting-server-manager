"""Ethics Engine Konfiguration an ai_providers: Modell, Denkstufe und Modus.

Revision ID: 20260826_01
Revises: 20260825_02
Create Date: 2026-08-26

Fügt die drei Konfigurationsspalten für die optionale, dynamische Ethics Engine
zu `ai_providers` hinzu:
- `ethics_model`: Das Modell für ethische Reflexion und Risikoabwägungen (NULL = keine Engine).
- `ethics_reasoning_effort`: Optionale feste Denkstufe für das Ethik-Modell.
- `ethics_mode`: Zoning-Modus ('off', 'auto', 'always', 'critical', Standard: 'auto').
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_01"
down_revision: Union[str, None] = "20260825_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(sa.Column("ethics_model", sa.String(length=256), nullable=True))
        batch.add_column(
            sa.Column("ethics_reasoning_effort", sa.String(length=16), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "ethics_mode",
                sa.String(length=32),
                nullable=False,
                server_default="auto",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("ethics_mode")
        batch.drop_column("ethics_reasoning_effort")
        batch.drop_column("ethics_model")
