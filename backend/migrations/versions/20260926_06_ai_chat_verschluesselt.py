"""Der Chattitel wird Chiffrat und braucht dafuer Platz.

Seit dieser Revision stehen Titel, Zusammenfassung, Nachrichten, Werkzeug-
ergebnisse und Laufzustand der KI nur noch verschluesselt in der Datenbank
(``models/dis_text.py``). Die meisten Spalten sind schon ``TEXT``, nur
``ai_conversations.title`` war ``VARCHAR(160)``. Ein Titel mit 160 Zeichen
wird als ``msm-dis-v1:`` + Base64 aus IV, Text und Tag rund 270 Zeichen lang,
auf PostgreSQL endete das in ``value too long``.

Die Daten selbst fasst diese Revision nicht an. Verschluesseln kann nur der
DIS-Sidecar, und der laeuft waehrend einer Migration nicht zuverlaessig. Das
erledigt ``services/dis_altbestand.py`` beim Start des Panels.

Revision ID: 20260926_06
Revises: 20260926_05
Create Date: 2026-09-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260926_06"
down_revision: Union[str, None] = "20260926_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "ai_conversations" not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    with op.batch_alter_table("ai_conversations") as batch_op:
        batch_op.alter_column(
            "title",
            existing_type=sa.String(length=160),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "ai_conversations" not in set(sa.inspect(bind).get_table_names()):
        return
    # Ein Chiffrat laesst sich hier nicht zurueckverwandeln, und abschneiden
    # hiesse, es unbrauchbar zu machen. Was nicht passt, bekommt einen neutralen
    # Titel; die Nachrichten des Chats bleiben unberuehrt.
    bind.execute(sa.text("UPDATE ai_conversations SET title = 'Chat' WHERE length(title) > 160"))
    with op.batch_alter_table("ai_conversations") as batch_op:
        batch_op.alter_column(
            "title",
            existing_type=sa.Text(),
            type_=sa.String(length=160),
            existing_nullable=False,
        )
