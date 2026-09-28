"""Die Gruppe verraet ihren Namen nicht mehr

Stufe 6. `chat_groups.name`, `description` und `avatar_url` standen seit jeher
im Klartext: der Server kannte den Namen jeder Gruppe, ihre Beschreibung und
den Pfad zu ihrem Logo, und den Logopfad lieferte er sogar ohne Anmeldung aus.

Das war kein Versehen, sondern eine Voraussetzung — die Vorschau eines
Einladungslinks kam daher. Seit Stufe 3f traegt die **verschluesselte
Einladungskarte** diese Vorschau, und seit dieser Migration ist der Klartext
nicht mehr noetig.

Die Spalten werden nicht bloss ignoriert, sondern **geleert**. Eine ignorierte
Spalte ist ein voller Datenspeicher mit einem Zettel davor; wer die Datenbank
in die Hand bekommt, liest sie trotzdem. Und `name` wird nullable: eine neue
Gruppe legt dort gar nichts mehr ab.

*Was das kostet, und es gehoert ausgesprochen:* Der Name einer bestehenden
Gruppe ist danach serverseitig weg. Die Clients haben ihn — im oertlichen,
versiegelten Namensspeicher und im verschluesselten Gruppenblock — und
schreiben ihn beim naechsten Oeffnen in den Block. Ein Geraet, das die Gruppe
noch nie gesehen hat und keinen Gruppenschluessel besitzt, sieht dort
„Verschluesselte Gruppe". Das ist dieselbe Zusage wie bei den Nachrichten
selbst, nur eine Ebene hoeher.

Revision ID: 20260923_03
Revises: 20260923_02
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_03"
down_revision = "20260923_02"
branch_labels = None
depends_on = None


def _spalte(tabelle: str, spalte: str) -> dict | None:
    bind = op.get_bind()
    pruefer = sa.inspect(bind)
    if tabelle not in pruefer.get_table_names():
        return None
    for s in pruefer.get_columns(tabelle):
        if s["name"] == spalte:
            return s
    return None


def upgrade() -> None:
    if _spalte("chat_groups", "name") is None:
        return

    # Erst nullable, dann leeren — andersherum scheitert das UPDATE an der
    # NOT-NULL-Bedingung, und die Migration bliebe auf halbem Weg stehen.
    #
    # `batch_alter_table`, nicht `alter_column`: SQLite kennt kein
    # `ALTER COLUMN` und braucht den Umbau ueber eine Zwischentabelle. Auf
    # Postgres faellt derselbe Aufruf auf ein schlichtes ALTER zurueck. Der
    # Unterschied faellt sonst erst in `test_schema_constraints.py` auf, das
    # den ganzen Migrationsweg auf einer SQLite-Datei nachfaehrt.
    with op.batch_alter_table("chat_groups") as stapel:
        stapel.alter_column(
            "name",
            existing_type=sa.String(length=64),
            nullable=True,
        )
    op.execute(
        sa.text(
            "UPDATE chat_groups SET name = NULL, description = NULL, avatar_url = NULL"
        )
    )


def downgrade() -> None:
    """Zurueck geht nur die Form, nicht der Inhalt.

    Die Namen sind weg und kommen aus keiner Datenbank zurueck — sie liegen
    verschluesselt bei den Mitgliedern. Damit die NOT-NULL-Bedingung wieder
    haelt, bekommen namenlose Gruppen einen Platzhalter; alles andere waere
    eine Migration, die sich nicht zuruecknehmen laesst.
    """
    if _spalte("chat_groups", "name") is None:
        return

    op.execute(
        sa.text("UPDATE chat_groups SET name = 'Gruppe ' || id WHERE name IS NULL")
    )
    with op.batch_alter_table("chat_groups") as stapel:
        stapel.alter_column(
            "name",
            existing_type=sa.String(length=64),
            nullable=False,
        )
