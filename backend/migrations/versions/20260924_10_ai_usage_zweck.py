"""Verbrauchszeilen tragen ihren Zweck: die Ethics Engine wird gebucht.

Revision ID: 20260924_10
Revises: 20260924_01
Create Date: 2026-09-24

Seit dem 23.09.2026 berät die Ethics Engine wirklich, bis zu einmal je
beratenem Werkzeugaufruf, und jeder Rat ist ein Modellaufruf auf Rechnung des
Betreibers. Gebucht wurde davon nichts. Ab jetzt steht jede Beratung als eigene
Zeile beim Benutzer, dessen Lauf sie ausgelöst hat.

``zweck`` unterscheidet diese Zeilen von den Anfragen des Benutzers. Nötig ist
das für genau eine Grenze: *Anfragen pro Minute* zählt Zeilen, und ohne die
Marke zählte jede Beratung als Anfrage. Bei einem Limit von fünf beendete dann
die Engine den Lauf, den sie nur beraten soll.

Bestandszeilen bleiben NULL, also Anfragen des Benutzers — das waren sie auch.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260924_10"
down_revision: Union[str, None] = "20260924_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Constraint im selben Batch-Block: SQLite kennt kein ADD CONSTRAINT
    # (siehe 20260812_01).
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.add_column(sa.Column("zweck", sa.String(length=16), nullable=True))
        batch.create_check_constraint(
            "ck_ai_usage_events_zweck",
            "zweck IS NULL OR zweck IN ('ethik')",
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.drop_constraint("ck_ai_usage_events_zweck", type_="check")
        batch.drop_column("zweck")
