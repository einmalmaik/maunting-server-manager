"""Team-Memory, an den Besitzer gebundene Verschluesselung, Audit-Ziel als Text.

Revision ID: 20260809_02
Revises: 20260809_01
Create Date: 2026-08-09

Drei Aenderungen, die zusammengehoeren, weil sie alle am Gedaechtnis haengen.

**1. Der Scope `team`.** Bisher waren alle Eintraege persoenlich — selbst die
serverbezogenen trugen die Benutzer-ID in ihrer Kennung. Zwei Kollegen, die
denselben Server verwalten, teilten also nichts. `team_id` verweist auf das
besitzende Team; `owner_user_id` bleibt bei Team-Eintraegen bewusst leer, damit
das Wissen bestehen bleibt, wenn der Kollege geht, der es aufgeschrieben hat.

**2. `aad_version`.** Die Zusatzdaten der Verschluesselung lauteten bisher
`msm:ai:memory:{id}` — gebunden an die Zeile, nicht an ihren Besitzer. Wer
Schreibzugriff auf die Datenbank hat, haette `owner_user_id` umschreiben und
fremde Notizen uebernehmen koennen. Version 2 nimmt die Scope-Kennung mit auf:
danach macht dasselbe Umschreiben den Eintrag **unlesbar**, statt ihn
umzuhaengen.

Bestandszeilen bleiben auf Version 1 und werden beim naechsten Schreibzugriff
angehoben. Eine Neuverschluesselung an dieser Stelle haette den DIS-Sidecar
vorausgesetzt — eine Migration, die an einem HTTP-Aufruf scheitern kann, ist
keine.

**3. `audit_logs.target_id` wird Text.** Seit Phase C uebergeben Memory,
Skills und Anhaenge UUIDs an `record_privileged_action`, die Spalte war aber
`INTEGER`. SQLite nimmt das klaglos an, PostgreSQL nicht: dort scheiterte
**jeder** `remember`-Aufruf am Audit-Eintrag. Der Fehler war in den Tests
unsichtbar, weil sie auf SQLite laufen. Bestehende Zahlen werden zu ihrer
Textdarstellung — `42` wird `"42"`, die Filter im Admin-Log vergleichen
entsprechend als Text.

Die Rueckrichtung ist nicht verlustfrei moeglich und sagt das ausdruecklich:
eine UUID hat keine Integerentsprechung. Das Downgrade behaelt deshalb nur, was
sich als Zahl lesen laesst, und setzt alles andere auf NULL — sonst waere der
Rueckbau des gesamten Branches nach dem ersten `remember`-Aufruf dauerhaft
versperrt.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260809_02"
down_revision: Union[str, None] = "20260809_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_SCOPES = "scope IN ('user', 'server', 'panel')"
_NEW_SCOPES = "scope IN ('user', 'server', 'team', 'panel')"


def upgrade() -> None:
    # Ein einziger Batch-Block: auf SQLite wird die Tabelle dabei genau einmal
    # neu aufgebaut, samt ersetztem CHECK. Zwei getrennte Bloecke wuerden sie
    # zweimal kopieren und den Constraint-Tausch unnoetig verwickeln.
    with op.batch_alter_table("ai_memory_entries") as batch:
        batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("aad_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.drop_constraint("ck_ai_memory_entries_scope", type_="check")
        batch.create_check_constraint("ck_ai_memory_entries_scope", _NEW_SCOPES)
        batch.create_foreign_key(
            "fk_ai_memory_entries_team", "teams", ["team_id"], ["id"], ondelete="CASCADE"
        )
    op.create_index("ix_ai_memory_team", "ai_memory_entries", ["team_id"])

    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "target_id",
            existing_type=sa.Integer(),
            type_=sa.String(length=64),
            existing_nullable=True,
            # PostgreSQL wandelt Integer nicht selbsttaetig in Text um; ohne
            # diesen Hinweis bricht die Migration mit "cannot be cast
            # automatically" ab.
            postgresql_using="target_id::text",
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "target_id",
            existing_type=sa.String(length=64),
            type_=sa.Integer(),
            existing_nullable=True,
            # Ein glatter Cast ist hier unmoeglich: sobald die KI einmal etwas
            # gemerkt hat, steht in der Spalte eine UUID, und PostgreSQL bricht
            # mit "invalid input syntax for type integer" ab — ausgerechnet als
            # **erste** Anweisung des Downgrades, wodurch die ganze Kette an
            # dieser Revision haengen bleibt. Auf SQLite ist das unsichtbar,
            # weil `postgresql_using` dort nie angewandt wird.
            #
            # Was sich nicht zurueckwandeln laesst, wird deshalb NULL. Der
            # Verlust ist unvermeidlich (eine UUID hat keine
            # Integerentsprechung) und trifft nur das Ziel des Audit-Eintrags,
            # nicht den Eintrag selbst — dieselbe Entscheidung wie unten fuer
            # die Team-Eintraege des Gedaechtnisses.
            #
            # Die Laengenschranke {1,9} ist kein Schoenheitsfehler: eine
            # 20-stellige Ziffernfolge wuerde den Cast mit "integer out of
            # range" zum Scheitern bringen und damit genau das wiederholen, was
            # diese Zeile verhindern soll.
            postgresql_using=(
                "(CASE WHEN target_id ~ '^-?[0-9]{1,9}$' "
                "THEN target_id::integer ELSE NULL END)"
            ),
        )

    op.drop_index("ix_ai_memory_team", table_name="ai_memory_entries")
    # Team-Eintraege haetten nach dem Rueckbau keinen gueltigen Scope mehr und
    # wuerden den wiederhergestellten CHECK verletzen.
    op.execute("DELETE FROM ai_memory_entries WHERE scope = 'team'")
    with op.batch_alter_table("ai_memory_entries") as batch:
        # Der Fremdschluessel wird bewusst nicht einzeln entfernt: SQLite
        # speichert keine Constraint-Namen fuer Fremdschluessel und kann ihn
        # deshalb nicht zurueckliefern — `drop_constraint` scheiterte hier mit
        # "No such constraint". Das Loeschen der Spalte nimmt ihn auf beiden
        # Datenbanken ohnehin mit.
        batch.drop_constraint("ck_ai_memory_entries_scope", type_="check")
        batch.create_check_constraint("ck_ai_memory_entries_scope", _OLD_SCOPES)
        batch.drop_column("aad_version")
        batch.drop_column("team_id")
