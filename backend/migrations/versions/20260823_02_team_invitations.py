"""Eine Mitgliedschaft entsteht nicht mehr ohne den Betroffenen.

Revision ID: 20260823_02
Revises: 20260823_01
Create Date: 2026-08-23

Bis heute trug ``add_member`` einen beliebigen Benutzer direkt in
``team_members`` ein. Wer ``teams.create`` hatte, konnte damit jeden anderen
still in sein Team ziehen — und das öffnet mehr als eine Mitgliederliste: das
Team-Wissen des Gründers fließt ab diesem Moment in jeden KI-Lauf des
Hinzugefügten, und was dessen KI für das Team lernt, landet im Team des
Fremden und ist dort im Klartext lesbar.

Die offene Einladung bekommt deshalb eine **eigene Tabelle** statt eines
Zustandsfeldes an ``team_members``. Der Grund ist Vorsicht, nicht Ordnung:
Mitgliedschaft wird an vielen Stellen abgefragt — mehrfach als direkter Join
auf ``team_members`` in ``permission_service``, dazu jede Nutzung von
``team_service.membership``. Ein Zustandsfeld müsste an jeder dieser Stellen
mitgeprüft werden, und die erste vergessene wäre genau das Loch, das hier
geschlossen werden soll. Ohne Zeile in ``team_members`` ist jede bestehende
Abfrage ohne Zutun richtig.

Bestehende Mitgliedschaften bleiben unberührt. Sie rückwirkend in Einladungen
zu verwandeln hieße, gewachsene Teams über Nacht aufzulösen; die neue Regel
gilt ab hier.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_02"
down_revision: Union[str, None] = "20260823_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "team_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "team_id",
            sa.Integer(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # CASCADE wie bei der Mitgliedschaft: ein gelöschtes Konto hat über
        # nichts mehr zu entscheiden.
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "can_manage_skills", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "can_manage_memory", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # SET NULL: verschwindet der Einladende, bleibt die Einladung gültig —
        # sie gehört dem Team, nicht ihm.
        sa.Column(
            "invited_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_invitations_team_user"),
    )
    op.create_index("ix_team_invitations_team_id", "team_invitations", ["team_id"])
    # Der Eingeladene fragt "wer will mich haben" — das ist der Lesepfad.
    op.create_index("ix_team_invitations_user", "team_invitations", ["user_id"])


def downgrade() -> None:
    # Offene Einladungen verschwinden mit der Tabelle. Sie in Mitgliedschaften
    # zu verwandeln wäre das Gegenteil dessen, wofür sie angelegt wurden.
    op.drop_index("ix_team_invitations_user", table_name="team_invitations")
    op.drop_index("ix_team_invitations_team_id", table_name="team_invitations")
    op.drop_table("team_invitations")
