"""Ein Lauf merkt sich, um welchen Server es ging.

Revision ID: 20260811_02
Revises: 20260811_01
Create Date: 2026-08-11

`ai_runs.last_server_id` haelt fest, welchen Server dieser Lauf zuletzt
nachweislich angefasst hat — ueber ein erfolgreiches serverbezogenes
Lesewerkzeug oder einen angelegten Schreibvorschlag.

Die Spalte beantwortet eine Frage, die bisher niemand stellen konnte: *worum*
geht es in diesem Zug gerade? Eine Unterhaltung taugt dafuer nicht, weil ihr
Thema wechselt; ein Lauf ist genau die Spanne, in der ein Thema gilt.

`ON DELETE SET NULL` und nicht `CASCADE`. Ein Lauf ist ein Beleg der
Unterhaltung und gehoert dem Benutzer, nicht dem Server. Was ein `CASCADE` an
dieser Stelle anrichtet, steht in `20260810_06`: dort nahm es bei jedem
geloeschten Server saemtliche je fuer ihn erzeugten Aktionsvorschlaege mit und
schrieb damit den Chatverlauf rueckwirkend um.

Bestandszeilen bekommen NULL. Das ist der richtige Wert und keine Luecke: kein
abgeschlossener Lauf hat je einen Serverbezug gehabt, und einen aus den
gespeicherten Anbieternachrichten nachtraeglich zu erraten hiesse, aus einer
vom Modell genannten Nummer einen geprueften Zugriff zu machen.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260811_02"
down_revision: Union[str, None] = "20260811_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Ein einziger Batch-Block: auf SQLite wird die Tabelle dabei genau einmal neu
# aufgebaut, samt Fremdschluessel. Der Name wird ausdruecklich vergeben — SQLite
# speichert keine Namen fuer Fremdschluessel, und ohne einen laesst sich der
# Constraint spaeter nicht ansprechen (Lehre aus `20260809_02`).
def upgrade() -> None:
    with op.batch_alter_table("ai_runs") as batch:
        batch.add_column(sa.Column("last_server_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_ai_runs_last_server_id_servers",
            "servers",
            ["last_server_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_runs") as batch:
        # Der Fremdschluessel wird bewusst nicht einzeln entfernt: das Loeschen
        # der Spalte nimmt ihn auf beiden Datenbanken mit, und `drop_constraint`
        # scheitert auf SQLite an eben jener fehlenden Namensablage.
        batch.drop_column("last_server_id")
