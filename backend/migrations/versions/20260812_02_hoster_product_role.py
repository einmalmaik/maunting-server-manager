"""Ein Produkt kann eine Rolle mitbringen.

Revision ID: 20260812_02
Revises: 20260812_01
Create Date: 2026-08-12

`hoster_products.role_id` haelt fest, welche globale Rolle ein Kunde
zusaetzlich zu seinen bestehenden bekommt, solange sein Vertrag aktiv ist.
Ueber globale Rollen laufen unter anderem die KI-Kontingente — ein groesseres
Produkt darf damit mehr, ohne dass jemand nach jedem Kauf von Hand nachpflegt.

`ON DELETE SET NULL` und nicht `RESTRICT`. Es ist dieselbe Wahl wie beim
bereits vorhandenen `node_id` in derselben Tabelle, und sie kostet hier nichts:
NULL bedeutet exakt das, was es vor dieser Migration bei jedem Produkt bedeutet
hat — *keine Zusatzrolle*. Die Vergabelogik bekommt also keinen Sonderfall,
den sie sonst nirgends kennt.

Der Preis dafuer ist bewusst in Kauf genommen und soll niemanden ueberraschen:
loescht der Betreiber eine Rolle, verliert das Produkt sie *still*, statt die
Loeschung zu blockieren. Neue Vertraege buchen dann ohne Zusatzrolle weiter.

Bestandszeilen bekommen NULL. Kein Produkt hat je eine Rolle vergeben, und
eine aus Ressourcengrenzen zu erraten hiesse, Rechte zu verteilen, die der
Betreiber nie zugesagt hat.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_02"
down_revision: Union[str, None] = "20260812_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Ein einziger Batch-Block: auf SQLite wird die Tabelle dabei genau einmal neu
# aufgebaut, samt Fremdschluessel. Der Name wird ausdruecklich vergeben — SQLite
# speichert keine Namen fuer Fremdschluessel, und ohne einen laesst sich der
# Constraint spaeter nicht ansprechen (Lehre aus `20260809_02`).
def upgrade() -> None:
    with op.batch_alter_table("hoster_products") as batch:
        batch.add_column(sa.Column("role_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_hoster_products_role_id_roles",
            "roles",
            ["role_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("hoster_products") as batch:
        # Der Fremdschluessel wird bewusst nicht einzeln entfernt: das Loeschen
        # der Spalte nimmt ihn auf beiden Datenbanken mit, und `drop_constraint`
        # scheitert auf SQLite an eben jener fehlenden Namensablage.
        batch.drop_column("role_id")
