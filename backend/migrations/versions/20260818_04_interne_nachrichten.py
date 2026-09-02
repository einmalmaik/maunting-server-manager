"""Interne Nachrichten: Maschinerie verlaesst den sichtbaren Verlauf.

Revision ID: 20260818_04
Revises: 20260818_03
Create Date: 2026-08-18

Vier Stellen schreiben eine Zeile in den Chat, die kein Mensch getippt hat:
die Zustellung der Worker-Meldungen (`ai_meldestelle`), das Guardian-Briefing,
der Wiederanlauf nach einem Neustart und die Notiz ueber eine abgebrochene
Runde. Alle vier standen bisher als ``role="user"`` im Verlauf — der Betreiber
las dort JSON-Nutzlasten und Anweisungen an die KI, adressiert an ihn selbst.

Ein Worker ist ein Hintergrund-Auftrag; seine Maschinerie gehoert nicht ins
Gespraech. Die Spalte markiert genau das und wird **nur** auf dem Weg in den
Browser ausgewertet. Der Kontext, der zum Anbieter geht, bleibt vollstaendig:
sonst bekaeme das Gehirn den Auftrag zu liefern und wuesste eine Runde spaeter
nicht mehr, warum.

``server_default=false`` und nicht nachtraeglich gefuellt: was vor dieser
Spalte im Verlauf stand, war echtes Gespraech und bleibt sichtbar. Ein
Backfill haette raten muessen, welche der alten Zeilen Maschinerie waren —
und ein falsches Raten laesst Gespraech verschwinden.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_04"
down_revision: Union[str, None] = "20260818_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_messages",
        sa.Column(
            "intern",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    # Die Zeilen selbst bleiben stehen — sie werden nur wieder sichtbar. Kein
    # Datenverlust, aber der Verlauf zeigt danach erneut die Panel-Meldungen.
    op.drop_column("ai_messages", "intern")
