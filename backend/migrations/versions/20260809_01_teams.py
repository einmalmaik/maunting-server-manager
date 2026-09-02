"""Teams als Traeger von geteiltem KI-Wissen und gebuendelten Serverrechten.

Revision ID: 20260809_01
Revises: 20260808_04
Create Date: 2026-08-09

Ein Team ist zweierlei: der Ort, an dem die KI Wissen fuer mehrere Menschen
ablegen darf, und ein Buendel von Serverrechten, das ein Gruender an seine
Mitglieder weiterreicht.

**Warum `personal_for_user_id` statt eines `is_personal`-Flags.** Die Spalte
traegt die Unterscheidung und die Eindeutigkeit zugleich: gesetzt heisst
"Ein-Mann-Team dieses Benutzers", und die UNIQUE-Bedingung sorgt dafuer, dass
es davon genau eines gibt. NULL heisst "echtes Team" — und weil NULL-Werte in
einer UNIQUE-Bedingung nicht kollidieren (PostgreSQL wie SQLite), darf derselbe
Benutzer beliebig viele echte Teams gruenden. Ein separates Flag haette zwei
Wahrheiten fuer dieselbe Aussage geschaffen, die auseinanderlaufen koennen.

**Warum `team_server_grants` keine Rechte gewaehrt.** Die Tabelle haelt nur den
*Wunsch* des Gruenders fest. Ob eine Zeile wirkt, entscheidet
`permission_service` bei jedem Zugriff neu, indem es nachsieht, ob der Gruender
den Key auf diesem Server **direkt** haelt. Damit ist Rechteausweitung
unmoeglich, und ein Rechteentzug beim Gruender wirkt sofort, ohne dass hier
etwas aufgeraeumt werden muss.

Keine Datenwanderung: alle drei Tabellen sind neu und starten leer. Persoenliche
Teams entstehen erst, wenn ein Benutzer sie das erste Mal braucht.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260809_01"
down_revision: Union[str, None] = "20260808_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        # RESTRICT: der Gruender ist der Bezugspunkt der Rechte-Obergrenze.
        # Verschwindet er ersatzlos, waere nicht mehr entscheidbar, was das
        # Team weitergeben darf — also muss es vorher aufgeloest werden.
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "personal_for_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("personal_for_user_id", name="uq_teams_personal_for_user"),
    )
    op.create_index("ix_teams_owner", "teams", ["owner_user_id"])

    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        # Zwei Schalter statt eines zweiten Rollensystems: fuer die Frage, wer
        # das gemeinsame Wissen aendern darf, genuegen zwei Ja/Nein-Angaben.
        sa.Column(
            "can_manage_skills", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "can_manage_memory", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "added_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_team_members_role"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.create_index("ix_team_members_user", "team_members", ["user_id"])

    op.create_table(
        "team_server_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "server_id", sa.Integer(), sa.ForeignKey("servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("permission_key", sa.String(length=64), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "granted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "team_id", "server_id", "permission_key", name="uq_team_server_grants"
        ),
    )
    op.create_index("ix_team_server_grants_team_id", "team_server_grants", ["team_id"])
    # Die Rechtepruefung fragt genau diese Kombination ab. Ohne den Index waere
    # jeder gescheiterte Direktzugriff ein voller Tabellenscan.
    op.create_index(
        "ix_team_server_grants_lookup", "team_server_grants", ["server_id", "permission_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_team_server_grants_lookup", table_name="team_server_grants")
    op.drop_index("ix_team_server_grants_team_id", table_name="team_server_grants")
    op.drop_table("team_server_grants")
    op.drop_index("ix_team_members_user", table_name="team_members")
    op.drop_index("ix_team_members_team_id", table_name="team_members")
    op.drop_table("team_members")
    op.drop_index("ix_teams_owner", table_name="teams")
    op.drop_table("teams")
