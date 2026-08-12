"""Ein Vertrag merkt sich, welche Rolle er vergeben hat.

Revision ID: 20260812_03
Revises: 20260812_02
Create Date: 2026-08-12

`hoster_services.granted_role_id` haelt fest, welche globale Rolle dieser
Vertrag dem Kunden tatsaechlich verschafft hat.

Die Vorgaengerrevision `20260812_02` brachte die Rolle ans Produkt. Der Entzug
las sie von dort auch wieder zurueck — und genau das geht schief, sobald sich
zwischen Vergabe und Entzug etwas am Produkt aendert. Drei Wege, alle aus dem
normalen Betrieb:

- Tarifwechsel: der Vertrag zeigt danach auf ein anderes Produkt. Die zuvor
  vergebene Rolle haengt an keinem Produkt des Kunden mehr, taucht in keiner
  Kandidatenmenge auf und wird nie entzogen. Der Kunde behaelt das Kontingent
  des grossen Tarifs, waehrend er den kleinen zahlt.
- Der Betreiber nimmt die Rolle aus dem Produkt oder ersetzt sie.
- Der Betreiber loescht das Produkt; `hoster_services.product_id` faellt auf
  NULL und der Vertrag verschwindet aus jeder Abfrage ueber das Produkt.

Ein Produkt ist veraenderlich, eine Vergabe ist ein Ereignis. Was
zurueckzunehmen ist, muss deshalb dort stehen, wo es passiert ist.

`ON DELETE SET NULL` wie bei `hoster_products.role_id`: loescht der Betreiber
die Rolle, gibt es nichts mehr zu entziehen, und ein blockiertes Loeschen waere
hier keine Hilfe.

Bestandszeilen bekommen NULL. Das ist richtig und keine Luecke: vor
`20260812_02` konnte kein Vertrag eine Rolle vergeben, und die Vergaben
zwischen den beiden Revisionen nachtraeglich zu erraten hiesse, Rechte zu
buchen, die vielleicht nie erteilt wurden. Der naechste Zustandswechsel eines
Vertrags schreibt den Wert ohnehin.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_03"
down_revision: Union[str, None] = "20260812_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Ein einziger Batch-Block: auf SQLite wird die Tabelle dabei genau einmal neu
# aufgebaut, samt Fremdschluessel. Der Name wird ausdruecklich vergeben — SQLite
# speichert keine Namen fuer Fremdschluessel, und ohne einen laesst sich der
# Constraint spaeter nicht ansprechen (Lehre aus `20260809_02`).
def upgrade() -> None:
    with op.batch_alter_table("hoster_services") as batch:
        batch.add_column(sa.Column("granted_role_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_hoster_services_granted_role_id_roles",
            "roles",
            ["granted_role_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("hoster_services") as batch:
        # Der Fremdschluessel wird bewusst nicht einzeln entfernt: das Loeschen
        # der Spalte nimmt ihn auf beiden Datenbanken mit, und `drop_constraint`
        # scheitert auf SQLite an eben jener fehlenden Namensablage.
        batch.drop_column("granted_role_id")
