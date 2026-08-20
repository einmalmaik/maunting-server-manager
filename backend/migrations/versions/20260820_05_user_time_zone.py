"""Zeitzone pro Benutzerkonto für verlässliche Ortszeit der KI und Aufgaben.

Revision ID: 20260820_05
Revises: 20260820_04
Create Date: 2026-08-20

MSM führt eine kanonische Benutzerspalte `time_zone` für die IANA-Zeitzone
des Benutzers (z.B. 'Europe/Berlin').

Damit hat die KI eine einzige verlässliche Quelle der Wahrheit für den
Lageblock, Chat-Zeitstempel und Aufgabenzeitpläne.

Die Spalte ist nullable, Bestandsbenutzer ohne Eintrag fallen deterministisch
auf UTC zurück, bis sie ihre Zeitzone im Profil speichern.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_05"
down_revision: Union[str, None] = "20260820_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("time_zone", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("time_zone")
