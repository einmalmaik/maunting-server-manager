"""Die KI heisst fest Singra: ``users.agent_name`` entfaellt.

Bis hierher konnte jeder Benutzer der KI einen eigenen Rufnamen geben
(20260821_01). Betreiberentscheid vom 05.10.2026: der Name ist Identitaet,
keine Einstellung — er steht jetzt einmal in ``services/ai_prompt.KI_NAME``.

Vergebene Namen gehen dabei verloren; das ist der Sinn der Aenderung. Der
Downgrade legt die Spalte leer wieder an — alle Konten landen dann auf dem
damaligen Standardnamen.

Revision ID: 20261005_01
Revises: 20261003_02
Create Date: 2026-10-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261005_01"
down_revision: Union[str, None] = "20261003_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _spalten() -> set[str]:
    return {spalte["name"] for spalte in sa.inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "agent_name" in _spalten():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("agent_name")


def downgrade() -> None:
    if "agent_name" not in _spalten():
        with op.batch_alter_table("users") as batch:
            batch.add_column(
                sa.Column("agent_name", sa.String(length=32), nullable=True)
            )
