"""Add client_uuid to e2ee_blind_envelopes for replay deduplication.

Revision ID: 20260907_07
Revises: 20260907_06
Create Date: 2026-09-10
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_07"
down_revision: Union[str, None] = "20260907_06"
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
    existing_uniques = {uq["name"] for uq in inspector.get_unique_constraints("e2ee_blind_envelopes")}

    with op.batch_alter_table("e2ee_blind_envelopes") as batch_op:
        if "client_uuid" not in columns:
            batch_op.add_column(sa.Column("client_uuid", sa.String(length=64), nullable=True))
        if "ix_e2ee_blind_envelopes_client_uuid" not in existing_indexes:
            batch_op.create_index("ix_e2ee_blind_envelopes_client_uuid", ["client_uuid"], unique=False)
        if "ix_e2ee_envelope_mailbox_uuid" not in existing_indexes:
            batch_op.create_index("ix_e2ee_envelope_mailbox_uuid", ["blind_mailbox_id", "client_uuid"], unique=False)
        if "uq_e2ee_envelope_mailbox_client_uuid" not in existing_uniques:
            batch_op.create_unique_constraint("uq_e2ee_envelope_mailbox_client_uuid", ["blind_mailbox_id", "client_uuid"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = set(inspector.get_table_names())
    if "e2ee_blind_envelopes" not in tables:
        return

    columns = {col["name"] for col in inspector.get_columns("e2ee_blind_envelopes")}
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("e2ee_blind_envelopes")}
    existing_uniques = {uq["name"] for uq in inspector.get_unique_constraints("e2ee_blind_envelopes")}

    with op.batch_alter_table("e2ee_blind_envelopes") as batch_op:
        if "uq_e2ee_envelope_mailbox_client_uuid" in existing_uniques:
            batch_op.drop_constraint("uq_e2ee_envelope_mailbox_client_uuid", type_="unique")
        if "ix_e2ee_envelope_mailbox_uuid" in existing_indexes:
            batch_op.drop_index("ix_e2ee_envelope_mailbox_uuid")
        if "ix_e2ee_blind_envelopes_client_uuid" in existing_indexes:
            batch_op.drop_index("ix_e2ee_blind_envelopes_client_uuid")
        if "client_uuid" in columns:
            batch_op.drop_column("client_uuid")
