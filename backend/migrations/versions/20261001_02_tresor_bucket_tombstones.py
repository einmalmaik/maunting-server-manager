"""Beerdigte Tresor-Buckets.

``vault_bucket_tombstones`` merkt sich Buckets, die zurueckgesetzt oder mit
ihrem Konto geloescht wurden. Ohne Kontobezug, nur die Kennung.

Revision ID: 20261001_02
Revises: 20261001_01
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261001_02"
down_revision: Union[str, None] = "20261001_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "vault_bucket_tombstones" not in _tabellen():
        op.create_table(
            "vault_bucket_tombstones",
            sa.Column("bucket_id", sa.String(64), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    if "vault_bucket_tombstones" in _tabellen():
        op.drop_table("vault_bucket_tombstones")
