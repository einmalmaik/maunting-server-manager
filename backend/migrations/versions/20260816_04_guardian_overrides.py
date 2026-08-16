"""Guardian laesst sich je Server anders einstellen.

Revision ID: 20260816_04
Revises: 20260816_03
Create Date: 2026-08-16

Die Blueprint gilt fuer jeden Server ihres Spiels. Sie kann nicht wissen, dass
ausgerechnet auf dieser Node zwoelf Instanzen um acht Gigabyte streiten und der
Start deshalb nicht in dreissig Sekunden durch ist. Guardian sieht dort einen
Server, der nicht rechtzeitig hochkommt, startet ihn neu, sieht es wieder, und
nach drei Anlaeufen steht er in Quarantaene — obwohl nichts kaputt ist ausser
der Erwartung.

Bisher gab es dagegen genau zwei Werkzeuge, und beide sind zu grob: die
Blueprint fuer **alle** Server dieses Spiels aendern, oder den Server auf eine
abgeleitete Blueprint umhaengen — was `switch_server_blueprint` als
Neuinstallation ausfuehrt, samt `wipe_server_root`. Fuer eine zu knapp
bemessene Startzeit die Welt zu loeschen ist keine Behebung.

Deshalb diese Spalte: eine Handvoll Zahlen, die `compile_guardian_config`
**nach** der Ableitung aus der Blueprint darueberlegt. Umkehrbar (NULL heisst
"wieder wie in der Blueprint"), sichtbar im Guardian-Reiter, und auf eine
geschlossene Menge von Skalaren begrenzt.

Bewusst **eine JSON-Spalte und nicht zwoelf Zahlenspalten**. Zwoelf Spalten
waeren zwoelf Migrationen, sobald eine dreizehnte Stellschraube dazukommt, und
jede davon muesste `NULL` als "nicht gesetzt" tragen — dieselbe Optionalitaet,
nur teurer. Die geschlossene Menge steht ohnehin im Code
(`GUARDIAN_STELLSCHRAUBEN`), und der Compiler wirft alles weg, was nicht darin
vorkommt: eine Zeile, die jemand von Hand mit Unsinn fuellt, kann den Agenten
damit nicht erreichen.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260816_04"
down_revision: Union[str, None] = "20260816_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("servers") as batch:
        batch.add_column(sa.Column("guardian_overrides_json", sa.Text(), nullable=True))


def downgrade() -> None:
    # Die Uebersteuerungen gehen dabei verloren, und das ist richtig so: ohne
    # die Spalte gibt es nichts mehr, was sie anwenden koennte, und ein Server
    # mit einer unsichtbar gespeicherten Sonderregel waere schlimmer als einer
    # ohne. Nach dem Rueckbau gilt wieder die Blueprint — der Zustand, den die
    # Anlage vor dieser Migration hatte.
    with op.batch_alter_table("servers") as batch:
        batch.drop_column("guardian_overrides_json")
