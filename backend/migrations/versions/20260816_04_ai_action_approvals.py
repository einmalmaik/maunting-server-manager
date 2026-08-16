"""Eine Freigabe, die per E-Mail erteilt wird.

Revision ID: 20260816_04
Revises: 20260816_03
Create Date: 2026-08-16

Bis hierher endete ein unbeaufsichtigter Lauf an dem Punkt, an dem einer seiner
Vorschlaege eine Bestaetigung brauchte: alle offenen Vorschlaege wurden
zurueckgenommen, der Lauf beendet, und die Mail sagte "konnte nicht behoben
werden". Das ist richtig, solange niemand antworten kann — und falsch, sobald
eine Adresse hinterlegt ist. Der Betreiber ist im Urlaub, nicht abwesend.

Diese Tabelle traegt genau ein kurzlebiges Einmal-Token je wartendem Vorschlag.
Der Klartext steht nur in der Mail, gespeichert wird sein SHA-256 — die Form von
`login_challenges`, und aus demselben Grund.

Der Bereich bleibt eng: gefragt wird nur nach dem, was der autonome Modus
ohnehin nie ohne Klick tut (`ALWAYS_CONFIRM_TOOLS`), und nur unter einer
erteilten Autonomie-Freigabe. Ohne sie aendert diese Tabelle nichts.
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
    op.create_table(
        "ai_action_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["ai_action_proposals.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('approved', 'rejected')",
            name="ck_ai_action_approvals_decision",
        ),
    )
    # **Die Namen sind die des Modells, nicht schoenere eigene.**
    #
    # `index=True` an einer Spalte erzeugt `ix_<tabelle>_<spalte>`. Wer hier
    # anders benennt, bekommt zwei Schemata, die dasselbe koennen und
    # verschieden heissen: eine Testdatenbank aus `create_all` traegt die
    # Modellnamen, eine echte Datenbank aus der Migrationskette die eigenen —
    # und der Rueckbau faellt auf der einen mit "no such index" um. Genau das
    # ist hier passiert.
    #
    # Eindeutigkeit als **Index** und nicht als Constraint: auf SQLite laesst
    # sich ein benannter UNIQUE-Constraint nicht ohne Tabellenneubau entfernen,
    # ein Index schon.
    op.create_index(
        "ix_ai_action_approvals_token_hash",
        "ai_action_approvals",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_ai_action_approvals_proposal_id", "ai_action_approvals", ["proposal_id"]
    )
    op.create_index("ix_ai_action_approvals_user_id", "ai_action_approvals", ["user_id"])
    op.create_index("ix_ai_action_approvals_expires", "ai_action_approvals", ["expires_at"])


def downgrade() -> None:
    # Offene Freigaben gehen verloren. Das ist die richtige Richtung: ohne die
    # Tabelle kann kein Link mehr eingeloest werden, und ein Vorschlag, der auf
    # eine Zustimmung wartet, die nie eintreffen kann, laeuft in seine Frist und
    # wird zurueckgenommen — genau wie vor dieser Migration.
    op.drop_index("ix_ai_action_approvals_expires", table_name="ai_action_approvals")
    op.drop_index("ix_ai_action_approvals_user_id", table_name="ai_action_approvals")
    op.drop_index("ix_ai_action_approvals_proposal_id", table_name="ai_action_approvals")
    op.drop_index("ix_ai_action_approvals_token_hash", table_name="ai_action_approvals")
    op.drop_table("ai_action_approvals")
