"""Dauerhafter Kanal-Ausweis für Benachrichtigungen bei geschlossener App.

Ein Ausweis je Gerät, an die Sitzung gebunden, widerrufbar. Gespeichert ist nur
sein SHA-256. Der Zugangstoken des Panels lebt 15 Minuten und taugt für einen
Vordergrunddienst nicht; dieser Ausweis hat keine Rotation und gilt, bis er
widerrufen wird.

Revision ID: 20261023_01
Revises: 20261011_01
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261023_01"
down_revision: Union[str, None] = "20261011_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # Datenbanken aus `create_all` kennen die Tabelle schon; dann gibt es
    # nichts zu tun.
    if "notification_channels" in _tabellen():
        return

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("familie", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_notification_channels_token"),
    )
    op.create_index(
        "ix_notification_channels_user", "notification_channels", ["user_id"]
    )
    op.create_index(
        "ix_notification_channels_familie", "notification_channels", ["familie"]
    )


def downgrade() -> None:
    if "notification_channels" not in _tabellen():
        return
    # Index vor der Tabelle: ein Drop der Tabelle nimmt ihn zwar mit, aber der
    # umgekehrte Weg über die Spalte scheitert auf PostgreSQL.
    op.drop_index("ix_notification_channels_familie", table_name="notification_channels")
    op.drop_index("ix_notification_channels_user", table_name="notification_channels")
    op.drop_table("notification_channels")
