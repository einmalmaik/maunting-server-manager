"""Add encrypted AI action proposals and confirmation state.

Revision ID: 20260801_05
Revises: 20260801_04
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260801_05"
down_revision: Union[str, None] = "20260801_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_action_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("preview_json", sa.Text(), nullable=False),
        sa.Column("expected_revision", sa.String(length=80), nullable=True),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confirmation_token_hash", sa.String(length=64), nullable=True),
        sa.Column("confirmation_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'executing', 'succeeded', 'failed', 'expired')",
            name="ck_ai_action_proposals_status",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_action_proposals_user_id", "ai_action_proposals", ["user_id"])
    op.create_index("ix_ai_action_proposals_server_id", "ai_action_proposals", ["server_id"])
    op.create_index("ix_ai_action_proposals_status", "ai_action_proposals", ["status"])
    op.create_index("ix_ai_action_proposals_correlation_id", "ai_action_proposals", ["correlation_id"])
    op.create_index(
        "ix_ai_action_proposals_user_created",
        "ai_action_proposals",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_ai_action_proposals_conversation_created",
        "ai_action_proposals",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_action_proposals_conversation_created", table_name="ai_action_proposals")
    op.drop_index("ix_ai_action_proposals_user_created", table_name="ai_action_proposals")
    op.drop_index("ix_ai_action_proposals_correlation_id", table_name="ai_action_proposals")
    op.drop_index("ix_ai_action_proposals_status", table_name="ai_action_proposals")
    op.drop_index("ix_ai_action_proposals_server_id", table_name="ai_action_proposals")
    op.drop_index("ix_ai_action_proposals_user_id", table_name="ai_action_proposals")
    op.drop_table("ai_action_proposals")
