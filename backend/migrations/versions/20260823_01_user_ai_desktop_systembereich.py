"""Der Systembereich des Rechners bekommt einen Schalter am Konto.

Revision ID: 20260823_01
Revises: 20260822_01
Create Date: 2026-08-23

Bis heute endete die Schreibgrenze der KI am freigegebenen Ordner, und das
Lesen kannte gar keine Grenze: ``desktop_system`` listet jedes Verzeichnis des
Rechners auf, auch ``C:\\Windows``. Mit dem Aufraeumen ausserhalb der Sandbox
kommen drei Zonen dazu (Frei, Muell, System) — und fuer die dritte hat der
Betreiber am 23.08.2026 entschieden, dass sie eine eigene Einstellung braucht:
*"in der [Sperr-]liste kann sie trotzdem helfen aber nur wenn man es bestaetigt
[und] sie es aufgelistet hat ... man kann in den einstellungen dann extra
einstellen ob sie es autonom mit dem autonomen modus darf oder nicht oder nur
readonly"*.

Der Vorgabewert ``lesen`` ist deshalb kein Kompromiss, sondern der **heutige
Zustand**. Ein Bestandsbenutzer, der nach dem Update ``aus`` haette, bekaeme
ploetzlich keine Auskunft mehr ueber seine Systemordner — eine Verschaerfung,
die niemand angeordnet hat und die wie ein Fehler aussieht. ``schreiben`` waere
die stille Lockerung in die andere Richtung. Beide Schritte gehoeren dem
Betreiber, sichtbar in den Einstellungen.

``NOT NULL`` mit ``server_default``: es soll keinen Zustand "nicht eingestellt"
geben. Ein ``NULL`` muesste an jeder Lesestelle erneut gedeutet werden, und die
erste Stelle, die es grosszuegig deutet, hat die Einstellung ausgehebelt.

**Der CHECK steht hier ausgeschrieben und wird nicht aus ``SYSTEMBEREICHE``
importiert.** Eine angewandte Migration ist Geschichte; wuerde sie das Tupel aus
dem Modell lesen, schriebe eine spaetere vierte Zone rueckwirkend um, was diese
Migration getan hat. Dasselbe Verfahren wie beim Fenster-CHECK in
``20260816_11`` und beim Zustands-CHECK in ``ai_runs``.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_01"
down_revision: Union[str, None] = "20260822_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BEREICHE = "ai_desktop_systembereich IN ('aus', 'lesen', 'schreiben')"


def upgrade() -> None:
    # Ein einziger Batch-Block: auf SQLite (die Tests fahren die Kette wirklich
    # auf und ab) wird die Tabelle dabei genau einmal neu aufgebaut, samt
    # Spalte und CHECK; auf PostgreSQL werden daraus gewoehnliche ALTERs.
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "ai_desktop_systembereich",
                sa.String(length=16),
                nullable=False,
                server_default="lesen",
            )
        )
        batch.create_check_constraint("ck_users_ai_desktop_systembereich", _BEREICHE)


def downgrade() -> None:
    # Erst der CHECK, dann die Spalte — andersherum bliebe auf PostgreSQL eine
    # Pruefung auf eine Spalte stehen, die es nicht mehr gibt. Nichts wird
    # aufgeraeumt: die Spalte traegt nur eine Vorliebe, keine Fremddaten.
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_ai_desktop_systembereich", type_="check")
        batch.drop_column("ai_desktop_systembereich")
