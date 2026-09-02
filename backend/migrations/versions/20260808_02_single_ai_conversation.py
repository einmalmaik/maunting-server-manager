"""Merge every user's AI conversations into a single persistent chat.

Revision ID: 20260808_02
Revises: 20260808_01
Create Date: 2026-08-08

Der KI-Assistent bekommt genau **eine** Unterhaltung je Benutzer.

Warum die bisherige Aufteilung weg muss: es gab globale Chats und je Server
einen eigenen. Damit hing der Serverbezug an der *Unterhaltung* — jedes
Werkzeug las ihn aus `conversation.server_id`. Ein Benutzer, der im Panel-Chat
"mein Minecraft-Server ist nicht erreichbar" schrieb, bekam deshalb kein
einziges Server-Werkzeug angeboten; er musste erst wissen, welcher Server
gemeint ist, dorthin navigieren und dort einen zweiten Chat anfangen. Genau
umgekehrt zur Erwartung an einen Assistenten.

Ab jetzt traegt der *Werkzeugaufruf* die Server-ID und die Unterhaltung ist
kontextfrei. Das macht mehrere Unterhaltungen ueberfluessig.

Die Migration ist bewusst verlustfrei:

- Je Benutzer wird die zuletzt aktualisierte Unterhaltung zur primaeren.
- Nachrichten, Vorschlaege, Anhaenge und Werkzeugergebnisse der uebrigen
  Unterhaltungen werden dorthin umgehaengt. Die Reihenfolge bleibt erhalten,
  weil sie ueber `created_at` und nicht ueber die Unterhaltung definiert ist.
- Erst danach werden die leeren Unterhaltungen geloescht.
- `server_id` wird auf NULL gesetzt. Bereits ausgefuehrte Vorschlaege behalten
  ihre eigene `server_id` und bleiben damit im Audit nachvollziehbar.

Zusaetzlich zwei neue Spalten:

- `ai_messages.reasoning` — die Denkschritte des Modells. Getrennt von
  `content`, weil sie etwas anderes sind: eine Nebenausgabe, die der Benutzer
  aufklappen kann. Wuerden sie in `content` landen, waeren sie von der Antwort
  nicht mehr unterscheidbar und wuerden in jede Folgeanfrage zurueckfliessen.
- `ai_conversations.summarized_until` — Marke fuer die spaetere automatische
  Kontextkompression (Phase C). Sie wird hier bereits angelegt, damit der eine
  lange Chat nicht zweimal migriert werden muss.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260808_02"
down_revision: Union[str, None] = "20260808_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Alle Tabellen, die auf eine Unterhaltung zeigen. Sie muessen *vor* dem
# Loeschen umgehaengt werden — ein ON DELETE CASCADE wuerde sie sonst
# stillschweigend mit entfernen.
_CHILD_TABLES = (
    "ai_messages",
    "ai_action_proposals",
    "ai_attachments",
    "ai_tool_results",
)


def upgrade() -> None:
    with op.batch_alter_table("ai_messages") as batch:
        batch.add_column(sa.Column("reasoning", sa.Text(), nullable=True))
    with op.batch_alter_table("ai_conversations") as batch:
        batch.add_column(
            sa.Column("summarized_until", sa.DateTime(timezone=True), nullable=True)
        )

    connection = op.get_bind()

    # Primaere Unterhaltung je Benutzer: die zuletzt benutzte. Der Tiebreaker
    # ueber die ID haelt das Ergebnis deterministisch, auch wenn zwei Zeilen
    # denselben Zeitstempel tragen.
    #
    # Die Auswahl laeuft bewusst in Python statt ueber `DISTINCT ON`: das ist
    # PostgreSQL-only, und die Migrationstests dieses Projekts laufen gegen
    # SQLite. Die Datenmenge ist eine Handvoll Zeilen je Benutzer.
    rows = connection.execute(
        sa.text(
            "SELECT user_id, id FROM ai_conversations "
            "ORDER BY user_id ASC, updated_at DESC, id DESC"
        )
    ).fetchall()
    primaries: dict[int, str] = {}
    for user_id, conversation_id in rows:
        primaries.setdefault(user_id, conversation_id)

    for user_id, primary_id in primaries.items():
        for table in _CHILD_TABLES:
            connection.execute(
                sa.text(
                    f"""
                    UPDATE {table} SET conversation_id = :primary_id
                    WHERE conversation_id IN (
                        SELECT id FROM ai_conversations
                        WHERE user_id = :user_id AND id <> :primary_id
                    )
                    """
                ),
                {"primary_id": primary_id, "user_id": user_id},
            )
        connection.execute(
            sa.text(
                "DELETE FROM ai_conversations WHERE user_id = :user_id AND id <> :primary_id"
            ),
            {"primary_id": primary_id, "user_id": user_id},
        )

    connection.execute(
        sa.text("UPDATE ai_conversations SET server_id = NULL WHERE server_id IS NOT NULL")
    )

    # Ab jetzt strukturell erzwungen statt nur im Service zugesichert. Ein
    # eindeutiger Index statt einer Unique-Constraint: SQLite kann eine
    # Constraint nicht nachtraeglich anlegen, einen Index dagegen schon, und die
    # Wirkung ist dieselbe.
    _drop_index_if_present(connection, "ix_ai_conversations_server_updated")
    op.create_index(
        "uq_ai_conversations_user", "ai_conversations", ["user_id"], unique=True
    )


def _drop_index_if_present(connection, name: str) -> None:
    """Entfernt einen Index nur, wenn er tatsaechlich existiert.

    Neue Installationen erzeugen ihr Schema aus den Modellen und werden danach
    gestempelt. Der Index steht dort gar nicht mehr, weil das Modell ihn nicht
    mehr fuehrt — ein bedingungsloses DROP wuerde deren Migrationslauf
    abbrechen, obwohl nichts zu tun ist.
    """
    existing = {
        index["name"]
        for index in sa.inspect(connection).get_indexes("ai_conversations")
    }
    if name in existing:
        op.drop_index(name, table_name="ai_conversations")


def downgrade() -> None:
    op.drop_index("uq_ai_conversations_user", table_name="ai_conversations")
    op.create_index(
        "ix_ai_conversations_server_updated",
        "ai_conversations",
        ["server_id", "updated_at"],
    )
    with op.batch_alter_table("ai_conversations") as batch:
        batch.drop_column("summarized_until")
    with op.batch_alter_table("ai_messages") as batch:
        batch.drop_column("reasoning")
