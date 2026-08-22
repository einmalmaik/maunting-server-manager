"""users.ai_provider_id: die Modellwahl folgt dem Konto, nicht dem Browser.

Revision ID: 20260822_01
Revises: 20260821_05
Create Date: 2026-08-22

Die Wahl des KI-Zugangs lag bisher allein im localStorage des Browsers. Die
Desktop-App hat eine eigene Herkunft (tauri.localhost) und damit einen leeren
Speicher — sie lief still auf dem erstbesten Zugang, der Sprachmodus ohne
Angabe auf der Backend-Reihenfolge. Beides konnte ein anderes (langsameres)
Modell sein als das im Panel gewählte. ON DELETE SET NULL: ein gelöschter
Zugang nimmt nur die Wahl mit (test_schema_constraints.py hält das fest).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260822_01"
down_revision: Union[str, None] = "20260821_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table: auf SQLite (Tests bauen die Kette wirklich auf und ab)
    # geht eine Fremdschlüsselspalte nur über den Tabellenneubau; auf
    # PostgreSQL werden daraus gewöhnliche ALTERs.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("ai_provider_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_users_ai_provider_id",
            "ai_providers",
            ["ai_provider_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Kein eigenes drop_constraint: SQLite-Reflexion verliert den
    # Constraint-Namen (KeyError im Batch), und mit der Spalte fällt der
    # Fremdschlüssel ohnehin — auf PostgreSQL wie im SQLite-Tabellenneubau.
    with op.batch_alter_table("users") as batch:
        batch.drop_column("ai_provider_id")
