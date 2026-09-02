"""Ein Werkzeugergebnis weiss, zu welchem Lauf es gehoert.

Revision ID: 20260811_04
Revises: 20260811_03
Create Date: 2026-08-11

`ai_tool_results.run_id` beantwortet dieselbe Frage wie `ai_runs.last_server_id`
eine Ebene tiefer: *wozu* gehoert dieses Ergebnis. Der Rueckfluss in den
Kontext (`ai_context_service._recent_tool_results`) nahm bisher die letzten
sechs Ergebnisse der gesamten Unterhaltung — und eine Unterhaltung laeuft in
MSM dauerhaft und wechselt dabei das Thema. Der gelesene Log von Server A stand
damit noch vor dem Modell, wenn laengst nach Server B gefragt wurde, und der
Text eines einmal gelesenen Skills wiederholte sich Zug um Zug.

Ein Lauf ist die Spanne, in der ein Thema gilt (die Begruendung steht in
`20260811_02`). Mit dieser Spalte laesst sich der Rueckfluss darauf begrenzen.

`ON DELETE SET NULL` und nicht `CASCADE`. Ein Ergebnis gehoert der Unterhaltung
— dort haengt es bereits mit `CASCADE` — und nicht dem Lauf. Was ein `CASCADE`
an einer solchen Stelle anrichtet, steht in `20260810_06`.

Bestandszeilen bekommen NULL. Sie bilden damit einen gemeinsamen Topf und
verhalten sich wie bisher; nachtraeglich einen Lauf zu erraten hiesse, aus
einer zeitlichen Naehe eine Tatsache zu machen.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260811_04"
down_revision: Union[str, None] = "20260811_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Ein einziger Batch-Block: auf SQLite wird die Tabelle dabei genau einmal neu
# aufgebaut, samt Fremdschluessel. Der Name wird ausdruecklich vergeben —
# SQLite speichert keine Namen fuer Fremdschluessel, und ohne einen laesst sich
# der Constraint spaeter nicht ansprechen (Lehre aus `20260809_02`).
def upgrade() -> None:
    with op.batch_alter_table("ai_tool_results") as batch:
        batch.add_column(sa.Column("run_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_ai_tool_results_run_id_ai_runs",
            "ai_runs",
            ["run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_tool_results") as batch:
        # Der Fremdschluessel wird bewusst nicht einzeln entfernt: das Loeschen
        # der Spalte nimmt ihn auf beiden Datenbanken mit, und `drop_constraint`
        # scheitert auf SQLite an eben jener fehlenden Namensablage.
        batch.drop_column("run_id")
