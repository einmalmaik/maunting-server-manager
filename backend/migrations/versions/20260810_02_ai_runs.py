"""Der Zug der KI wird ein eigenstaendiges, dauerhaftes Ding.

Revision ID: 20260810_02
Revises: 20260810_01
Create Date: 2026-08-10

Ein Zug der KI war bisher ein Python-Generator an einem HTTP-Request. Er lebte
genau so lange wie die Verbindung des Browsers. Drei Beschwerden aus dem Betrieb
haben dieselbe Ursache:

1. "Wenn ich den Chat verlasse oder den Browser schliesse, bricht die Anfrage
   ab." — Der Generator bekommt ein ``GeneratorExit``, die halbfertige Antwort
   wird als ``failed`` abgerechnet.
2. "Die KI arbeitet nach dem Bestaetigen nicht weiter, man muss eine neue
   Nachricht schreiben." — Die Bestaetigung laeuft ueber einen eigenen
   Endpunkt. Es gibt niemanden mehr, den sie aufwecken koennte.
3. "Die KI soll Aufgaben von Anfang bis Ende durchfuehren." — Unmoeglich,
   solange jede Unterbrechung das Ende ist.

``ai_runs`` haelt den Lauf fest, ``state_json`` sein Arbeitsgedaechtnis: die
vollstaendige Nachrichtenliste an den Anbieter samt Budgetzaehlern. Genau das,
was vorher nur in einer lokalen Variablen stand.

``ai_action_proposals.run_id`` ist die Gegenrichtung: der Bestaetigungsknopf muss
wissen, **wen** er aufweckt. ``correlation_id`` konnte das nicht — sie ist die
``request_id`` eines Segments, und zwei Schreibrunden desselben Zuges teilten sie
sich.

Portabel gehalten (``batch_alter_table``, keine Servervorgaben): die Testsuite
laeuft auf SQLite, der Betrieb auf PostgreSQL.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_02"
down_revision: Union[str, None] = "20260810_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("state_json", sa.Text(), nullable=True),
        sa.Column("reasoning", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('running', 'waiting_confirmation', 'waiting_user', "
            "'completed', 'failed', 'cancelled')",
            name="ck_ai_runs_status",
        ),
    )
    op.create_index("ix_ai_runs_user_id", "ai_runs", ["user_id"])
    op.create_index("ix_ai_runs_conversation_id", "ai_runs", ["conversation_id"])
    op.create_index("ix_ai_runs_user_status", "ai_runs", ["user_id", "status"])
    op.create_index(
        "ix_ai_runs_conversation_created", "ai_runs", ["conversation_id", "created_at"]
    )

    with op.batch_alter_table("ai_action_proposals") as batch:
        batch.add_column(sa.Column("run_id", sa.String(length=36), nullable=True))
    op.create_index("ix_ai_action_proposals_run_id", "ai_action_proposals", ["run_id"])
    # Der Fremdschluessel wird bewusst **nicht** nachtraeglich per ALTER gesetzt:
    # SQLite kann das nicht ohne Tabellenkopie, und `batch_alter_table` wuerde
    # dafuer die gesamte Vorschlagstabelle umschreiben. Die Beziehung wird im
    # ORM gefuehrt (models/ai_action_proposal.py) und beim Aufraeumen eines
    # Laufes im Dienst behandelt; ein verwaister Verweis weckt schlicht
    # niemanden auf.


def downgrade() -> None:
    op.drop_index("ix_ai_action_proposals_run_id", table_name="ai_action_proposals")
    with op.batch_alter_table("ai_action_proposals") as batch:
        batch.drop_column("run_id")

    op.drop_index("ix_ai_runs_conversation_created", table_name="ai_runs")
    op.drop_index("ix_ai_runs_user_status", table_name="ai_runs")
    op.drop_index("ix_ai_runs_conversation_id", table_name="ai_runs")
    op.drop_index("ix_ai_runs_user_id", table_name="ai_runs")
    op.drop_table("ai_runs")
