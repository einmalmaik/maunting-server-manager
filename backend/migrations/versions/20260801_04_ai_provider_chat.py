"""Add AI providers, user credentials, conversations, and messages.

Revision ID: 20260801_04
Revises: 20260801_03
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260801_04"
down_revision: Union[str, None] = "20260801_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("default_model", sa.String(length=256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("requires_api_key", sa.Boolean(), nullable=False),
        sa.Column("allow_private_network", sa.Boolean(), nullable=False),
        sa.Column("operator_api_key_encrypted", sa.String(length=4096), nullable=True),
        sa.Column("operator_api_key_hint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_ai_providers_id", "ai_providers", ["id"], unique=False)

    with op.batch_alter_table("ai_usage_events") as batch_op:
        batch_op.add_column(sa.Column("provider_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("model", sa.String(length=256), nullable=True))
        batch_op.create_foreign_key(
            "fk_ai_usage_events_provider_id_ai_providers",
            "ai_providers",
            ["provider_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_ai_usage_events_provider_id", ["provider_id"], unique=False)

    op.create_table(
        "ai_user_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("api_key_encrypted", sa.String(length=4096), nullable=False),
        sa.Column("api_key_hint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "provider_id", name="uq_ai_user_credentials_user_provider"
        ),
    )
    op.create_index(
        "ix_ai_user_credentials_user_id", "ai_user_credentials", ["user_id"], unique=False
    )
    op.create_index(
        "ix_ai_user_credentials_provider_id",
        "ai_user_credentials",
        ["provider_id"],
        unique=False,
    )

    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_conversations_user_id", "ai_conversations", ["user_id"], unique=False)
    op.create_index("ix_ai_conversations_server_id", "ai_conversations", ["server_id"], unique=False)
    op.create_index(
        "ix_ai_conversations_user_updated",
        "ai_conversations",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_conversations_server_updated",
        "ai_conversations",
        ["server_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_ai_messages_role"),
        sa.CheckConstraint(
            "status IN ('complete', 'streaming', 'failed')", name="ck_ai_messages_status"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_ai_messages_conversation_id", "ai_messages", ["conversation_id"], unique=False
    )
    op.create_index(
        "ix_ai_messages_conversation_created",
        "ai_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_messages_conversation_created", table_name="ai_messages")
    op.drop_index("ix_ai_messages_conversation_id", table_name="ai_messages")
    op.drop_table("ai_messages")
    # Bedingt: `20260808_02` entfernt diesen Index wieder, und das Modell fuehrt
    # ihn seitdem nicht mehr. Eine Installation, deren Schema aus den Modellen
    # erzeugt und anschliessend gestempelt wurde, hat ihn deshalb nie besessen —
    # ein bedingungsloses DROP wuerde ihr Downgrade abbrechen.
    if "ix_ai_conversations_server_updated" in {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("ai_conversations")
    }:
        op.drop_index("ix_ai_conversations_server_updated", table_name="ai_conversations")
    op.drop_index("ix_ai_conversations_user_updated", table_name="ai_conversations")
    op.drop_index("ix_ai_conversations_server_id", table_name="ai_conversations")
    op.drop_index("ix_ai_conversations_user_id", table_name="ai_conversations")
    op.drop_table("ai_conversations")
    op.drop_index("ix_ai_user_credentials_provider_id", table_name="ai_user_credentials")
    op.drop_index("ix_ai_user_credentials_user_id", table_name="ai_user_credentials")
    op.drop_table("ai_user_credentials")
    with op.batch_alter_table("ai_usage_events") as batch_op:
        batch_op.drop_index("ix_ai_usage_events_provider_id")
        batch_op.drop_constraint(
            "fk_ai_usage_events_provider_id_ai_providers", type_="foreignkey"
        )
        batch_op.drop_column("model")
        batch_op.drop_column("provider_id")
    op.drop_index("ix_ai_providers_id", table_name="ai_providers")
    op.drop_table("ai_providers")
