"""Stehende Aufträge bekommen ein eigenes Hintergrundfenster.

Revision ID: 20260820_03
Revises: 20260820_02
Create Date: 2026-08-20

Bis hierher schrieb ein fälliger Auftrag in den Dauerchat — und vertagte sich,
solange der Mensch dort arbeitete. Der Betreiber hat das umgedreht: im
Dauerchat steht nur, was der Mensch schreibt; Aufgaben laufen im Hintergrund
wie Worker und Guardian, und ihr Ergebnis kommt als Meldung, sobald das
Gespräch Ruhe hat (Meldestelle), plus optional als E-Mail.

``conversation_id`` zeigt auf das Fenster (``ai_conversations``,
``kind='worker'``), das beim ersten fälligen Lauf angelegt und danach
wiederverwendet wird. **Ein** Fenster je Aufgabe statt eines je Lauf: eine
tägliche Aufgabe hinterließe sonst 365 Fenster im Jahr, und der Verlauf der
Läufe gehört zusammen — das Modell sieht dort, was es gestern festgestellt hat.

``ON DELETE SET NULL`` wie bei ``last_run_id``: verschwindet das Fenster,
bleibt die Aufgabe bestehen und legt sich beim nächsten Termin ein neues an.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_03"
down_revision: Union[str, None] = "20260820_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch-Modus (Tabellen-Neubau): SQLite kann einer bestehenden Tabelle
    # keinen Fremdschlüssel per ALTER anhängen. ai_tasks ist klein (max. 20
    # Zeilen je Benutzer), der Neubau also unbedenklich.
    with op.batch_alter_table("ai_tasks") as batch:
        batch.add_column(sa.Column("conversation_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_ai_tasks_conversation_id",
            "ai_conversations",
            ["conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Nur der Verweis fällt weg; Fenster und Verläufe bleiben stehen. Die
    # Aufgabe legt sich nach dem nächsten Upgrade ein neues Fenster an.
    #
    # Batch-Modus (Tabellen-Neubau) statt DROP COLUMN: SQLite weigert sich,
    # eine Spalte zu entfernen, die in einer FOREIGN-KEY-Definition der
    # Tabelle vorkommt. ai_tasks ist klein (max. 20 Zeilen je Benutzer), der
    # Neubau also unbedenklich — anders als bei `servers`, wo die
    # KI-Verwaltungsspalten aus genau diesem Grund ohne FK angelegt wurden.
    with op.batch_alter_table("ai_tasks") as batch:
        batch.drop_column("conversation_id")
