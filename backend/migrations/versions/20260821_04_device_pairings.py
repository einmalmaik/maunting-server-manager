"""Kopplungscodes fuer das Smart System, und die Herkunft an der Sitzung.

Revision ID: 20260821_04
Revises: 20260821_03
Create Date: 2026-08-21

Zwei zusammengehoerige Aenderungen:

``device_pairings`` traegt die kurzlebigen Einladungen. Gespeichert wird nur
der SHA-256 des Codes — der Klartext existiert einmal, in der Antwort, die ihn
erzeugt hat.

``refresh_tokens.geraet`` haelt fest, ob eine Sitzung von einem gekoppelten
Geraet stammt. Vorher erklaerte der Client seine Herkunft in jedem Request
selbst; damit war "aus dem Panel erreicht nichts den Rechner" eine
Absichtserklaerung. Steht sie am Token, ist sie eine Schranke. Die Spalte muss
die Rotation ueberleben, deshalb haengt sie an der Zeile und nicht nur am JWT.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_04"
down_revision: Union[str, None] = "20260821_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_pairings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("label", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("family", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_pairings_code_hash", "device_pairings", ["code_hash"])
    op.create_index("ix_device_pairings_family", "device_pairings", ["family"])
    op.create_index(
        "ix_device_pairings_user_expires", "device_pairings", ["user_id", "expires_at"]
    )

    # Nullable ohne Vorgabewert: bestehende Sitzungen sind Browser-Sitzungen,
    # und `NULL` heisst hier genau das — "kein Geraet", also Panel.
    op.add_column("refresh_tokens", sa.Column("geraet", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("refresh_tokens", "geraet")
    op.drop_index("ix_device_pairings_user_expires", table_name="device_pairings")
    op.drop_index("ix_device_pairings_family", table_name="device_pairings")
    op.drop_index("ix_device_pairings_code_hash", table_name="device_pairings")
    op.drop_table("device_pairings")
