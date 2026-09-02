"""Die Rueckfrage gehoert zur Nachricht, nicht nur ins Ereignis.

Revision ID: 20260810_01
Revises: 20260809_04
Create Date: 2026-08-10

Bisher lebte eine Rueckfrage der KI ausschliesslich als SSE-Ereignis `question`
und als clientseitiger Eintrag im Chat. Gespeichert wurde sie nirgends. Das
hatte drei Folgen, die im Betrieb alle drei auffielen:

1. Die Assistenten-Nachricht blieb leer, und der Chat zeigte statt der Frage
   den Platzhalter "Keine Antwort erhalten".
2. **Das Modell sah seine eigene Frage nicht mehr.** `build_provider_messages`
   baut die Historie aus `ai_messages`; dort stand eine leere Assistenten-Zeile,
   gefolgt von der Antwort des Benutzers. Auf "Server.properties" konnte das
   Modell nur mit derselben Frage nochmal reagieren — es wusste nicht, dass es
   gefragt hatte.
3. Nach einem Neuladen der Seite war die Frage verschwunden.

`question_json` haelt die bereits gepruefte und redigierte Nutzlast
(`{"question": ..., "options": [...]}`) an der Nachricht fest, zu der sie
gehoert. Damit ist sie Teil des Verlaufs wie jeder andere Text auch.

Bewusst eine JSON-Spalte und keine eigene Tabelle: eine Frage hat genau eine
Nachricht, wird nie einzeln abgefragt und nie ohne sie angezeigt. Eine Tabelle
mit 1:1-Beziehung waere ein Join ohne Gegenwert.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_01"
down_revision: Union[str, None] = "20260809_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_messages") as batch:
        batch.add_column(sa.Column("question_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_messages") as batch:
        batch.drop_column("question_json")
