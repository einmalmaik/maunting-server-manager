"""Einführung der schlanken FIFO-Queue für E2EE-Geräte.

Revision ID: 20261024_02
Revises: 20261024_01
Create Date: 2026-10-24
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261024_02"
down_revision: Union[str, None] = "20261024_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "e2ee_device_queue" in tables:
        existing_indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("e2ee_device_queue")}
        if "ix_e2ee_queue_target_device" not in existing_indexes:
            op.create_index("ix_e2ee_queue_target_device", "e2ee_device_queue", ["target_device_id"])
        if "ix_e2ee_queue_client_uuid" not in existing_indexes:
            op.create_index("ix_e2ee_queue_client_uuid", "e2ee_device_queue", ["client_uuid"])
        if "ix_e2ee_queue_chat" not in existing_indexes:
            op.create_index("ix_e2ee_queue_chat", "e2ee_device_queue", ["chat_id"])
        return

    op.create_table(
        "e2ee_device_queue",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("target_device_id", sa.Integer(), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), nullable=False),
        sa.Column("sender_device_id", sa.Integer(), nullable=True),
        sa.Column("chat_type", sa.String(length=16), nullable=False),
        sa.Column("chat_id", sa.String(length=64), nullable=False),
        sa.Column("client_uuid", sa.String(length=64), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_device_id"], ["user_e2ee_devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_e2ee_queue_target_device", "e2ee_device_queue", ["target_device_id"])
    op.create_index("ix_e2ee_queue_client_uuid", "e2ee_device_queue", ["client_uuid"])
    op.create_index("ix_e2ee_queue_chat", "e2ee_device_queue", ["chat_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "e2ee_device_queue" in tables:
        existing_indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("e2ee_device_queue")}
        if "ix_e2ee_queue_chat" in existing_indexes:
            op.drop_index("ix_e2ee_queue_chat", table_name="e2ee_device_queue")
        if "ix_e2ee_queue_client_uuid" in existing_indexes:
            op.drop_index("ix_e2ee_queue_client_uuid", table_name="e2ee_device_queue")
        if "ix_e2ee_queue_target_device" in existing_indexes:
            op.drop_index("ix_e2ee_queue_target_device", table_name="e2ee_device_queue")
        op.drop_table("e2ee_device_queue")
