"""Add chat_media table for secure E2EE chat attachments.

Revision ID: 20260907_06
Revises: 20260907_05
Create Date: 2026-09-10
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_06"
down_revision: Union[str, None] = "20260907_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names()
    if "chat_media" not in tables:
        op.create_table(
            "chat_media",
            sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column("blind_mailbox_id", sa.String(length=64), nullable=False),
            sa.Column("direct_chat_id", sa.Integer(), nullable=True),
            sa.Column("group_id", sa.Integer(), nullable=True),
            sa.Column("uploader_user_id", sa.Integer(), nullable=False),
            sa.Column("ciphertext_blob", sa.Text(), nullable=False),
            sa.Column("media_type", sa.String(length=64), nullable=False, server_default="application/octet-stream"),
            sa.Column("file_name", sa.String(length=256), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["uploader_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["direct_chat_id"], ["direct_chats.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["group_id"], ["chat_groups.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_media_id", "chat_media", ["id"], unique=False)
        op.create_index("ix_chat_media_blind_mailbox_id", "chat_media", ["blind_mailbox_id"], unique=False)
        op.create_index("ix_chat_media_direct_chat_id", "chat_media", ["direct_chat_id"], unique=False)
        op.create_index("ix_chat_media_group_id", "chat_media", ["group_id"], unique=False)
        op.create_index("ix_chat_media_uploader_user_id", "chat_media", ["uploader_user_id"], unique=False)
        op.create_index("ix_chat_media_created_at", "chat_media", ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names()
    if "chat_media" in tables:
        op.drop_index("ix_chat_media_created_at", table_name="chat_media")
        op.drop_index("ix_chat_media_uploader_user_id", table_name="chat_media")
        op.drop_index("ix_chat_media_group_id", table_name="chat_media")
        op.drop_index("ix_chat_media_direct_chat_id", table_name="chat_media")
        op.drop_index("ix_chat_media_blind_mailbox_id", table_name="chat_media")
        op.drop_index("ix_chat_media_id", table_name="chat_media")
        op.drop_table("chat_media")
