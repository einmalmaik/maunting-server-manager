"""Der Guardian bekommt ein eigenes Fenster.

Revision ID: 20260816_01
Revises: 20260815_01
Create Date: 2026-08-16

Bisher gab es genau eine Unterhaltung je Benutzer, erzwungen ueber den
eindeutigen Index ``uq_ai_conversations_user`` (angelegt in ``20260808_02``).
Das war richtig, solange es nur einen Anlass gab: ein Mensch tippt. Seit
Guardian die KI weckt, gibt es einen zweiten — und beide standen in derselben
Zeile.

Die Wirkung im Betrieb war keine Unordnung, sondern ein Ausfall. Eine Heilung
startete gar nicht, solange der Benutzer irgendetwas laufen hatte
(``ai_guardian_service.heilungslauf_starten`` bricht bei ``aktiver_lauf`` ab),
und schrieb der Mensch waehrend einer Heilung, loeste ``vorgaenger_abloesen``
den Reparaturlauf ab und brach dessen asyncio-Aufgabe wirklich ab. Wer nachts
einen Server repariert bekommen wollte, durfte tagsueber nicht mit der KI reden.

Deshalb ``kind`` und ein Index ueber ``(user_id, kind)``: genau eine Zeile je
Benutzer **und Anlass**. Es ist ausdruecklich **keine** Ablage mit beliebig
vielen Chats — die Zusage aus ``20260808_02`` gilt unveraendert weiter, sie
zaehlt nur nicht mehr Zeilen, sondern Anlaesse.

**Index statt Constraint**, aus demselben Grund wie damals: SQLite kann eine
Unique-Constraint nicht nachtraeglich anlegen, einen Index dagegen schon, und
die Wirkung ist dieselbe.

**Der CHECK wird hier ausgeschrieben und nicht aus ``ARTEN`` importiert.** Eine
angewandte Migration ist Geschichte; wuerde sie das Tupel aus dem Modell lesen,
schriebe eine spaetere Erweiterung rueckwirkend um, was diese Migration getan
hat. Dasselbe Verfahren wie beim Zustands-CHECK in ``ai_runs``.

Bestandszeilen bekommen ``primary``. Das ist nicht bloss der bequeme Default,
sondern die Wahrheit: jede vorhandene Zeile ist der Dauerchat eines Menschen,
und der Vorfallverlauf, der bis heute darin steht, bleibt genau dort stehen. Es
wird nichts umgehaengt — was ein Betreiber gestern in seinem Chat gelesen hat,
soll morgen nicht in einem anderen Fenster liegen.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260816_01"
down_revision: Union[str, None] = "20260815_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ARTEN = "kind IN ('primary', 'guardian')"


def upgrade() -> None:
    connection = op.get_bind()

    # Ein einziger Batch-Block: auf SQLite wird die Tabelle dabei genau einmal
    # neu aufgebaut, samt Spalte und CHECK.
    with op.batch_alter_table("ai_conversations") as batch:
        batch.add_column(
            sa.Column(
                "kind",
                sa.String(length=16),
                nullable=False,
                server_default="primary",
            )
        )
        batch.create_check_constraint("ck_ai_conversations_kind", _ARTEN)

    _drop_index_if_present(connection, "uq_ai_conversations_user")
    op.create_index(
        "uq_ai_conversations_user_kind",
        "ai_conversations",
        ["user_id", "kind"],
        unique=True,
    )


def _drop_index_if_present(connection, name: str) -> None:
    """Entfernt einen Index nur, wenn er tatsaechlich existiert.

    Neue Installationen erzeugen ihr Schema aus den Modellen und werden danach
    gestempelt. Der alte Index steht dort gar nicht mehr, weil das Modell ihn
    nicht mehr fuehrt — ein bedingungsloses DROP wuerde deren Migrationslauf
    abbrechen, obwohl nichts zu tun ist. Woertlich uebernommen aus
    ``20260808_02``, das denselben Fall fuer den Vorgaengerindex hatte.
    """
    existing = {
        index["name"]
        for index in sa.inspect(connection).get_indexes("ai_conversations")
    }
    if name in existing:
        op.drop_index(name, table_name="ai_conversations")


def downgrade() -> None:
    # Erst raeumen, dann verengen: zurueck geht es nur mit einer Zeile je
    # Benutzer, und die Guardian-Zeilen haetten danach keinen Platz mehr. Ihre
    # Nachrichten, Laeufe, Vorschlaege und Anhaenge haengen per ON DELETE CASCADE
    # daran und gehen mit — das ist der Preis eines Downgrades und der Grund,
    # warum es hier ausdruecklich steht statt in einer Fussnote.
    op.execute("DELETE FROM ai_conversations WHERE kind <> 'primary'")

    op.drop_index("uq_ai_conversations_user_kind", table_name="ai_conversations")
    op.create_index(
        "uq_ai_conversations_user", "ai_conversations", ["user_id"], unique=True
    )
    with op.batch_alter_table("ai_conversations") as batch:
        batch.drop_constraint("ck_ai_conversations_kind", type_="check")
        batch.drop_column("kind")
