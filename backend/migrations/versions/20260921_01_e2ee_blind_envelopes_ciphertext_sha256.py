"""Add ciphertext_sha256 to e2ee_blind_envelopes for indexed replay prevention.

Revision ID: 20260921_01
Revises: 20260918_01
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260921_01"
down_revision: Union[str, None] = "20260918_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = set(inspector.get_table_names())
    if "e2ee_blind_envelopes" not in tables:
        return

    columns = {col["name"] for col in inspector.get_columns("e2ee_blind_envelopes")}
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("e2ee_blind_envelopes")}

    with op.batch_alter_table("e2ee_blind_envelopes") as batch_op:
        if "ciphertext_sha256" not in columns:
            batch_op.add_column(sa.Column("ciphertext_sha256", sa.String(length=64), nullable=True))
        if "ix_e2ee_blind_envelopes_ciphertext_sha256" not in existing_indexes:
            batch_op.create_index("ix_e2ee_blind_envelopes_ciphertext_sha256", ["ciphertext_sha256"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = set(inspector.get_table_names())
    if "e2ee_blind_envelopes" not in tables:
        return

    columns = {col["name"] for col in inspector.get_columns("e2ee_blind_envelopes")}
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("e2ee_blind_envelopes")}

    with op.batch_alter_table("e2ee_blind_envelopes") as batch_op:
        if "ix_e2ee_blind_envelopes_ciphertext_sha256" in existing_indexes:
            batch_op.drop_index("ix_e2ee_blind_envelopes_ciphertext_sha256")
        if "ciphertext_sha256" in columns:
            batch_op.drop_column("ciphertext_sha256")
