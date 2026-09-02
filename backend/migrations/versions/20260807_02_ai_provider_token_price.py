"""Add an operator-supplied token price so the AI cost limit can be enforced.

Revision ID: 20260807_02
Revises: 20260807_01
Create Date: 2026-08-07

Hintergrund: `role_ai_limits.monthly_cost_limit_cents` war konfigurierbar, aber
wirkungslos — der verbuchte Verbrauch lag immer bei null, weil es keine
Preisquelle gab. Ein Betreiber konnte also ein Kostenlimit setzen und sich
faelschlich geschuetzt fuehlen.

Der Preis wird bewusst vom Betreiber gepflegt und nicht von MSM geraten: das
Zielbild verbietet erfundene Kostengenauigkeit. Ohne gesetzten Preis bleiben
die Kosten wie bisher bei null, und das Limit greift nicht — dann zeigt die
Oberflaeche das aber auch an.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_02"
down_revision: Union[str, None] = "20260807_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Cent je eine Million Tokens. Ganzzahlig, damit die Abrechnung ohne
    # Gleitkomma-Rundungsfehler auskommt.
    op.add_column(
        "ai_providers",
        sa.Column("token_price_cents_per_million", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_providers", "token_price_cents_per_million")
