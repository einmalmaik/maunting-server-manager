"""Der Zwischenspeicher bekommt seine zweite Zahl.

Revision ID: 20260814_01
Revises: 20260813_04
Create Date: 2026-08-14

Gebucht wurde bisher nur ``cached_tokens`` — die Eingabe, die der Anbieter aus
seinem Zwischenspeicher gelesen hat. ``cache_write_tokens`` ist die Gegenzahl
dazu; warum es beide braucht, steht bei der Spalte in
`models/ai_usage_event.py`.

Bestandszeilen bekommen NULL. Eine 0 hieße „nichts geschrieben“, und das ist für
Zeilen aus der Zeit vor dieser Spalte eine Behauptung, die niemand gemessen hat.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260814_01"
down_revision: Union[str, None] = "20260813_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.add_column(sa.Column("cache_write_tokens", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.drop_column("cache_write_tokens")
