"""Rufname des Assistenten pro Benutzerkonto (Panel und Smart System).

Revision ID: 20260821_01
Revises: 20260820_05
Create Date: 2026-08-21

Der Benutzer vergibt dem Assistenten einen eigenen Namen (z. B. 'Singra',
'Jarvis'). Der Name fliesst in den Lageblock (services/ai_lage.py), nie in den
statischen Systemprompt — sonst waere der Prompt je Benutzer verschieden und
das Prompt-Caching des Anbieters tot.

Die Spalte ist nullable: Bestandsbenutzer ohne Eintrag fallen deterministisch
auf den Standardnamen 'Singra' zurueck (ai_lage.STANDARD_NAME).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_01"
down_revision: Union[str, None] = "20260820_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("agent_name", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("agent_name")
