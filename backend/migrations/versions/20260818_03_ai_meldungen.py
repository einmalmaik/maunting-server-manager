"""Die Meldungen der Worker: wartende Wortmeldungen als Zeilen.

Revision ID: 20260818_03
Revises: 20260818_02
Create Date: 2026-08-18

Umsetzung von docs/agentic-framework.md (Abschnitt 4, Meldestelle): Ergebnisse
und Rueckfragen der Worker graetschen nie ins Gespraech — sie warten als
Zeilen, bis Ruhe ist. Die Zeile ist die Zustellgarantie: sie ueberlebt
Neustarts, und ``zugestellt_at`` ist die Doppelversand-Marke, die **vor** dem
Versand committet wird.

``worker_id`` traegt `SET NULL` und nicht `CASCADE`: die Meldung ist ein Beleg
an den Menschen und ueberlebt das Aufraeumen des Worker-Fensters — dieselbe
Ueberlegung wie bei `ai_runs.last_server_id` (20260810_06), wo ein CASCADE
einmal Chatverlauf rueckwirkend umgeschrieben hat.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_03"
down_revision: Union[str, None] = "20260818_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_meldungen",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=36), nullable=True),
        sa.Column("art", sa.String(length=16), nullable=False),
        sa.Column("kanal", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("question_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("zugestellt_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["worker_id"], ["ai_conversations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Woertliche Kopien, nicht aus dem Modell importiert — eine angewandte
        # Migration ist Geschichte und wird nicht rueckwirkend umgeschrieben.
        sa.CheckConstraint(
            "art IN ('ergebnis', 'frage')", name="ck_ai_meldungen_art"
        ),
        sa.CheckConstraint(
            "kanal IN ('chat', 'email', 'both')", name="ck_ai_meldungen_kanal"
        ),
    )
    # Die Namen des Modells, nicht schoenere eigene (Lektion aus 20260816_14).
    op.create_index("ix_ai_meldungen_user_id", "ai_meldungen", ["user_id"])
    op.create_index(
        "ix_ai_meldungen_user_zugestellt", "ai_meldungen", ["user_id", "zugestellt_at"]
    )


def downgrade() -> None:
    # Offene Meldungen gehen verloren — mit der Tabelle verschwindet auch der
    # einzige Weg, sie je zuzustellen. Die Ergebnisse selbst stehen weiterhin
    # in den Worker-Unterhaltungen.
    op.drop_index("ix_ai_meldungen_user_zugestellt", table_name="ai_meldungen")
    op.drop_index("ix_ai_meldungen_user_id", table_name="ai_meldungen")
    op.drop_table("ai_meldungen")
