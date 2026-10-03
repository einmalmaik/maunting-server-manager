"""Zugang der Kamera-Sicherung.

``vault_sicherungszugaenge`` haelt je Geraetesitzung einen eng gefassten
Zugang fuer die Sicherung ohne offene App: Token nur als Hash, Konto und
Sitzungsfamilie nur als HMAC.

Revision ID: 20261003_02
Revises: 20261003_01
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_02"
down_revision: Union[str, None] = "20261003_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "vault_sicherungszugaenge" not in _tabellen():
        op.create_table(
            "vault_sicherungszugaenge",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("konto_index", sa.String(64), nullable=False),
            sa.Column("familie_index", sa.String(64), nullable=False),
            sa.Column("bucket_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_vault_sicherungszugaenge_konto_index", "vault_sicherungszugaenge", ["konto_index"])
        op.create_index("ix_vault_sicherungszugaenge_bucket_id", "vault_sicherungszugaenge", ["bucket_id"])


def downgrade() -> None:
    if "vault_sicherungszugaenge" in _tabellen():
        op.drop_index("ix_vault_sicherungszugaenge_bucket_id", table_name="vault_sicherungszugaenge")
        op.drop_index("ix_vault_sicherungszugaenge_konto_index", table_name="vault_sicherungszugaenge")
        op.drop_table("vault_sicherungszugaenge")
