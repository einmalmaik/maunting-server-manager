"""Posteingang des Tresors.

``vault_eingang`` haelt Datensaetze der Kamera-Sicherung, die ein Geraet bei
gesperrtem Tresor ablegt. Ohne Kontobezug, nur Bucket und Chiffrat.

Revision ID: 20261003_01
Revises: 20261001_02
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_01"
down_revision: Union[str, None] = "20261001_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "vault_eingang" not in _tabellen():
        op.create_table(
            "vault_eingang",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("bucket_id", sa.String(64), nullable=False),
            sa.Column("ciphertext", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_vault_eingang_bucket_id", "vault_eingang", ["bucket_id"])


def downgrade() -> None:
    if "vault_eingang" in _tabellen():
        op.drop_index("ix_vault_eingang_bucket_id", table_name="vault_eingang")
        op.drop_table("vault_eingang")
