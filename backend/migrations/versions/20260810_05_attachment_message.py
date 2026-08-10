"""Ein Anhang gehoert zu einer Nachricht, nicht zur ganzen Unterhaltung.

Revision ID: 20260810_05
Revises: 20260810_04
Create Date: 2026-08-10

Anhaenge hingen bisher nur an der Unterhaltung. Drei Folgen davon:

1. Nach dem Absenden blieb die Datei als Chip ueber dem Eingabefeld stehen und
   ging bei **jeder** weiteren Frage erneut an den Anbieter — bis sie aus den
   letzten fuenf herausfiel. Wer ein Log anhaengt und danach drei Dinge fragt,
   bezahlt es viermal.
2. Nach einem Neuladen war nicht mehr erkennbar, zu welcher Frage sie gehoerte.
3. Beim Bearbeiten einer Nachricht wurde der Verlauf ab dort abgeschnitten —
   die Anhaenge blieben liegen und tauchten in einem Zusammenhang wieder auf,
   in dem sie niemand angefordert hatte.

`message_id` ohne Fremdschluessel, dieselbe bewusste Entscheidung wie bei
`ai_action_proposals.run_id`: SQLite kann kein `ADD CONSTRAINT`, und die Tests
bauen das Schema mit `create_all` waehrend die Produktion Alembic laeuft. Ein
Fremdschluessel nur im Modell hiesse, dass die beiden Wege auseinanderlaufen.
Das Aufraeumen erledigt `ai_chat_service.truncate_from` ausdruecklich.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_05"
down_revision: Union[str, None] = "20260810_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_attachments") as batch:
        batch.add_column(sa.Column("message_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("redacted_spans", sa.Integer(), nullable=True))
    op.create_index(
        "ix_ai_attachments_message_id", "ai_attachments", ["message_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_attachments_message_id", table_name="ai_attachments")
    with op.batch_alter_table("ai_attachments") as batch:
        batch.drop_column("redacted_spans")
        batch.drop_column("message_id")
