"""Add AI provisioning proposals, persisted tool results and autonomy grants.

Revision ID: 20260808_01
Revises: 20260807_03
Create Date: 2026-08-08

Zielpunkt 1 (3.1), Zielpunkt 3.3 und Zielpunkt 3.7 des v4-Zielbilds.

Drei Aenderungen, die zusammengehoeren:

- **`ai_action_proposals.server_id` wird optional.** Die KI soll einen Server
  *erstellen* koennen (`propose_server_create`). Zum Zeitpunkt des Vorschlags
  existiert dieser Server noch nicht — eine NOT-NULL-Spalte haette den Fall
  ausgeschlossen. Nach der Ausfuehrung traegt der Vorschlag die neue Server-ID.

- **`ai_tool_results`.** Read-Ergebnisse wurden bisher nur in einer lokalen
  Liste gehalten und nach dem Stream verworfen. Eine Rueckfrage im selben Chat
  sah den gerade gelesenen Log nicht mehr und musste ihn neu lesen — doppelte
  Kosten und, schlimmer, ein Modell das ohne die Daten antwortet, die es
  gerade selbst geholt hat.

- **`ai_autonomy_grants`.** Die Berechtigung `ai.autonomous.use` existierte, war
  aber an keiner Stelle im Code abgefragt. Sie allein genuegt auch nicht: eine
  Freigabe muss pro Server (oder ausdruecklich panelweit) erteilt werden und ein
  Stundenbudget haben. Ohne Grant bleibt jeder Vorschlag bestaetigungspflichtig.

Was sich **nicht** aendert: die Rechtepruefung. Autonomie entfernt die
menschliche Bestaetigung, niemals eine Permission. `requires_confirmation` bleibt
deshalb eine eigene Spalte neben `autonomous`.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260808_01"
down_revision: Union[str, None] = "20260807_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_action_proposals") as batch:
        batch.alter_column(
            "server_id", existing_type=sa.Integer(), nullable=True
        )
        # Trennt "wurde bestaetigt" von "lief autonom". Ohne eigene Spalte
        # waere im Audit nicht mehr unterscheidbar, ob ein Mensch zugestimmt hat.
        batch.add_column(
            sa.Column(
                "autonomous",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        # Zielpunkt 3.6 verlangt, dass ein Vorschlag zeigt, *warum* geaendert
        # wird und *welche Folgen* erwartet werden. Bisher gab es dafuer kein
        # Feld; die Begruendung stand bestenfalls im freien Chattext daneben.
        batch.add_column(sa.Column("reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("expected_effect", sa.Text(), nullable=True))

    op.create_table(
        "ai_tool_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        # Redigiertes JSON. Bewusst nicht DIS-verschluesselt: der Inhalt hat
        # dieselbe Vertraulichkeit wie die Chatnachricht daneben und wurde
        # bereits durch `redact_sensitive_text` gefuehrt.
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_tool_results_conversation_created",
        "ai_tool_results",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "ai_autonomy_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # NULL = panelweit. Bewusst nullable statt einer zweiten Tabelle: die
        # Aufloesung ist "Server-Grant vor Panel-Grant" und bleibt damit eine
        # einzige Abfrage.
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "max_actions_per_hour",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "max_actions_per_hour >= 0 AND max_actions_per_hour <= 1000",
            name="ck_ai_autonomy_grants_budget",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "server_id", name="uq_ai_autonomy_grants_scope"),
    )
    op.create_index("ix_ai_autonomy_grants_user_id", "ai_autonomy_grants", ["user_id"])
    # Das Stundenbudget zaehlt autonome Vorschlaege der letzten Stunde. Ohne
    # diesen Index waere das bei jeder autonomen Aktion ein Full Scan.
    op.create_index(
        "ix_ai_action_proposals_autonomous_created",
        "ai_action_proposals",
        ["user_id", "autonomous", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_action_proposals_autonomous_created", table_name="ai_action_proposals"
    )
    op.drop_index("ix_ai_autonomy_grants_user_id", table_name="ai_autonomy_grants")
    op.drop_table("ai_autonomy_grants")
    op.drop_index(
        "ix_ai_tool_results_conversation_created", table_name="ai_tool_results"
    )
    op.drop_table("ai_tool_results")
    with op.batch_alter_table("ai_action_proposals") as batch:
        batch.drop_column("expected_effect")
        batch.drop_column("reason")
        batch.drop_column("autonomous")
        # Ein Rueckbau auf NOT NULL ist nur moeglich, wenn keine Zeile ohne
        # Server existiert. Der Aufrufer muss das vorher sicherstellen.
        batch.alter_column(
            "server_id", existing_type=sa.Integer(), nullable=False
        )
