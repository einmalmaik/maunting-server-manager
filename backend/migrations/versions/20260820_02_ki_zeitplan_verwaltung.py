"""Von der KI verwaltete Zeitpläne an Auto-Neustart und Auto-Backup.

Revision ID: 20260820_02
Revises: 20260820_01
Create Date: 2026-08-20

Stellt die KI einen Neustart- oder Backup-Zeitplan ein, soll der Betreiber das
am Server sehen — und eine manuelle Änderung soll gewinnen. Die beiden
``*_ai_managed``-Flags tragen das Abzeichen „Von der KI verwaltet"; die beiden
``*_ai_task_id``-Spalten verweisen auf den stehenden Auftrag (``ai_tasks``),
der den Zeitplan gesetzt hat, damit eine manuelle Änderung genau diesen
Auftrag deaktivieren kann statt irgendeinen.

Bewusst **ohne** Fremdschlüssel: die Verweise sind weiche Links. Ein harter
FK auf ``ai_tasks`` verlangte unter SQLite den Neubau der zentralen
``servers``-Tabelle (batch mode) — ein unverhältnismäßiges Risiko für einen
Verweis, den ``ai_task_service.loeschen`` ohnehin selbst aufräumt und den
jeder Leser tolerant behandelt (Task weg = nicht mehr verwaltet).

``server_default=false`` statt Backfill: bestehende Zeitpläne hat ein Mensch
eingestellt, und genau das sagen die Flags dann auch aus.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_02"
down_revision: Union[str, None] = "20260820_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("restart_ai_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "servers",
        sa.Column("restart_ai_task_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("backup_ai_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "servers",
        sa.Column("backup_ai_task_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    # Die Zeitpläne selbst (Intervalle, Zeiten, Aufbewahrung) bleiben stehen —
    # es entfällt nur die Auskunft, dass die KI sie verwaltet hat.
    op.drop_column("servers", "backup_ai_task_id")
    op.drop_column("servers", "backup_ai_managed")
    op.drop_column("servers", "restart_ai_task_id")
    op.drop_column("servers", "restart_ai_managed")
