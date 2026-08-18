"""Die Worker-Rolle am Provider-Zugang: Arbeitsmodell und feste Denkstufe.

Revision ID: 20260818_02
Revises: 20260818_01
Create Date: 2026-08-18

Umsetzung von docs/agentic-framework.md (Abschnitt 5, Provider-Zweiteilung):
Der Betreiber legt je Chatzugang fest, mit welchem Modell und welcher
Denkstufe die Worker arbeiten. Beides sind neue, leere Spalten an
``ai_providers`` — das Muster stammt aus ``20260816_02``: eine eigene
Modellspalte fuer eine eigene Frage. ``default_model`` denkt im Gespraech,
``transcription_model`` hoert, ``default_voice`` spricht, ``worker_model``
arbeitet.

NULL heisst „keine Worker-Rolle konfiguriert" und damit der heutige
Ein-Modell-Betrieb — deshalb braucht diese Migration keine Datenarbeit: jede
Bestandszeile verhaelt sich nach dem Upgrade exakt wie vorher.

Der Downgrade verwirft die Betreiberwahl ersatzlos. Ein Rueckweg koennte sie
auch nicht retten: die aeltere Version kennt keine Worker.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_02"
down_revision: Union[str, None] = "20260818_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(sa.Column("worker_model", sa.String(length=256), nullable=True))
        batch.add_column(
            sa.Column("worker_reasoning_effort", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("worker_reasoning_effort")
        batch.drop_column("worker_model")
