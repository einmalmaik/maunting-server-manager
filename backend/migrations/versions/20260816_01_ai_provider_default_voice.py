"""Die Stimme des Sprachmodus gehört dem Betreiber.

Revision ID: 20260816_01
Revises: 20260815_01
Create Date: 2026-08-16

Mit welcher der acht Realtime-Stimmen das Panel spricht, stand als Zeichenkette
im Sprachdienst und galt für jede Anlage gleich. Als Spalte am Zugang gehört sie
dem, der den Zugang einrichtet: die Stimme ist der Ton, in dem das Panel dem
Kunden gegenübertritt, und das ist keine Entscheidung des Programms.

Bestandszeilen bekommen NULL, und das ist hier der Punkt, nicht bloß der bequeme
Default: bei dieser Spalte heißt NULL „nichts hinterlegt“, und
``ai_voice_session.STANDARDSTIMME`` löst es bei jedem Verbinden neu auf. Nichts
ändert sich für einen bestehenden Zugang, weil der Rückfall dieselbe Stimme
liefert wie der Code vorher — nicht weil die Stimme verschwände.

Ein eingetragenes ``alloy`` sähe heute deshalb genau gleich aus, wäre aber
morgen ein Beschluss: wechselt MSM die Standardstimme, spräche jeder
Bestandszugang weiter mit der alten — nicht weil der Betreiber sie gewählt
hätte, sondern weil diese Migration sie hineingeschrieben hat. Und beim nächsten
Blick in die Oberfläche müsste er sie für seine eigene Wahl halten. NULL lässt
das Feld leer und die Entscheidung bei ihm.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260816_01"
down_revision: Union[str, None] = "20260815_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(sa.Column("default_voice", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("default_voice")
